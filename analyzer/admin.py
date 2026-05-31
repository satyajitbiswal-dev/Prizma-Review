from django.contrib import admin

# Register your models here.
from .models import LLMKeyRingSlot
@admin.register(LLMKeyRingSlot)
class LLMKeyRingSlotAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "provider",
        "account_identifier",
        "total_calls_handled",
        "is_active",
    )
    list_filter = ("provider", "is_active")
    search_fields = ("account_identifier",)