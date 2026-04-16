"""
LLM Client v2.0 — Three-provider cascade
=========================================
Priority order:
  1. Puter Bridge  → FREE Claude via puter.com (no API key cost)
                     Requires: PUTER_AUTH_TOKEN in .env + node puter_bridge/server.js running
  2. Anthropic API → Paid Claude (falls back if Puter is unavailable)
                     Requires: ANTHROPIC_API_KEY in .env with credits
  3. Ollama        → Local open-source models (last resort)
                     Requires: Ollama running on localhost:11434

This function NEVER raises exceptions — it always returns a string.
On failure, it returns a user-friendly error message.

HOW THE PUTER PATH WORKS:
  Python → POST http://localhost:3456/complete → Node.js bridge server
         → Puter API (api.puter.com) → Claude → response back
  The bridge server (puter_bridge/server.js) handles the Puter protocol.
  You must have it running: node puter_bridge/server.js
"""
import httpx
import json
import logging
from app.config import settings

logger = logging.getLogger(__name__)

PUTER_BRIDGE_URL = settings.puter_bridge_url

def _parse_anthropic_error(response_text: str, status_code: int) -> str:
    """Convert Anthropic HTTP errors into friendly messages."""
    try:
        data = json.loads(response_text)
        msg = data.get("error", {}).get("message", "")
        if "credit_balance" in msg or "too low" in msg:
            return (
                "❌ *Anthropic API credit balance too low.*\n\n"
                "Top up at: https://console.anthropic.com/settings/billing\n\n"
                "_Note: claude.ai Pro subscription ≠ API credits. They are separate._\n\n"
                "💡 *Free alternative:* Set up the Puter Bridge instead!\n"
                "Add `PUTER_AUTH_TOKEN` to .env and run `node puter_bridge/server.js`"
            )
        if "invalid_api_key" in msg or "authentication" in msg:
            return "❌ Invalid ANTHROPIC_API_KEY. Check your .env file."
        if "overloaded" in msg:
            return "❌ Anthropic is overloaded right now. Wait 30s and try again."
        if msg:
            return f"❌ Anthropic error ({status_code}): {msg[:200]}"
    except Exception:
        pass
    return f"❌ Anthropic HTTP {status_code} error."


async def complete(system: str, user: str, max_tokens: int = 2048) -> str:
    """
    Call LLM with 3-provider cascade: Puter Bridge → Anthropic → Ollama.
    Never raises. Always returns a string (response or friendly error).
    """

    # ── 1. Puter Bridge (free — check if token is set AND bridge is running) ──
    puter_token = getattr(settings, "puter_auth_token", "")
    if puter_token:
        try:
            result = await _puter_bridge(system, user, max_tokens)
            logger.info("LLM via Puter Bridge ✓")
            return result
        except httpx.ConnectError:
            logger.warning("Puter Bridge not running (ConnectError) — falling back to Anthropic")
        except httpx.HTTPStatusError as e:
            logger.warning(f"Puter Bridge HTTP {e.response.status_code} — falling back")
        except Exception as e:
            logger.warning(f"Puter Bridge failed: {e} — falling back to Anthropic")

    # ── 2. Anthropic API (paid) ───────────────────────────────────────────────
    if settings.anthropic_api_key:
        try:
            result = await _anthropic(system, user, max_tokens)
            logger.info("LLM via Anthropic API ✓")
            return result
        except httpx.HTTPStatusError as e:
            msg = _parse_anthropic_error(e.response.text, e.response.status_code)
            logger.warning(f"Anthropic {e.response.status_code}: {e.response.text[:200]}")
            # Before giving up, try Ollama
            try:
                result = await _ollama(system, user)
                logger.info("LLM via Ollama (Anthropic fallback) ✓")
                return result
            except Exception:
                pass
            return msg
        except Exception as e:
            logger.warning(f"Anthropic exception ({e}) — trying Ollama")
            try:
                result = await _ollama(system, user)
                logger.info("LLM via Ollama (Anthropic exception fallback) ✓")
                return result
            except Exception as e2:
                return (
                    f"❌ All LLM providers failed.\n"
                    f"• Anthropic: {e}\n"
                    f"• Ollama: {e2}\n\n"
                    "Check ANTHROPIC_API_KEY credits or set up Puter Bridge."
                )

    # ── 3. Ollama only (no keys set at all) ──────────────────────────────────
    try:
        result = await _ollama(system, user)
        logger.info("LLM via Ollama ✓")
        return result
    except httpx.ConnectError:
        return (
            "❌ No LLM configured.\n\n"
            "*Option A — Free (Puter Bridge):*\n"
            "1. Sign up at https://puter.com (free)\n"
            "2. Open DevTools → Console → run:\n"
            "   `puter.auth.getToken().then(t => console.log(t))`\n"
            "3. Copy token → add to .env: `PUTER_AUTH_TOKEN=xxx`\n"
            "4. Run: `node puter_bridge/server.js`\n\n"
            "*Option B — Paid (Anthropic):*\n"
            "Add `ANTHROPIC_API_KEY` to .env with credits."
        )
    except Exception as e:
        return f"❌ Ollama error: {e}"


# ── Provider implementations ──────────────────────────────────────────────────

async def _puter_bridge(system: str, user: str, max_tokens: int) -> str:
    """
    POST to the local Puter Bridge server (Node.js, port 3456).
    The bridge translates this into a Puter API call and returns Claude's text.
    Raises on any HTTP error so the caller can fall back.
    """
    async with httpx.AsyncClient(timeout=90) as client:
        r = await client.post(
            f"{PUTER_BRIDGE_URL}/complete",
            json={"system": system, "user": user, "max_tokens": max_tokens},
        )
        r.raise_for_status()
    data = r.json()
    if "error" in data:
        raise Exception(f"Bridge returned error: {data['error']}")
    if "text" not in data:
        raise Exception(f"Bridge returned unexpected shape: {list(data.keys())}")
    return data["text"]


async def _anthropic(system: str, user: str, max_tokens: int) -> str:
    """Direct Anthropic Claude API call (paid)."""
    headers = {
        "Content-Type":    "application/json",
        "x-api-key":       settings.anthropic_api_key,
        "anthropic-version": "2023-06-01",
    }
    payload = {
        "model":      settings.anthropic_model,
        "max_tokens": max_tokens,
        "system":     system,
        "messages":   [{"role": "user", "content": user}],
    }
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers=headers,
            json=payload,
        )
        r.raise_for_status()
    return r.json()["content"][0]["text"]


async def _ollama(system: str, user: str) -> str:
    """Local Ollama model call (last resort, requires Ollama running)."""
    payload = {
        "model":   settings.ollama_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
        "temperature": 0.4,
    }
    async with httpx.AsyncClient(timeout=120) as client:
        r = await client.post(
            settings.ollama_api_url,
            headers={"Content-Type": "application/json"},
            json=payload,
        )
        r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


async def check_puter_bridge() -> bool:
    """Quick health check — is the bridge running?"""
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{PUTER_BRIDGE_URL}/health")
            return r.status_code == 200 and r.json().get("token_set", False)
    except Exception:
        return False