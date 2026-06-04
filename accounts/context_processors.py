from django.conf import settings

from accounts.models import GitHubProfile


def github_user(request):
    """Expose GitHub profile + avatar safely on every template."""
    if not request.user.is_authenticated:
        return {
            "github_profile": None,
            "github_avatar_url": "",
            "github_display_name": "",
        }

    try:
        profile = GitHubProfile.objects.get(user=request.user)
    except GitHubProfile.DoesNotExist:
        return {
            "github_profile": None,
            "github_avatar_url": "",
            "github_display_name": request.user.username,
        }

    return {
        "github_profile": profile,
        "github_avatar_url": (profile.avatar_url or "").strip(),
        "github_display_name": request.user.username,
    }


def prizmareview_links(request):
    """GitHub repo + per-repo config docs for all templates."""
    repo = getattr(settings, "PRIZMAREVIEW_GITHUB_REPO", "")
    return {
        "prizmareview_repo_url": repo,
        "prizmareview_config_file": getattr(settings, "PRIZMAREVIEW_CONFIG_FILENAME", ".prizmareview.yml"),
        "prizmareview_docs_config_url": getattr(
            settings,
            "PRIZMAREVIEW_DOCS_CONFIG_URL",
            f"{repo.rstrip('/')}/#per-repo-configuration" if repo else "",
        ),
        "prizmareview_docs_readme_url": repo,
    }
