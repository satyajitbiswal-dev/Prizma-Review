import logging
from django.core.cache import cache
from .models import LLMKeyRingSlot

logger = logging.getLogger(__name__)

PENALTY_BOX_TTL = 300   # 429  → 5 minute cooldown
HARD_KILL_TTL   = 86400 # 401/403 → 24hr cooldown (until you manually fix)

TIER_ORDER = ["openrouter", "openai", "gemini"]


def _ban_key(slot_id: int, ttl: int, reason: str):
    cache.set(f"llm_ban:slot:{slot_id}", reason, timeout=ttl)


def _is_banned(slot_id: int) -> bool:
    return bool(cache.get(f"llm_ban:slot:{slot_id}"))


def acquire_healthy_key_slot() -> LLMKeyRingSlot:
    """
    Tiered cascading selector:
      Tier 1 (OpenRouter) → Tier 2 (OpenAI) → Tier 3 (Gemini)

    Within each tier: picks least-used key that isn't banned in Redis.
    Thread-safe: Redis ban checks are atomic reads.
    """
    active_slots = LLMKeyRingSlot.objects.filter(is_active=True)

    if not active_slots.exists():
        raise RuntimeError(
            "CRITICAL: All API key slots are deactivated. "
            "Add keys via Django admin."
        )

    # Try each tier in priority order
    for tier in TIER_ORDER:
        tier_slots = active_slots.filter(provider=tier)

        for slot in tier_slots:  # already ordered by total_calls_handled
            if _is_banned(slot.id):
                logger.debug(
                    f"Slot #{slot.id} [{tier}] is in penalty box — skipping"
                )
                continue

            logger.info(
                f"Acquired slot #{slot.id} [{tier}] "
                f"({slot.account_identifier})"
            )
            return slot

        logger.warning(f"All Tier [{tier}] keys in penalty box — cascading down")

    raise RuntimeError(
        "ALL_TIERS_EXHAUSTED: Every key across all 3 tiers is rate-limited. "
        "Retry in 5 minutes."
    )


def penalize_slot(slot: LLMKeyRingSlot, status_code: int):
    """Call this after a failed API call."""
    if status_code == 429:
        _ban_key(slot.id, PENALTY_BOX_TTL, "rate_limited")
        logger.warning(
            f"Slot #{slot.id} [{slot.provider}] → penalty box "
            f"for {PENALTY_BOX_TTL}s (429)"
        )

    elif status_code in (401, 403):
        # Hard kill in DB — needs manual intervention
        slot.is_active = False
        slot.save(update_fields=["is_active"])
        logger.critical(
            f"Slot #{slot.id} [{slot.provider}] → permanently disabled "
            f"(auth/billing failure {status_code})"
        )

    else:
        # Soft ban for other 5xx errors
        _ban_key(slot.id, 60, f"error_{status_code}")
        logger.warning(f"Slot #{slot.id} soft-banned 60s (status {status_code})")


def get_keyring_status() -> list[dict]:
    """Returns live status of all keys — for dashboard monitoring."""
    slots = LLMKeyRingSlot.objects.all()
    result = []
    for slot in slots:
        ban_val = cache.get(f"llm_ban:slot:{slot.id}")
        result.append({
            "id":         slot.id,
            "provider":   slot.provider,
            "account":    slot.account_identifier,
            "is_active":  slot.is_active,
            "calls":      slot.total_calls_handled,
            "status": (
                "disabled"   if not slot.is_active
                else "banned" if ban_val
                else "available"
            ),
        })
    return result