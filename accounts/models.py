from django.db import models
from django.contrib.auth.models import User
import uuid

class GitHubProfile(models.Model):
    """Extends standard Django user architecture with GitHub specific metadata parameters."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # One-to-one link to native Django Auth User ───────────────────
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="github_profile")
    
    # GitHub Identity Specs
    github_id = models.BigIntegerField(unique=True)
    avatar_url = models.URLField(max_length=500, blank=True)
    access_token = models.CharField(max_length=255)  # GitHub User OAuth Token
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"GitHub Profile for @{self.user.username}"