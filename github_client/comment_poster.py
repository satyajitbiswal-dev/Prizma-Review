import re
import logging
import requests

from github_client.gh_client import get_installation_token

logger = logging.getLogger(__name__)


# ── 1. Hunk Parser — line number → diff position ─────────────────────────
# This is the hardest part. GitHub Review API needs "position" (index in
# the unified diff) NOT the actual file line number. We build a lookup table.

def build_position_map(patch: str) -> dict[int, int]:
    """
    Parses a unified diff patch and returns:
    { actual_file_line_number: diff_position }

    diff_position increments for every line in the diff including
    hunk headers (@@ lines). GitHub counts from 1.
    """
    position_map = {}
    position = 0       # diff position counter (what GitHub wants)
    current_line = 0   # actual file line number (what Claude/Gemini gives us)

    for line in patch.splitlines():
        if line.startswith("@@"):
            # Parse @@ -old_start,old_count +new_start,new_count @@
            match = re.search(r"\+(\d+)(?:,(\d+))?", line)
            if match:
                current_line = int(match.group(1)) - 1  # -1 because we increment before use
            position += 1  # hunk header itself counts as a position

        elif line.startswith("+"):
            current_line += 1
            position += 1
            position_map[current_line] = position

        elif line.startswith("-"):
            position += 1  # removed line — has position but no new line number

        else:
            # Context line (unchanged)
            current_line += 1
            position += 1
            position_map[current_line] = position

    return position_map


# ── 2. Post GitHub Review with inline comments ────────────────────────────

def post_github_review(repo_full_name: str, pr_number: int,
                       installation_id: int, head_sha: str,
                       comments: list[dict], health_score: int) -> bool:
    """
    Posts a GitHub Pull Request Review with:
    - Inline comments on flagged lines
    - A summary comment at the top of the PR

    comments: list of Comment model instances (or dicts with same fields)
    """
    token = get_installation_token(installation_id)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # ── Fetch patches to build position maps ──────────────────────────────
    files_url = f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}/files"
    resp = requests.get(files_url, headers=headers)
    resp.raise_for_status()
    pr_files = resp.json()

    # Build position map for every file in the PR
    patch_map = {}  # { filename: { line_number: diff_position } }
    for f in pr_files:
        if f.get("patch"):
            patch_map[f["filename"]] = build_position_map(f["patch"])

    # ── Build inline comment payloads ──────────────────────────────────────
    inline_comments = []
    for comment in comments:
        # Abstract extraction helper supporting object and dict footprints safely
        def get_val(obj, field, default=""):
            if hasattr(obj, field):
                return getattr(obj, field)
            if isinstance(obj, dict):
                return obj.get(field, default)
            return default
        filename = get_val(comment, "file_path") or get_val(comment, "file")
        line = get_val(comment, "line_start")
        severity = str(get_val(comment, "severity", "SUGGESTION")).upper()
        issue = get_val(comment, "issue")
        suggestion = get_val(comment, "suggestion")
        complexity_before = get_val(comment, "time_complexity_before") or get_val(comment, "complexity_before")
        complexity_after = get_val(comment, "time_complexity_after") or get_val(comment, "complexity_after")

        # Get diff position for this line
        file_positions = patch_map.get(filename, {})
        position = file_positions.get(line)

        if position is None:
            logger.warning(f"No diff position found for {filename}:{line} — skipping")
            continue

        # Format the comment body
        severity_emoji = {"CRITICAL": "🔴", "WARNING": "🟡", "SUGGESTION": "🔵"}.get(severity, "⚪")
        body = f"{severity_emoji} **{severity} — DSA Issue**\n\n"
        body += f"**Problem:** {issue}\n\n"
        body += f"**Fix:** {suggestion}\n"
        if complexity_before and complexity_after:
            body += f"\n> Complexity: `{complexity_before}` → `{complexity_after}`"

        inline_comments.append({
            "path": filename,
            "position": position,
            "body": body,
        })

    if not inline_comments:
        logger.warning("No inline comments could be mapped to diff positions")
        # Still post summary comment
        _post_summary_comment(
            repo_full_name, pr_number, headers, [], health_score
        )
        return False

    # ── Post the Review ────────────────────────────────────────────────────
    review_url = f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}/reviews"
    review_payload = {
        "commit_id": head_sha,
        "body": _build_summary_body(inline_comments, health_score),
        "event": "COMMENT",   # COMMENT = no approve/reject, just comments
        "comments": inline_comments,
    }

    resp = requests.post(review_url, json=review_payload, headers=headers)
    resp.raise_for_status()

    logger.info(
        f"Posted review on {repo_full_name}#{pr_number} "
        f"with {len(inline_comments)} inline comments"
    )
    return True


# ── 3. Summary body (top of PR review) ───────────────────────────────────

def _build_summary_body(comments: list[dict], health_score: int) -> str:
    critical   = sum(1 for c in comments if "CRITICAL" in c["body"])
    warning    = sum(1 for c in comments if "WARNING"  in c["body"])
    suggestion = sum(1 for c in comments if "SUGGESTION" in c["body"])

    score_emoji = "🟢" if health_score >= 80 else "🟡" if health_score >= 50 else "🔴"

    return f"""## 🤖 PrizmReview — AI Code Review

{score_emoji} **PR Health Score: {health_score}/100**

| Severity | Count |
|----------|-------|
| 🔴 Critical | {critical} |
| 🟡 Warning | {warning} |
| 🔵 Suggestion | {suggestion} |

*Powered by PrizmReview — DSA-focused AI code review*
"""


def _post_summary_comment(repo_full_name, pr_number,
                           headers, comments, health_score):
    """Fallback — post just a top-level comment if no inline comments."""
    url = f"https://api.github.com/repos/{repo_full_name}/issues/{pr_number}/comments"
    body = _build_summary_body(comments, health_score)
    requests.post(url, json={"body": body}, headers=headers)