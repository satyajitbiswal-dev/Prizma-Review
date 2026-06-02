from django.urls import path
from .views import GitHubCommentWebhookView, GitHubWebhookView

urlpatterns = [
    # path("github/", GitHubWebhookView.as_view(), name="github-webhook"),
    path("github", GitHubWebhookView.as_view(), name="github-webhook-no-slash"),
    path("github/comments/", GitHubCommentWebhookView.as_view(), name="github-comment-webhook"),
]