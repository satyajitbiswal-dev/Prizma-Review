import logging
from django.core.cache import cache
from .models import LLMKeyRingSlot

logger = logging.getLogger(__name__)

PENALTY_BOX_TTL = 300   # 429  → 5 minute cooldown
HARD_KILL_TTL   = 86400 # 401/403 → 24hr cooldown (until you manually fix)

TIER_ORDER = ["openrouter", "gemini", "openai"]


def _ban_key(slot_id: int, ttl: int, reason: str):
    cache.set(f"llm_ban:slot:{slot_id}", reason, timeout=ttl)


def _is_banned(slot_id: int) -> bool:
    return bool(cache.get(f"llm_ban:slot:{slot_id}"))


def acquire_healthy_key_slot() -> LLMKeyRingSlot:
    """
    Tiered cascading selector:
      Tier 1 (OpenRouter) → Tier 2 (Gemini) → Tier 3 (OpenAI)

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


def penalize_slot(slot: LLMKeyRingSlot, status_code: int, error_message: str = "") -> bool:
    """
    Smarter Circuit Breaker: Distinguishes between standard transit bottlenecks
    and hard financial/credential outages. Returns True if a hard fatal block occurred.
    """
    msg_lower = error_message.lower()

    # Include 402 (Payment Required) and catch OpenAI's quota notice disguised as a 429
    is_hard_kill = (
        status_code in (401, 403, 402) or 
        (status_code == 429 and "quota" in msg_lower)
    )

    if is_hard_kill:
        # Hard kill in database — prevents looping dead tokens or running out of funds mid-flight
        slot.is_active = False
        slot.save(update_fields=["is_active"])
        logger.critical(
            f"HARD BLOCK: Slot #{slot.id} [{slot.provider}] → permanently disabled "
            f"due to structural auth/billing failure ({status_code}). Reason: {error_message}"
        )
        return True

    elif status_code == 429:
        # Standard rate-limiting behavior
        _ban_key(slot.id, PENALTY_BOX_TTL, "rate_limited")
        logger.warning(
            f"SOFT BAN: Slot #{slot.id} [{slot.provider}] → penalty box "
            f"for {PENALTY_BOX_TTL}s (429 Traffic Volatility)"
        )
        return False

    else:
        # Soft ban for 500, 503, or other temporary provider-side disruptions
        _ban_key(slot.id, 60, f"error_{status_code}")
        logger.warning(f"SOFT BAN: Slot #{slot.id} soft-banned 60s (status {status_code})")
        return False


def get_keyring_status() -> list[dict]:
    """Returns live status tracking overview of all infrastructure keys."""
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