"""
LLM Client – Priority Order:
  1. Puter Bridge (free Claude via puter.com – no API key needed)
  2. Anthropic API (paid, if key is set + has credits)
  3. Ollama (local fallback, if running)

Set in .env:
  PUTER_AUTH_TOKEN=xxx  → enables Puter Bridge (free)
  ANTHROPIC_API_KEY=xxx → enables Claude API (paid)
  (neither set)         → uses Ollama (local)
"""
import httpx
import json
import logging
import asyncio
from app.config import settings

logger = logging.getLogger(__name__)

PUTER_BRIDGE_URL = getattr(settings, "puter_bridge_url", "http://localhost:3456")
PUTER_MAX_RETRIES = 2
PUTER_RETRY_DELAY = 1.0  # seconds


def _parse_anthropic_error(response_text: str, status_code: int) -> str:
    """Convert Anthropic API errors into user-friendly messages."""
    try:
        data = json.loads(response_text)
        msg = data.get("error", {}).get("message", "")
        
        if "credit_balance" in msg.lower() or "too low" in msg.lower():
            return (
                "❌ *Anthropic API credit balance is too low.*\n\n"
                "🔗 Top up at: https://console.anthropic.com/settings/billing\n\n"
                "_Note: Your claude.ai Pro subscription ≠ API credits. "
                "They are separate billing systems._\n\n"
                "💡 *Free alternative:* Use the Puter Bridge instead!\n"
                "→ See `puter_bridge/server.js` for one-time setup."
            )
        if "invalid_api_key" in msg.lower() or "authentication" in msg.lower():
            return "❌ Invalid `ANTHROPIC_API_KEY`. Check your `.env` file."
        if "overloaded" in msg.lower() or "rate_limit" in msg.lower():
            return "❌ Anthropic is temporarily overloaded. Wait ~30s and try again."
        return f"❌ Anthropic error ({status_code}): {msg[:150]}"
    except Exception:
        return f"❌ Anthropic API error (HTTP {status_code})."


