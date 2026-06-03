from django.db import models

# Create your models here.
import uuid

class Repo(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    github_repo_id = models.BigIntegerField(unique=True)
    full_name = models.CharField(max_length=255)  # e.g. "torvalds/linux"
    installation_id = models.BigIntegerField()     # GitHub App installation
    owner_login = models.CharField(max_length=100, blank=True, db_index=True)  # user or org login
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.full_name


class PullRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    repo = models.ForeignKey(Repo, on_delete=models.CASCADE, related_name="pull_requests")
    pr_number = models.IntegerField()
    title = models.CharField(max_length=500, blank=True)
    head_sha = models.CharField(max_length=40)
    author = models.CharField(max_length=100, blank=True)
    github_pr_id = models.BigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("repo", "pr_number")

    def __str__(self):
        return f"{self.repo.full_name}#{self.pr_number}"


class Review(models.Model):
    class Status(models.TextChoices):
        PENDING   = "pending",   "Pending"
        RUNNING   = "running",   "Running"
        COMPLETED = "completed", "Completed"
        FAILED    = "failed",    "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    pull_request = models.ForeignKey(PullRequest, on_delete=models.CASCADE, related_name="reviews")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    health_score = models.IntegerField(null=True, blank=True)   # 0–100
    celery_task_id = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)

    health_score = models.IntegerField(null=True, blank=True)   # 0–100 overall health score 
    prompt_tokens = models.IntegerField(null=True, blank=True)    # Track Claude API consumption costs
    completion_tokens = models.IntegerField(null=True, blank=True)

    github_summary_comment_id = models.BigIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Review for {self.pull_request} [{self.status}]"


class Comment(models.Model):
    class Severity(models.TextChoices):
        CRITICAL   = "critical",   "Critical"
        WARNING    = "warning",    "Warning"
        SUGGESTION = "suggestion", "Suggestion"

    class Category(models.TextChoices):
        DSA      = "dsa",      "DSA / Algorithm"
        SECURITY = "security", "Security"
        RESOURCE = "resource", "Resource / Reliability"
        OTHER    = "other",    "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="comments")
    file_path = models.CharField(max_length=500)
    line_start = models.IntegerField()
    line_end = models.IntegerField()
    severity = models.CharField(max_length=20, choices=Severity.choices)
    category = models.CharField(max_length=20, choices=Category.choices, default=Category.OTHER)
    issue = models.TextField()
    suggestion = models.TextField()
    time_complexity_before = models.CharField(max_length=50, blank=True)  # e.g. "O(n²)"
    time_complexity_after = models.CharField(max_length=50, blank=True)   # e.g. "O(n)"
    github_comment_id = models.BigIntegerField(null=True, blank=True)     # set after posting
    fixed_code_before = models.TextField(blank=True)  # bad code snippet
    fixed_code_after  = models.TextField(blank=True)  # corrected snippet
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"[{self.severity}] {self.file_path}:{self.line_start}"