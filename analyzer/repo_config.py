import yaml
import logging
import requests
from dataclasses import dataclass, field
from typing import Optional
from github_client.gh_client import get_installation_token

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "fail_threshold":   50,
    "max_comments":     15,
    "skip_categories":  [],
    "skip_paths":       [],
    "language_focus":   [],
    "review_tests":     False,
}


@dataclass
class RepoConfig:
    fail_threshold:  int              = 50
    max_comments:    int              = 15
    skip_categories: list[str]        = field(default_factory=list)
    skip_paths:      list[str]        = field(default_factory=list)
    language_focus:  list[str]        = field(default_factory=list)
    review_tests:    bool             = False


def fetch_repo_config(repo_full_name: str,
                      installation_id: int,
                      head_sha: str) -> RepoConfig:
    """
    Fetches .prizmareview.yml from the repo at the PR's head SHA.
    Falls back to defaults if file doesn't exist.
    """
    token = get_installation_token(installation_id)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept":        "application/vnd.github+json",
    }

    url = (
        f"https://api.github.com/repos/{repo_full_name}"
        f"/contents/.prizmareview.yml?ref={head_sha}"
    )

    try:
        resp = requests.get(url, headers=headers, timeout=10)

        if resp.status_code == 404:
            logger.info(f"No .prizmareview.yml in {repo_full_name} — using defaults")
            return RepoConfig()

        resp.raise_for_status()

        import base64
        content = base64.b64decode(resp.json()["content"]).decode("utf-8")
        raw     = yaml.safe_load(content) or {}

        config = RepoConfig(
            fail_threshold  = int(raw.get("fail_threshold",  DEFAULT_CONFIG["fail_threshold"])),
            max_comments    = int(raw.get("max_comments",    DEFAULT_CONFIG["max_comments"])),
            skip_categories = [s.upper() for s in raw.get("skip_categories", [])],
            skip_paths      = raw.get("skip_paths",     []),
            language_focus  = [l.lower() for l in raw.get("language_focus", [])],
            review_tests    = bool(raw.get("review_tests", False)),
        )

        logger.info(
            f"Loaded .prizmareview.yml from {repo_full_name} — "
            f"threshold={config.fail_threshold} "
            f"skip={config.skip_categories}"
        )
        return config

    except yaml.YAMLError as e:
        logger.warning(f"Invalid .prizmareview.yml in {repo_full_name}: {e} — using defaults")
        return RepoConfig()

    except Exception as e:
        logger.warning(f"Could not fetch .prizmareview.yml: {e} — using defaults")
        return RepoConfig()


def should_skip_path(filename: str, skip_paths: list[str]) -> bool:
    """Check if file matches any skip_paths pattern from config."""
    import fnmatch
    return any(fnmatch.fnmatch(filename, pattern) for pattern in skip_paths)