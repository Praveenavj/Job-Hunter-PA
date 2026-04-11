"""
LLM client – Anthropic Claude (primary) with Ollama fallback.
Returns clear, user-friendly error messages on failure instead of raising 500.
"""
import httpx
import logging
from app.config import settings

logger = logging.getLogger(__name__)

_ERRORS = {
    400: "❌ LLM config error: check ANTHROPIC_MODEL in .env — use: claude-sonnet-4-5",
    401: "❌ Invalid ANTHROPIC_API_KEY. Get one at console.anthropic.com",
    403: "❌ API key lacks permission for this model.",
    429: "❌ Rate limit hit — wait 60s and try again.",
    500: "❌ Anthropic server error — try again shortly.",
    503: "❌ Anthropic temporarily unavailable.",
}


async def complete(system: str, user: str, max_tokens: int = 2048) -> str:
    """Call LLM. Claude first, Ollama fallback. Never raises — returns friendly error string."""
    if settings.anthropic_api_key:
        try:
            return await _anthropic(system, user, max_tokens)
        except httpx.HTTPStatusError as e:
            msg = _ERRORS.get(e.response.status_code,
                              f"❌ Anthropic error {e.response.status_code}")
            logger.warning(f"Anthropic {e.response.status_code}: {e.response.text[:200]}")
            # Try Ollama fallback
            if "localhost" in settings.ollama_api_url:
                try:
                    return await _ollama(system, user)
                except Exception:
                    pass
            return msg
        except Exception as e:
            logger.warning(f"Anthropic failed ({e}), trying Ollama")
            try:
                return await _ollama(system, user)
            except Exception as e2:
                return (f"❌ LLM unavailable.\n"
                        f"• Anthropic: {e}\n"
                        f"• Ollama: {e2}\n\n"
                        f"Check your ANTHROPIC_API_KEY in .env")

    try:
        return await _ollama(system, user)
    except httpx.ConnectError:
        return ("❌ No LLM configured.\n\n"
                "Add ANTHROPIC_API_KEY to .env — free tier at console.anthropic.com")
    except Exception as e:
        return f"❌ Ollama error: {e}"


async def _anthropic(system: str, user: str, max_tokens: int) -> str:
    headers = {
        "Content-Type": "application/json",
        "x-api-key": settings.anthropic_api_key,
        "anthropic-version": "2023-06-01",
    }
    payload = {
        "model": settings.anthropic_model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post("https://api.anthropic.com/v1/messages",
                              headers=headers, json=payload)
        r.raise_for_status()
    return r.json()["content"][0]["text"]


async def _ollama(system: str, user: str) -> str:
    payload = {
        "model": settings.ollama_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        "temperature": 0.4,
    }
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(settings.ollama_api_url,
                              headers={"Content-Type": "application/json"},
                              json=payload)
        r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]