async def _puter_with_retry(system: str, user: str, max_tokens: int) -> str:
    """Call Puter Bridge with retry logic for transient failures."""
    last_error = None
    
    for attempt in range(PUTER_MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    f"{PUTER_BRIDGE_URL}/complete",
                    json={"system": system, "user": user, "max_tokens": max_tokens},
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                data = response.json()
                
                if "error" in data:
                    raise Exception(data["error"])
                if "text" not in data:
                    raise Exception(f"Unexpected response format: {data}")
                    
                return data["text"]
                
        except httpx.ConnectError:
            last_error = "Bridge not reachable (is it running?)"
            if attempt < PUTER_MAX_RETRIES:
                await asyncio.sleep(PUTER_RETRY_DELAY)
                continue
            raise
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise Exception("Puter token invalid or expired. Refresh via puter.com console.")
            if e.response.status_code == 429:
                raise Exception("Puter rate limit reached. Wait ~60s and retry.")
            last_error = f"Puter HTTP {e.response.status_code}"
            if attempt < PUTER_MAX_RETRIES:
                await asyncio.sleep(PUTER_RETRY_DELAY)
                continue
            raise
        except Exception as e:
            last_error = str(e)
            if attempt < PUTER_MAX_RETRIES and "timeout" in str(e).lower():
                await asyncio.sleep(PUTER_RETRY_DELAY)
                continue
            raise
    
    raise Exception(f"Puter Bridge failed after retries: {last_error}")


async def _anthropic(system: str, user: str, max_tokens: int) -> str:
    """Call Anthropic Claude API directly."""
    headers = {
        "Content-Type": "application/json",
        "x-api-key": settings.anthropic_api_key,
        "anthropic-version": "2023-06-01",
        "anthropic-beta": "prompt-caching-2024-07-31",  # optional: enable caching
    }
    payload = {
        "model": settings.anthropic_model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "temperature": 0.3,
    }
    
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        
    return data["content"][0]["text"]


async def _ollama(system: str, user: str) -> str:
    """Call local Ollama instance."""
    payload = {
        "model": settings.ollama_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.4,
        "stream": False,
    }
    
    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            settings.ollama_api_url,
            headers={"Content-Type": "application/json"},
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        
    # Handle both OpenAI-compatible and native Ollama response formats
    if "choices" in data:  # OpenAI-compatible
        return data["choices"][0]["message"]["content"]
    elif "message" in data:  # Native Ollama
        return data["message"]["content"]
    else:
        raise Exception(f"Unexpected Ollama response: {data}")


async def complete(system: str, user: str, max_tokens: int = 2048) -> str:
    """
    Main entry point: route to best available LLM.
    Never raises — always returns a user-friendly string.
    """
    puter_token = getattr(settings, "puter_auth_token", "").strip()
    
    # ── 1. Try Puter Bridge (free) ─────────────────────────────
    if puter_token:
        try:
            logger.debug("→ Using Puter Bridge")
            return await _puter_with_retry(system, user, max_tokens)
        except Exception as e:
            logger.warning(f"Puter Bridge failed: {e}")
            # Fall through to next option
    
    # ── 2. Try Anthropic API (paid) ────────────────────────────
    if settings.anthropic_api_key:
        try:
            logger.debug("→ Using Anthropic API")
            return await _anthropic(system, user, max_tokens)
        except httpx.HTTPStatusError as e:
            msg = _parse_anthropic_error(e.response.text, e.response.status_code)
            logger.warning(f"Anthropic API error: {e.response.status_code}")
            
            # If Anthropic fails but Ollama is available, try it as emergency fallback
            if "localhost" in settings.ollama_api_url:
                try:
                    logger.debug("→ Emergency fallback: Ollama")
                    return await _ollama(system, user)
                except Exception:
                    pass  # Return Anthropic error message below
            return msg
        except Exception as e:
            logger.warning(f"Anthropic exception: {e}")
            # Try Ollama as fallback
            try:
                return await _ollama(system, user)
            except Exception:
                return (
                    "❌ All LLMs unavailable.\n\n"
                    "🔧 Troubleshooting:\n"
                    "• Puter Bridge: Ensure `node puter_bridge/server.js` is running\n"
                    "• Anthropic: Check API key & credits at console.anthropic.com\n"
                    "• Ollama: Run `ollama pull mistral && ollama serve`"
                )
    
    # ── 3. Fall back to Ollama (local) ─────────────────────────
    try:
        logger.debug("→ Using Ollama (local)")
        return await _ollama(system, user)
    except httpx.ConnectError:
        return (
            "❌ No LLM backend available.\n\n"
            "✨ *Free setup (recommended)*:\n"
            "1. Sign up at https://puter.com (free)\n"
            "2. Get token: DevTools → Console → `puter.auth.getToken()`\n"
            "3. Add to `.env`: `PUTER_AUTH_TOKEN=your_token`\n"
            "4. Run bridge: `node puter_bridge/server.js`\n\n"
            "💰 *Paid alternative*:\n"
            "Add credits at https://console.anthropic.com/settings/billing"
        )
    except Exception as e:
        return f"❌ Ollama error: {e}"


async def check_puter_bridge() -> bool:
    """Health check: is Puter Bridge running and authenticated?"""
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.get(f"{PUTER_BRIDGE_URL}/health")
            if response.status_code != 200:
                return False
            data = response.json()
            return data.get("token_set") is True
    except Exception:
        return False


async def get_active_provider() -> str:
    """Return which LLM provider is currently active (for logging/debug)."""
    if await check_puter_bridge():
        return "puter"
    if getattr(settings, "anthropic_api_key", ""):
        return "anthropic"
    if "localhost" in getattr(settings, "ollama_api_url", ""):
        return "ollama"
    return "none"
