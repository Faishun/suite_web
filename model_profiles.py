from __future__ import annotations

from urllib.parse import urlparse

from suite_web.crypto import decrypt_str
from suite_web.models import ModelProfile, ProviderKind
from suite_web.settings import Settings


# Values we treat as "no key" so we don't send them (use override/sk-local instead).
# "lmstudio" / "lm-studio" are NOT included so saved profile keys are used.
_LOCAL_NO_AUTH_PLACEHOLDERS = frozenset(
    s.strip().lower()
    for s in (
        "ollama",
        "local",
        "no",
        "none",
        "n/a",
        "not-needed",
        "sk-no-op",
        "",
    )
)


def decrypt_api_key(settings: Settings, profile: ModelProfile | None) -> str | None:
    if profile is None:
        return None
    if not profile.api_key_enc:
        return None
    return decrypt_str(settings.master_key, profile.api_key_enc)


def api_key_for_openai_env(settings: Settings, profile: ModelProfile | None) -> str | None:
    """
    Key to use for OPENAI_API_KEY when calling local/openai-compat endpoints.
    Returns None if no key; returns a harmless placeholder for no-auth placeholders
    so we don't send 'lmstudio' etc. (which some servers reject with 401).
    """
    raw = decrypt_api_key(settings, profile)
    if not raw or str(raw).strip().lower() in _LOCAL_NO_AUTH_PLACEHOLDERS:
        return None
    return raw


def normalize_openai_base_url(base_url: str) -> str:
    """
    Normalize user-provided base_url to an OpenAI-style base like http(s)://host:port/v1
    """
    u = (base_url or "").strip()
    if not u:
        return ""
    u = u.rstrip("/")
    # If user provided /v1/chat/completions, trim to /v1
    if u.endswith("/v1/chat/completions"):
        return u[: -len("/chat/completions")]
    if u.endswith("/chat/completions"):
        return u[: -len("/chat/completions")]
    if u.endswith("/v1"):
        return u
    # Heuristic: if it already contains /v1 somewhere, keep as is.
    if "/v1" in u:
        return u
    return u + "/v1"


def rest_chat_completions_url(base_url: str) -> str:
    base = normalize_openai_base_url(base_url)
    return base.rstrip("/") + "/chat/completions"


def extract_port(base_url: str) -> int | None:
    try:
        p = urlparse(base_url)
        return p.port
    except Exception:
        return None

