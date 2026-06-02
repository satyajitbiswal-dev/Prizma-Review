import time
import jwt
import requests
import logging
from typing import List, Dict, Any
from django.conf import settings
from .diff_sanitizer import sanitize_pr_files

logger = logging.getLogger(__name__)


def get_app_jwt() -> str:
    """Authenticates the GitHub App by creating a short-lived cryptographic JWT."""
    key_path = getattr(settings, "GITHUB_PRIVATE_KEY_PATH", None)
    if not key_path:
        raise ValueError("Missing GITHUB_PRIVATE_KEY_PATH configuration settings.")

    with open(key_path, "r") as f:
        private_key = f.read()

    now = int(time.time())
    payload = {
        "iat": now - 60,       
        "exp": now + (9 * 60), 
        "iss": settings.GITHUB_APP_ID,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


def get_installation_token(installation_id: int) -> str:
    """Exchanges the App JWT for an installation access token used in request headers."""
    app_jwt = get_app_jwt()
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()["token"]


def fetch_pr_diff_chunks(repo_full_name: str, pr_number: int, installation_id: int) -> List[Dict[str, Any]]:
    """
    PAGINATION ROUTINE: Iterates over the GitHub Files API. Uses an internal 
    lookup set to prevent double-tracking files across pagination page windows.
    """
    token = get_installation_token(installation_id)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }

    chunks = []
    # Initialize Idempotency Register Set
    # This prevents duplicates if a massive file overlaps between pagination indices.
    seen_filenames = set() 
    url = f"https://api.github.com/repos/{repo_full_name}/pulls/{pr_number}/files?per_page=100"
    
    while url:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        files = resp.json()

        # Process current page through the perimeter sanitizer
        sanitized = sanitize_pr_files(files)
        for chunk in sanitized:
            # Apply Deduplication Gate Check
            if chunk.filename in seen_filenames:
                logger.warning(f"Deduplicator: Bypassing duplicate paginated record block for '{chunk.filename}'")
                continue
                
            seen_filenames.add(chunk.filename)
            chunks.append({
                "filename":    chunk.filename,
                "language":    chunk.language,
                "patch":       chunk.patch,
                "additions":   chunk.additions,
                "deletions":   chunk.deletions,
                "skipped":     chunk.skipped,
                "skip_reason": chunk.skip_reason,
                "is_large":    chunk.is_large,
            })
        
       
        # Read GitHub's 'Link' header to extract the exact next page URL location.
        link_header = resp.headers.get("Link", "")
        url = None
        if link_header:
            links = link_header.split(",")
            for link in links:
                if 'rel=\"next\"' in link:
                    url = link.split(";")[0].strip("<> ")
                    break

    logger.info(f"Processed {len(chunks)} synchronized file chunks for {repo_full_name}#{pr_number}")
    return chunks