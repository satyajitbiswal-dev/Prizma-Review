import re
import logging
import requests
from typing import Any
from github_client.gh_client import get_installation_token
from .diff_sanitizer import MAX_CHANGED_LINES

logger = logging.getLogger(__name__)


def build_position_map(patch: str) -> dict[int, int]:
    """
    HUNK PARSER: Tracks code modifications line-by-line. 
    Maps the physical line number to the specific index location within the raw diff patch.
    """
    position_map = {}
    position = 0       # Tracks position in the diff patch (Required by GitHub API)
    current_line = 0   # Tracks line number in the modified source file

    for line in patch.splitlines():
        if line.startswith("@@"):
            # Extract target line start coordinate numbers from the unified diff header
            match = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if match:
                current_line = int(match.group(1)) - 1  
            position += 1  

        elif line.startswith("+"):
            current_line += 1
            position += 1
            position_map[current_line] = position

        elif line.startswith("-"):
            position += 1  # Deletions move the diff position down but do not affect file line numbers

        else:
            current_line += 1
            position += 1
            position_map[current_line] = position

    return position_map


def _extract_field(obj, field: str, default: Any = "") -> Any:
    """
    HOISTED HELPER: Resolves unstructured fields from either class objects or 
    JSON dictionaries. Kept at module scope to avoid re-compilation in hot execution loops.
    """
    if hasattr(obj, field):
        return getattr(obj, field)
    if isinstance(obj, dict):
        return obj.get(field, default)
    return default


def post_github_review(repo_full_name: str, pr_number: int,
                       installation_id: int, head_sha: str,
                       comments: list[dict], health_score: int,
                       large_files: list[str] = None) -> bool:
    """
    ORCHESTRATION LAYER: Assembles inline anomalies and posts them back 
    to the active Pull Request as a single unified GitHub Review collection.
    """
    token = get_installation_token(installation_id)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # STEP 1: Fetch fresh patch state profiles directly from GitHub
    files_url = f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}/files"
    resp = requests.get(files_url, headers=headers)
    resp.raise_for_status()
    pr_files = resp.json()

    # STEP 2: Compile position mapping tables for files in this PR
    patch_map = {}  
    for f in pr_files:
        if f.get("patch"):
            patch_map[f["filename"]] = build_position_map(f["patch"])

    inline_comments = []
    severity_emoji_map = {"CRITICAL": "🔴", "WARNING": "🟡", "SUGGESTION": "🔵"}

    # STEP 3: Loop over issues and map line numbers to diff positions
    for comment in comments:
        filename = _extract_field(comment, "file_path") or _extract_field(comment, "file")
        line = _extract_field(comment, "line_start")
        severity = str(_extract_field(comment, "severity", "SUGGESTION")).upper()
        issue = _extract_field(comment, "issue")
        suggestion = _extract_field(comment, "suggestion")
        complexity_before = _extract_field(comment, "time_complexity_before") or _extract_field(comment, "complexity_before")
        complexity_after = _extract_field(comment, "time_complexity_after") or _extract_field(comment, "complexity_after")

        # Read map to find the required diff position index
        file_positions = patch_map.get(filename, {})
        position = file_positions.get(line)

        # STEP 4: Deflect Out-Of-Bounds Comments
        # If the line isn't part of the active code diff, skip it to prevent GitHub API errors.
        if position is None:
            logger.warning(f"No diff position found for {filename}:{line} — bypass comment mapping.")
            continue

        # Format markdown string for comment body layout
        severity_emoji = severity_emoji_map.get(severity, "⚪")
        category        = str(_extract_field(comment, "category", "DSA")).upper()
        category_label  = {"DSA": "DSA Issue", "SECURITY": "Security Issue",
                   "RESOURCE": "Reliability Issue"}.get(category, "Code Issue")
        body = f"{severity_emoji} **{severity} — {category_label}**\n\n"
        
        body += f"**Problem:** {issue}\n\n"
        body += f"**Fix:** {suggestion}\n"
        if complexity_before and complexity_after:
            body += f"\n> Complexity: `{complexity_before}` → `{complexity_after}`"

        inline_comments.append({
            "path": filename,
            "position": position,
            "body": body,
        })

    # STEP 5: Fallback to standalone issue summary if no lines match
    if not inline_comments:
        logger.warning("No inline comment tracking lines aligned to diff layouts.")
        _post_summary_comment(repo_full_name, pr_number, headers, [], health_score)
        return False

    # STEP 6: Post the complete unified review collection
    review_url = f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}/reviews"
    review_payload = {
        "commit_id": head_sha,
        "body": _build_summary_body(inline_comments, health_score, large_files),
        "event": "COMMENT",   
        "comments": inline_comments,
    }

    resp = requests.post(review_url, json=review_payload, headers=headers)
    resp.raise_for_status()
    return True


def _build_summary_body(comments: list[dict], health_score: int, large_files: list[str] = None) -> str:
    """Constructs the high-level summary overview markdown block."""
    critical   = sum(1 for c in comments if "CRITICAL"   in c["body"])
    warning    = sum(1 for c in comments if "WARNING"    in c["body"])
    suggestion = sum(1 for c in comments if "SUGGESTION" in c["body"])

    score_emoji = "🟢" if health_score >= 80 else "🟡" if health_score >= 50 else "🔴"

    body = f"""## 🤖 prizmareview — AI Code Review\n\n{score_emoji} **PR Health Score: {health_score}/100**\n\n| Severity | Count |\n|----------|-------|\n| 🔴 Critical | {critical} |\n| 🟡 Warning | {warning} |\n| 🔵 Suggestion | {suggestion} |\n"""
    
    # Inform users if files were truncated due to high volume limits
    if large_files:
        files_list = "\n".join(f"  - `{f}`" for f in large_files)
        body += f"\n> ⚠️ **Partial Review Added** — The following tracks exceeded {MAX_CHANGED_LINES} changed lines and were split safely at hunk markers:\n{files_list}\n"
        
    body += "\n\n*Powered by prizmareview — DSA-focused AI code review*"
    return body


def _post_summary_comment(repo_full_name, pr_number, headers, comments, health_score):
    """Fallback utility used when comments can't map to specific line modifications."""
    url = f"https://api.github.com/repos/{repo_full_name}/issues/{pr_number}/comments"
    body = _build_summary_body(comments, health_score)
    requests.post(url, json={"body": body}, headers=headers)