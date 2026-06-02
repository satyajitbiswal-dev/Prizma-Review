from django.db import models
from encrypted_model_fields.fields import EncryptedCharField

class LLMKeyRingSlot(models.Model):

    PROVIDER_CHOICES = [
        ("openrouter", "OpenRouter"),
        ("gemini",     "Gemini"),
        ("openai",     "OpenAI"),
    ]

    account_identifier = models.CharField(max_length=100,
        help_text="e.g. gemini_acc1, openrouter_acc1, openai_acc2"
    )
    provider = models.CharField(max_length=30, choices=PROVIDER_CHOICES)

    # Encrypted at rest — decrypted in RAM only
    key_value = EncryptedCharField(max_length=255)

    model_override = models.CharField(
        max_length=100, blank=True,
        help_text="Optional: override default model string profile for this key"
    )
    is_active           = models.BooleanField(default=True)
    total_calls_handled = models.IntegerField(default=0)

    class Meta:
        ordering = ["total_calls_handled"]

    def __str__(self):
        return f"[{self.get_provider_display()}] {self.account_identifier}"