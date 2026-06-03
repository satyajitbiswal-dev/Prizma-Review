from django.shortcuts import render

# Create your views here.
import hashlib
import hmac
import json
import logging

from django.conf import settings
import requests
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import request, status

from reviews.models import Repo, PullRequest, Review
from tasks.review_tasks import process_pr_review
from github_client.status_poster import post_commit_status_pending
from github_client.installations import (
    upsert_repo,
    deactivate_repo,
    sync_installation_repos,
)

logger = logging.getLogger(__name__)


def handle_installation_event(payload: dict) -> Response:
    """GitHub App installed, updated, or removed."""
    action = payload.get("action", "")
    installation = payload.get("installation", {})
    installation_id = installation.get("id")
    account_login = (installation.get("account") or {}).get("login", "")

    if action == "deleted":
        if installation_id:
            Repo.objects.filter(installation_id=installation_id).update(is_active=False)
        logger.info("Installation removed: %s", installation_id)
        return Response({"status": "installation_deleted"})

    if action in ("created", "new_permissions_accepted"):
        repos = payload.get("repositories") or []
        if repos:
            for repo_data in repos:
                upsert_repo(repo_data, installation_id, account_login)
        elif installation_id:
            sync_installation_repos(installation_id, account_login)
        logger.info(
            "Installation %s for @%s — synced %s repo(s)",
            action,
            account_login,
            len(repos) or "all",
        )
        return Response({"status": "installation_synced", "action": action})

    return Response({"status": "ignored", "action": action})


def handle_installation_repositories_event(payload: dict) -> Response:
    """Repos added or removed from an existing installation."""
    action = payload.get("action", "")
    installation = payload.get("installation", {})
    installation_id = installation.get("id")
    account_login = (installation.get("account") or {}).get("login", "")

    if action == "added":
        for repo_data in payload.get("repositories_added") or []:
            upsert_repo(repo_data, installation_id, account_login)
        return Response({"status": "repos_added"})

    if action == "removed":
        for repo_data in payload.get("repositories_removed") or []:
            deactivate_repo(repo_data["id"])
        return Response({"status": "repos_removed"})

    return Response({"status": "ignored", "action": action})


class GitHubWebhookView(APIView):
    authentication_classes = []  # GitHub signs requests, no session/token needed
    permission_classes = []

    def post(self, request):
        # ── 1. Validate HMAC signature ─────────────────────────────────────
        if not self._verify_signature(request):
            logger.warning("Webhook rejected: invalid signature")
            return Response({"error": "invalid signature"}, status=status.HTTP_401_UNAUTHORIZED)

        event = request.headers.get("X-GitHub-Event", "")
        if event == "issue_comment":
            return GitHubCommentWebhookView().post(request)

        delivery_id = request.headers.get("X-GitHub-Delivery", "")
        payload = request.data

        logger.info(
            "GitHub webhook received: event=%s delivery=%s",
            event,
            delivery_id,
        )

        if event == "installation":
            return handle_installation_event(payload)

        if event == "installation_repositories":
            return handle_installation_repositories_event(payload)

        # ── 2. Only pull_request events ─────────────────────────
        if event != "pull_request":
            logger.info("Webhook ignored (not pull_request): event=%s", event)
            return Response({"status": "ignored", "event": event})

        action = payload.get("action", "")
        if action not in ("opened", "synchronize", "reopened"):
            logger.info("Webhook ignored (action=%s) — only opened/synchronize/reopened queue Celery", action)
            return Response({"status": "ignored", "action": action})

        # ── 3. Extract PR metadata ─────────────────────────────────────────
        pr_data   = payload["pull_request"]
        repo_data = payload["repository"]

        installation = payload.get("installation") or {}
        owner_login = (
            (repo_data.get("owner") or {}).get("login")
            or (installation.get("account") or {}).get("login", "")
        )
        repo, _ = Repo.objects.update_or_create(
            github_repo_id=repo_data["id"],
            defaults={
                "full_name": repo_data["full_name"],
                "installation_id": installation["id"],
                "owner_login": owner_login,
                "is_active": True,
            },
        )

        pull_request, _ = PullRequest.objects.update_or_create(
            repo=repo,
            pr_number=pr_data["number"],
            defaults={
                "title":       pr_data.get("title", ""),
                "head_sha":    pr_data["head"]["sha"],
                "author":      pr_data["user"]["login"],
                "github_pr_id": pr_data["id"],
            },
        )

        review = Review.objects.create(pull_request=pull_request)

        # Add github status 
        try:
            post_commit_status_pending(
                repo_full_name=repo.full_name, head_sha=pull_request.head_sha,
                installation_id=repo.installation_id,
            )
        except Exception as e:
            logger.warning(f"Could not post pending status: {e}")

        # ── 4. Push to Celery  ─────────────────────
        task = process_pr_review.delay(
            review_id=str(review.id),
            repo_full_name=repo.full_name,
            pr_number=pull_request.pr_number,
            installation_id=repo.installation_id,
            head_sha=pull_request.head_sha,
        )

        review.celery_task_id = task.id
        review.status = Review.Status.RUNNING
        review.save(update_fields=["celery_task_id", "status"])

        logger.info(
            "Queued Celery task %s for review %s (%s#%s, action=%s)",
            task.id,
            review.id,
            repo.full_name,
            pull_request.pr_number,
            action,
        )
        return Response({
            "status": "queued",
            "review_id": str(review.id),
            "celery_task_id": task.id,
        })

    # ── Helpers ────────────────────────────────────────────────────────────

    def _verify_signature(self, request) -> bool:
        secret = settings.GITHUB_WEBHOOK_SECRET
        if not secret:
            logger.error("GITHUB_WEBHOOK_SECRET not set — rejecting all webhooks")
            return False

        sig_header = request.headers.get("X-Hub-Signature-256", "")
        if not sig_header.startswith("sha256="):
            return False

        expected = hmac.new(
            secret.encode(),
            request.body,
            hashlib.sha256,
        ).hexdigest()
        
        received = sig_header[len("sha256="):]
        return hmac.compare_digest(expected, received)
    

