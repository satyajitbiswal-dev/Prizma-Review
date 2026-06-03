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
