import logging
import re
from dataclasses import dataclass
from typing import Optional, List, Tuple

logger = logging.getLogger(__name__)

# These match automated files that contain zero logic evaluation value but burn token costs.
SKIP_FILENAME_PATTERNS: Tuple[str, ...] = (
    "package-lock.json", "yarn.lock", "poetry.lock",
    "Pipfile.lock", "composer.lock", "Gemfile.lock",
    "migrations/", ".min.js", ".min.css",
    "dist/", "build/", "__pycache__/", ".pyc",
    ".svg", ".png", ".jpg", ".jpeg", ".gif", ".ico",
    ".pdf", ".woff", ".ttf", ".eot",
    ".terraform/", ".github/workflows/",
)

# Automated testing files are skipped here to isolate reviews to production source logic.
SKIP_TEST_PATTERNS: Tuple[str, ...] = (
    "test_", "_test.py", ".test.js", ".spec.js",
    ".test.ts", ".spec.ts", "tests/",
)

# System line-count configuration threshold
MAX_CHANGED_LINES = 300  


@dataclass
class SanitizedChunk:
    filename:    str
    language:    str
    patch:       str
    additions:   int
    deletions:   int
    skipped:     bool
    skip_reason: Optional[str] = None
    is_large:    bool = False   


def sanitize_pr_files(pr_files: list[dict]) -> list[SanitizedChunk]:
    """
    INGESTION ENGINE: Evaluates incoming repository file dictionaries. Filter items 
    to drop data noise and intercepts large files before they reach LLM prompts.
    """
    chunks = []

    for f in pr_files:
        filename  = f.get("filename", "")
        status    = f.get("status", "")       
        patch     = f.get("patch", "") or ""  
        additions = f.get("additions", 0)
        deletions = f.get("deletions", 0)

        # STEP 2: Intercept Deleted/Purged Paths
        # Files that are deleted don't exist in the head branch; evaluating them is redundant.
        if status in ("removed", "deleted"):
            logger.info(f"SKIP  {filename} — deleted file")
            chunks.append(SanitizedChunk(
                filename=filename, language="", patch="",
                additions=0, deletions=deletions,
                skipped=True, skip_reason="File deleted"
            ))
            continue

        # STEP 3: Intercept Empty or Missing Diff Strings
        # This protects the app if files were renamed or permission-modified without content changes.
        if not patch:
            logger.info(f"SKIP  {filename} — binary or empty diff")
            chunks.append(SanitizedChunk(
                filename=filename, language="", patch="",
                additions=additions, deletions=deletions,
                skipped=True, skip_reason="Binary or empty file"
            ))
            continue

        # STEP 4: Match Ecosystem and Noise Patterns
        if any(p in filename for p in SKIP_FILENAME_PATTERNS):
            logger.info(f"SKIP  {filename} — generated/vendor noise")
            chunks.append(SanitizedChunk(
                filename=filename, language="", patch="",
                additions=additions, deletions=deletions,
                skipped=True, skip_reason="Generated or vendor file"
            ))
            continue

        # STEP 5: Match Testing Asset Bypasses
        if any(p in filename for p in SKIP_TEST_PATTERNS):
            logger.info(f"SKIP  {filename} — test asset bypass")
            chunks.append(SanitizedChunk(
                filename=filename, language="", patch="",
                additions=additions, deletions=deletions,
                skipped=True, skip_reason="Test file"
            ))
            continue

        # STEP 6: Line Volume Threshold Enforcement
        # Count lines starting with '+' or '-' to get true logical line additions/deletions.
        patch_lines = patch.splitlines()
        changed_lines = sum(1 for l in patch_lines if l.startswith("+") or l.startswith("-"))

        is_large = False
        if changed_lines > MAX_CHANGED_LINES:
            logger.warning(f"LARGE {filename} — {changed_lines} changed lines. Safe parsing truncation activated.")
            # Trigger grammar-preserving hunk splitting instead of arbitrary text clipping
            patch = _safe_hunk_truncator(patch, MAX_CHANGED_LINES)
            is_large = True

        language = _detect_language(filename)
        logger.info(f"OK    {filename} ({language}) +{additions} -{deletions}")

        chunks.append(SanitizedChunk(
            filename=filename,
            language=language,
            patch=patch,
            additions=additions,
            deletions=deletions,
            skipped=False,
            is_large=is_large,
        ))

    return chunks


def _safe_hunk_truncator(patch: str, max_changed: int) -> str:
    """
    GRAMMAR TRUNCATOR: Uses regex to break diff code along structural hunk headers (@@)
    instead of raw string clipping, keeping patches well-formed for LLM processing.
    """
    # Regex split that isolates and keeps the structural @@ hunk markers intact
    hunk_segments = re.split(r"(@@\s+-\d+,\d+\s+\+\d+,\d+\s+@@)", patch)
    if len(hunk_segments) <= 1:
        return "\n".join(patch.splitlines()[:max_changed])

    result_payload = []
    accumulated_changes = 0
    
    # Store initial repository info header blocks if present before first hunk
    if hunk_segments[0].strip():
        result_payload.append(hunk_segments[0])

    # Reconstruct segments: odd indices are headers, even indices are hunk body contents
    for idx in range(1, len(hunk_segments), 2):
        header = hunk_segments[idx]
        body = hunk_segments[idx + 1] if (idx + 1) < len(hunk_segments) else ""
        
        body_lines = body.splitlines()
        hunk_changes = sum(1 for l in body_lines if l.startswith("+") or l.startswith("-"))

        # If the entire hunk fits under the maximum limit, include it completely
        if accumulated_changes + hunk_changes <= max_changed:
            result_payload.append(f"{header}\n{body}")
            accumulated_changes += hunk_changes
        else:
            # If the hunk overflows, add partial lines line-by-line until hitting max_changed
            partial_body = []
            for line in body_lines:
                if line.startswith("+") or line.startswith("-"):
                    if accumulated_changes >= max_changed:
                        break
                    accumulated_changes += 1
                partial_body.append(line)
                
            result_payload.append(f"{header}\n" + "\n".join(partial_body))
            result_payload.append(f"+# ... Truncated at safe hunk boundary index ({max_changed} lines max).")
            break

    return "\n".join(result_payload)


EXTENSION_MAP = {
    ".py": "python", 
    ".js": "javascript", 
    ".ts": "typescript",
    ".jsx": "javascript", 
    ".tsx": "typescript", 
    ".java": "java",
    ".cpp": "cpp", 
    ".c": "c", 
    ".go": "go", 
    ".rb": "ruby",
    ".rs": "rust", 
    ".cs": "csharp", 
    ".php": "php",
}

def _detect_language(filename: str) -> str:
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return EXTENSION_MAP.get(ext, "unknown")