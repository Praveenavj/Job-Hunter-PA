from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import httpx

from app.config import settings
from app.services.gmail_connection_store import gmail_connection_store


class GmailOAuthService:
    SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
    AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"

    def _sign(self, payload_b64: str) -> str:
        digest = hmac.new(
            settings.app_secret_key.encode("utf-8"),
            payload_b64.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")

    def _create_state(self, telegram_user_id: int) -> str:
        payload = {
            "telegram_user_id": telegram_user_id,
            "exp": int(time.time()) + 900,
        }
        payload_json = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        payload_b64 = base64.urlsafe_b64encode(payload_json).decode("utf-8").rstrip("=")
        signature = self._sign(payload_b64)
        return f"{payload_b64}.{signature}"

    def _verify_state(self, state: str) -> int:
        try:
            payload_b64, signature = state.split(".", 1)
        except ValueError as exc:
            raise ValueError("Invalid state format.") from exc

        expected = self._sign(payload_b64)
        if not hmac.compare_digest(signature, expected):
            raise ValueError("Invalid state signature.")

        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            raise ValueError("State has expired.")

        return int(payload["telegram_user_id"])

    def get_connect_url(self, telegram_user_id: int) -> str:
        if not settings.gmail_client_id or not settings.gmail_client_secret:
            raise ValueError("Gmail OAuth is not configured. Set GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET.")

        state = self._create_state(telegram_user_id)
        query = urlencode(
            {
                "client_id": settings.gmail_client_id,
                "redirect_uri": settings.oauth_redirect_url,
                "response_type": "code",
                "scope": " ".join(self.SCOPES),
                "access_type": "offline",
                "include_granted_scopes": "true",
                "prompt": "consent",
                "state": state,
            }
        )
        return f"{self.AUTH_URL}?{query}"

    async def complete_oauth_callback(self, code: str, state: str) -> tuple[int, str]:
        telegram_user_id = self._verify_state(state)

        async with httpx.AsyncClient(timeout=30) as client:
            token_resp = await client.post(
                self.TOKEN_URL,
                data={
                    "code": code,
                    "client_id": settings.gmail_client_id,
                    "client_secret": settings.gmail_client_secret,
                    "redirect_uri": settings.oauth_redirect_url,
                    "grant_type": "authorization_code",
                },
            )
            token_resp.raise_for_status()
            token_data = token_resp.json()

            access_token = token_data.get("access_token")
            refresh_token = token_data.get("refresh_token")
            if not access_token:
                raise ValueError("OAuth token response missing access_token.")

            connected, current_email = gmail_connection_store.is_connected(telegram_user_id)
            if not refresh_token and connected:
                stored = gmail_connection_store.get_connection(telegram_user_id)
                if stored:
                    _, refresh_token = stored

            if not refresh_token:
                raise ValueError("OAuth token response missing refresh_token.")

            profile_resp = await client.get(
                self.PROFILE_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            profile_resp.raise_for_status()
            profile_data = profile_resp.json()
            sender_email = profile_data.get("emailAddress") or current_email or "unknown"

        gmail_connection_store.upsert_connection(
            telegram_user_id=telegram_user_id,
            sender_email=sender_email,
            refresh_token=refresh_token,
            scopes=" ".join(self.SCOPES),
        )

        return telegram_user_id, sender_email

    def get_status(self, telegram_user_id: int) -> tuple[bool, str | None]:
        return gmail_connection_store.is_connected(telegram_user_id)

    def disconnect(self, telegram_user_id: int) -> None:
        gmail_connection_store.delete_connection(telegram_user_id)


gmail_oauth_service = GmailOAuthService()
