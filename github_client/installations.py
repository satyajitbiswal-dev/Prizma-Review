"""Sync GitHub App installations and repositories into the local database."""
import logging

import requests
from django.conf import settings
from django.db.models import Q

from github_client.gh_client import get_app_jwt, get_installation_token
from reviews.models import Repo

logger = logging.getLogger(__name__)


def upsert_repo(repo_data: dict, installation_id: int, owner_login: str) -> Repo:
    """Create or refresh a Repo row from GitHub repository metadata."""
    full_name = repo_data.get("full_name") or repo_data.get("name", "")
    owner = repo_data.get("owner", {}) or {}
    owner_login = owner_login or owner.get("login", "")

    repo, created = Repo.objects.update_or_create(
        github_repo_id=repo_data["id"],
        defaults={
            "full_name": full_name,
            "installation_id": installation_id,
            "owner_login": owner_login,
            "is_active": True,
        },
    )
    if created:
        logger.info("Registered repo %s (installation %s)", full_name, installation_id)
    return repo


def deactivate_repo(github_repo_id: int) -> None:
    Repo.objects.filter(github_repo_id=github_repo_id).update(is_active=False)


def fetch_installation_repositories(installation_id: int) -> list[dict]:
    """List every repository the app can access for this installation."""
    token = get_installation_token(installation_id)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    repos: list[dict] = []
    url = "https://api.github.com/installation/repositories?per_page=100"
    while url:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
        repos.extend(payload.get("repositories", []))
        url = None
        link = resp.headers.get("Link", "")
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split(";")[0].strip().strip("<>")
                break
    return repos


def sync_installation_repos(installation_id: int, owner_login: str) -> int:
    """Pull all repos for an installation and upsert locally. Returns count synced."""
    try:
        remote_repos = fetch_installation_repositories(installation_id)
    except Exception as exc:
        logger.error("Failed to list repos for installation %s: %s", installation_id, exc)
        return 0

    count = 0
    for repo_data in remote_repos:
        upsert_repo(repo_data, installation_id, owner_login)
        count += 1
    return count


def fetch_user_installations(access_token: str) -> list[dict]:
    """Installations of this GitHub App visible to the logged-in user."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    app_id = int(settings.GITHUB_APP_ID)
    installations: list[dict] = []
    url = "https://api.github.com/user/installations?per_page=100"
    while url:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 401:
            logger.warning("GitHub OAuth token expired or invalid for installation sync")
            return []
        resp.raise_for_status()
        for inst in resp.json().get("installations", []):
            if int(inst.get("app_id", 0)) == app_id:
                installations.append(inst)
        url = None
        link = resp.headers.get("Link", "")
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.split(";")[0].strip().strip("<>")
                break
    return installations


def sync_installations_for_user(user) -> int:
    """
    Refresh repos the current dashboard user should see.
    Uses their OAuth token first, then falls back to matching App installations by login.
    """
    github_login = user.username
    total = 0

    try:
        profile = user.github_profile
        if profile.access_token:
            for inst in fetch_user_installations(profile.access_token):
                account_login = inst.get("account", {}).get("login", github_login)
                total += sync_installation_repos(inst["id"], account_login)
    except Exception as exc:
        logger.warning("OAuth installation sync skipped for @%s: %s", github_login, exc)

    if total:
        return total

    # Fallback: match installations on this app where account login equals the user
    try:
        app_jwt = get_app_jwt()
        headers = {
            "Authorization": f"Bearer {app_jwt}",
            "Accept": "application/vnd.github+json",
        }
        url = "https://api.github.com/app/installations?per_page=100"
        while url:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            for inst in resp.json():
                account = inst.get("account", {})
                if account.get("login", "").lower() == github_login.lower():
                    total += sync_installation_repos(inst["id"], account["login"])
            url = None
            link = resp.headers.get("Link", "")
            for part in link.split(","):
                if 'rel="next"' in part:
                    url = part.split(";")[0].strip().strip("<>")
                    break
    except Exception as exc:
        logger.warning("App JWT installation sync failed: %s", exc)

    return total


def repos_queryset_for_user(user):
    """Repos this user is allowed to see on the dashboard."""
    login = user.username
    return Repo.objects.filter(is_active=True).filter(
        Q(owner_login__iexact=login) | Q(full_name__istartswith=f"{login}/")
    )
