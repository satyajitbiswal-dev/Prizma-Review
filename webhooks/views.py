from django.shortcuts import render

# Create your views here.
import hashlib
import hmac
import json
import logging

from django.conf import settings
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status

from reviews.models import Repo,PullRequest,Review
from tasks.review_tasks import process_pr_review

logger = logging.getLogger(__name__)


class GitHubWebhookView(APIView):
    authentication_classes = []  # GitHub signs requests, no session/token needed
    permission_classes = []

    def post(self, request):
        # ── 1. Validate HMAC signature ─────────────────────────────────────
        if not self._verify_signature(request):
            logger.warning("Webhook rejected: invalid signature")
            return Response({"error": "invalid signature"}, status=status.HTTP_401_UNAUTHORIZED)

        event = request.headers.get("X-GitHub-Event", "")
        payload = request.data  # DRF already parsed JSON

        # ── 2. Only care about pull_request events ─────────────────────────
        if event != "pull_request":
            return Response({"status": "ignored", "event": event})

        action = payload.get("action", "")
        if action not in ("opened", "synchronize", "reopened"):
            return Response({"status": "ignored", "action": action})

        # ── 3. Extract PR metadata ─────────────────────────────────────────
        pr_data   = payload["pull_request"]
        repo_data = payload["repository"]

        repo, _ = Repo.objects.get_or_create(
            github_repo_id=repo_data["id"],
            defaults={
                "full_name":       repo_data["full_name"],
                "installation_id": payload["installation"]["id"],
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

        # ── 4. Push to Celery — return 200 immediately ─────────────────────
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

        logger.info(f"Queued review {review.id} for {repo.full_name}#{pull_request.pr_number}")
        return Response({"status": "queued", "review_id": str(review.id)})

    # ── Helpers ────────────────────────────────────────────────────────────

    def _verify_signature(self, request) -> bool:
        secret = settings.GITHUB_WEBHOOK_SECRET
        print(f"Secret: {secret}")
        if not secret:
            logger.error("GITHUB_WEBHOOK_SECRET not set — rejecting all webhooks")
            return False

        sig_header = request.headers.get("X-Hub-Signature-256", "")
        print(f"Signature header: {sig_header}")
        if not sig_header.startswith("sha256="):
            return False

        expected = hmac.new(
            secret.encode(),
            request.body,
            hashlib.sha256,
        ).hexdigest()
        print(f"Expected signature: sha256={expected}")

        received = sig_header[len("sha256="):]
        return hmac.compare_digest(expected, received)