class GitHubCommentWebhookView(APIView):
    authentication_classes = []
    permission_classes     = []

    TRIGGER_PHRASES = (
        "prizmareview: recheck",
        "prizmareview recheck",
        "/recheck",
    )

    def post(self, request):
        if not self._verify_signature(request):
            return Response({"error": "invalid signature"}, status=401)

        event = request.headers.get("X-GitHub-Event", "")
        if event != "issue_comment":
            return Response({"status": "ignored"})

        payload = request.data
        action  = payload.get("action", "")

        # Only react to new comments
        if action != "created":
            return Response({"status": "ignored"})

        # Only react to PR comments (not issue comments)
        if not payload.get("issue", {}).get("pull_request"):
            return Response({"status": "ignored"})

        comment_body = payload.get("comment", {}).get("body", "").lower().strip()
        if not any(trigger in comment_body for trigger in self.TRIGGER_PHRASES):
            return Response({"status": "ignored", "reason": "not a trigger phrase"})

        # Extract PR info
        repo_data    = payload["repository"]
        pr_number    = payload["issue"]["number"]
        commenter    = payload["comment"]["user"]["login"]

        try:
            repo = Repo.objects.get(github_repo_id=repo_data["id"])
        except Repo.DoesNotExist:
            return Response({"error": "repo not found"}, status=404)

        try:
            pull_request = PullRequest.objects.get(
                repo=repo, pr_number=pr_number
            )
        except PullRequest.DoesNotExist:
            return Response({"error": "PR not found"}, status=404)

        # Create fresh review
        review = Review.objects.create(pull_request=pull_request)

        task = process_pr_review.delay(
            review_id=str(review.id),
            repo_full_name=repo.full_name,
            pr_number=pull_request.pr_number,
            installation_id=repo.installation_id,
            head_sha=pull_request.head_sha,
        )

        review.celery_task_id = task.id
        review.status         = Review.Status.RUNNING
        review.save(update_fields=["celery_task_id", "status"])

        # React to the comment with 👀 so user knows it worked
        self._react_to_comment(
            repo_full_name=repo.full_name,
            comment_id=payload["comment"]["id"],
            installation_id=repo.installation_id,
        )

        logger.info(
            f"Recheck triggered by @{commenter} "
            f"for {repo.full_name}#{pr_number}"
        )
        return Response({"status": "queued", "review_id": str(review.id)})

    def _verify_signature(self, request) -> bool:
        # Reuse same logic from GitHubWebhookView
        secret     = settings.GITHUB_WEBHOOK_SECRET
        sig_header = request.headers.get("X-Hub-Signature-256", "")
        if not sig_header.startswith("sha256="):
            return False
        import hmac, hashlib
        expected = hmac.new(
            secret.encode(), request.body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, sig_header[7:])

    def _react_to_comment(self, repo_full_name: str,
                           comment_id: int, installation_id: int):
        """Post 👀 reaction so user knows the bot saw the command."""
        try:
            from github_client.gh_client import get_installation_token
            token = get_installation_token(installation_id)
            url   = (
                f"https://api.github.com/repos/{repo_full_name}"
                f"/issues/comments/{comment_id}/reactions"
            )
            requests.post(
                url,
                json={"content": "eyes"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
                timeout=5,
            )
        except Exception as e:
            logger.warning(f"Could not react to comment: {e}")