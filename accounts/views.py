# accounts/views.py
import requests
import logging
from django.conf import settings
from django.shortcuts import redirect, render
from django.contrib.auth import login, logout as auth_logout
from django.contrib.auth.models import User

from .models import GitHubProfile 

logger = logging.getLogger(__name__)

GITHUB_OAUTH_URL  = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL  = "https://github.com/login/oauth/access_token"
GITHUB_USER_API   = "https://api.github.com/user"


def login_page(request):
    """Renders the login wall screen. Bypasses straight to dashboard if already authorized."""
    if request.user.is_authenticated:
        return redirect("dashboard_home")  
    return render(request, "registration/login.html")


def github_login(request):
    """Redirects the user to GitHub's authorization screen."""
    params = (
        f"?client_id={settings.GITHUB_OAUTH_CLIENT_ID}"
        f"&scope=read:user%20read:org"
    )
    return redirect(GITHUB_OAUTH_URL + params)


def github_callback(request):
    """Processes incoming GitHub authorization codes."""
    code = request.GET.get("code")
    if not code:
        return redirect("github_login")  # Bounces back to login trigger if code is missing

    # Exchange authorization code for access token
    token_resp = requests.post(
        GITHUB_TOKEN_URL,
        json={
            "client_id":     settings.GITHUB_OAUTH_CLIENT_ID,
            "client_secret": settings.GITHUB_OAUTH_CLIENT_SECRET,
            "code":          code,
        },
        headers={"Accept": "application/json"},
        timeout=10,
    )
    
    token_json = token_resp.json()
    access_token = token_json.get("access_token")
    if not access_token:
        logger.error(f"OAuth token exchange failed: {token_json}")
        return redirect("github_login")

    # Fetch GitHub user profile parameters
    try:
        user_resp = requests.get(
            GITHUB_USER_API,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        user_data = user_resp.json()
    except Exception as e:
        logger.error(f"Failed fetching data from user API: {e}")
        return redirect("github_login")

    # Sync Django core auth instance
    user, _ = User.objects.get_or_create(
        username=user_data["login"],
        defaults={"email": user_data.get("email") or ""}
    )

    # Sync extended profiles schema
    GitHubProfile.objects.update_or_create(
        user=user,
        defaults={
            "github_id":    user_data["id"],
            "avatar_url":   user_data.get("avatar_url", ""),
            "access_token": access_token,
        },
    )

    # Initialize session cookie infrastructure
    login(request, user)
    logger.info(f"User @{user.username} authenticated successfully via OAuth portal.")
    return redirect("dashboard_home")


def logout(request):
    """Terminates session tokens and drops user right back to the custom login screen."""
    auth_logout(request)  
    logger.info("Session terminated successfully.")
    return redirect("login_page")