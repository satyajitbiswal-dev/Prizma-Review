import time
import jwt
import requests
import logging
from typing import List, Dict, Any
from django.conf import settings

logger = logging.getLogger(__name__)

# Configured at the settings layer for scalability
SKIP_PATTERNS = getattr(settings, "PR_REVIEW_SKIP_PATTERNS", (
    "package-lock.json", "yarn.lock", "poetry.lock", "Pipfile.lock",
    "migrations/", ".min.js", ".min.css", "dist/", "build/",
    "__pycache__/", ".pyc",
))

MAX_LINES_PER_FILE = getattr(settings, "PR_REVIEW_MAX_LINES_PER_FILE", 500)


def should_skip_file(filename: str) -> bool:
    return any(pattern in filename for pattern in SKIP_PATTERNS)

# ── 1. Generate JWT for GitHub App ────────────────────────────────────────

def get_app_jwt() -> str:
    """GitHub App authentication — short-lived JWT (10 min max)."""
    
    key_path = getattr(settings, "GITHUB_PRIVATE_KEY_PATH", None)
    if not key_path:
        logger.critical("PRODUCTION CONFIG ERROR: GITHUB_PRIVATE_KEY_PATH is not defined in settings/environment!")
        raise ValueError("Missing GITHUB_PRIVATE_KEY_PATH configuration.")

    # Production Fix: Read pristine cryptographic bytes directly from the absolute path location
    try:
        with open(key_path, "r") as f:
            private_key = f.read()
    except FileNotFoundError:
        logger.critical(f"Crypto Core Failure: PEM file could not be found at path: {key_path}")
        raise FileNotFoundError(f"PEM file missing at {key_path}")
    except Exception as e:
        logger.critical(f"Crypto Core Failure: Unable to read PEM file at {key_path}. Error: {e}")
        raise

    now = int(time.time())
    payload = {
        "iat": now - 60,       # clock skew buffer
        "exp": now + (9 * 60), # 9 minutes max
        "iss": settings.GITHUB_APP_ID,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")

# ── 2. Exchange JWT for installation access token ─────────────────────────

def get_installation_token(installation_id: int) -> str:
    """Exchanges the App JWT for a localized installation token."""
    app_jwt = get_app_jwt()
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
        },
        timeout=10, # Production-essential network timeout protection
    )
    resp.raise_for_status()
    return resp.json()["token"]


# ── 3. Fetch PR diff and chunk by file ────────────────────────────────────
def fetch_pr_diff_chunks(repo_full_name: str, pr_number: int, installation_id: int) -> List[Dict[str, Any]]:
    """
    Fetches all PR files using cursor pagination, filtering out binary,
    vendor, or oversized file streams gracefully.
    """
    token = get_installation_token(installation_id)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }

    chunks = []
    # Production Fix: Use cursor pagination to capture all files across large PRs
    url = f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}/files?per_page=100"
    
    while url:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        files = resp.json()

        for f in files:
            filename = f.get("filename", "unknown_file")
            patch = f.get("patch", "")
            additions = f.get("additions", 0)
            deletions = f.get("deletions", 0)
            changes = additions + deletions

            if should_skip_file(filename):
                logger.debug(f"Skipping tracked asset: {filename} (matched exclusion pattern)")
                continue

            # Production Fix: Handle large files where GitHub omits or truncates the patch string (Important to do ***)
            if changes > MAX_LINES_PER_FILE or not patch:
                skip_reason = f"Too large ({changes} lines)" if changes > MAX_LINES_PER_FILE else "Empty or truncated patch payload"
                chunks.append({
                    "filename": filename,
                    "language": detect_language(filename),
                    "patch": "",
                    "additions": additions,
                    "deletions": deletions,
                    "skipped": True,
                    "skip_reason": skip_reason,
                })
                continue

            chunks.append({
                "filename": filename,
                "language": detect_language(filename),
                "patch": patch,
                "additions": additions,
                "deletions": deletions,
                "skipped": False,
            })

        # Check for GitHub's Pagination 'Link' header to advance the cursor loop
        link_header = resp.headers.get("Link", "")
        url = None
        if link_header:
            # Parse links to check if a 'next' page relation pointer exists
            links = link_header.split(",")
            for link in links:
                if 'rel="next"' in link:
                    url = link.split(";")[0].strip("<> ")
                    break

    logger.info(f"Successfully processed {len(chunks)} structural file chunks for {repo_full_name}#{pr_number}")
    return chunks


# ── 4. Language detection from file extension ─────────────────────────────

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
}

def detect_language(filename: str) -> str:
    if "." not in filename:
        return "unknown"
    ext = "." + filename.rsplit(".", 1)[-1].lower()
    return EXTENSION_MAP.get(ext, "unknown")