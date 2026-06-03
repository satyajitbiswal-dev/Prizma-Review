import requests
import logging
from github_client.gh_client import get_installation_token

logger = logging.getLogger(__name__)

FAIL_THRESHOLD = 50  # score below this = failure status


def post_commit_status(repo_full_name: str, head_sha: str,
                       installation_id: int, health_score: int,
                       issue_count: int, fail_threshold: int = FAIL_THRESHOLD):

    """
    Posts a GitHub commit status — shows as ✅ or ❌ on the PR.
    Called BEFORE posting inline comments so status appears first.
    """
    token = get_installation_token(installation_id)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }

    # Determine pass/fail
    if health_score >= fail_threshold:
        state       = "success"
        description = f"Score {health_score}/100 — {issue_count} issue(s) found"
    else:
        state       = "failure"
        description = f"Score {health_score}/100 — {issue_count} critical issue(s) require attention"

    payload = {
        "state":       state,
        "description": description,
        "context":     "prizmareview / DSA + Security Analysis",
        "target_url":  f"https://github.com/{repo_full_name}/pull/",
    }

    url = f"https://api.github.com/repos/{repo_full_name}/statuses/{head_sha}"
    resp = requests.post(url, json=payload, headers=headers, timeout=10)
    resp.raise_for_status()

    logger.info(
        f"Commit status posted → {state} "
        f"({repo_full_name}@{head_sha[:7]}) score={health_score}"
    )


def post_commit_status_pending(repo_full_name: str, head_sha: str,
                                installation_id: int):
    """
    Post 'pending' status immediately when webhook is received.
    Shows spinner on PR while review is running.
    """
    token = get_installation_token(installation_id)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept":        "application/vnd.github+json",
    }
    payload = {
        "state":       "pending",
        "description": "prizmareview is analyzing your code...",
        "context":     "prizmareview / DSA + Security Analysis",
    }
    url = f"https://api.github.com/repos/{repo_full_name}/statuses/{head_sha}"
    resp = requests.post(url, json=payload, headers=headers, timeout=10)
    resp.raise_for_status()
    logger.info(f"Commit status → pending ({repo_full_name}@{head_sha[:7]})")


UNAVAILABLE_STATUS_DESCRIPTION = (
    "Sorry — prizmareview is temporarily unavailable. Please try again shortly."
)


def post_commit_status_error(repo_full_name: str, head_sha: str,
                              installation_id: int, reason: str = ""):
    """Post error status if review crashes."""
    token = get_installation_token(installation_id)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept":        "application/vnd.github+json",
    }
    payload = {
        "state":       "error",
        "description": f"Review failed: {reason[:100]}" if reason else "Review failed — will retry",
        "context":     "prizmareview / DSA + Security Analysis",
    }
    url = f"https://api.github.com/repos/{repo_full_name}/statuses/{head_sha}"
    requests.post(url, json=payload, headers=headers, timeout=10)
    logger.error(f"Commit status → error ({repo_full_name}@{head_sha[:7]})")


def post_commit_status_unavailable(repo_full_name: str, head_sha: str,
                                   installation_id: int):
    """Post when the review could not run — never show a misleading green 100/100."""
    token = get_installation_token(installation_id)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept":        "application/vnd.github+json",
    }
    payload = {
        "state":       "error",
        "description": UNAVAILABLE_STATUS_DESCRIPTION[:140],
        "context":     "prizmareview / DSA + Security Analysis",
    }
    url = f"https://api.github.com/repos/{repo_full_name}/statuses/{head_sha}"
    resp = requests.post(url, json=payload, headers=headers, timeout=10)
    resp.raise_for_status()
    logger.error("Commit status → unavailable (%s@%s)", repo_full_name, head_sha[:7])