from __future__ import annotations

import base64
from email.mime.text import MIMEText
from typing import Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.config import settings
from app.services.gmail_connection_store import gmail_connection_store


class GmailService:
    def is_configured(self) -> bool:
        return bool(settings.gmail_client_id and settings.gmail_client_secret)

    def send_email(
        self,
        telegram_user_id: int,
        to_email: str,
        subject: str,
        body: str,
    ) -> tuple[bool, Optional[str], str]:
        if not self.is_configured():
            return (
                False,
                None,
                "Gmail OAuth is not configured. Set GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET.",
            )

        stored = gmail_connection_store.get_connection(telegram_user_id)
        if not stored:
            return False, None, "Gmail is not connected for this Telegram user."

        sender_email, refresh_token = stored

        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings.gmail_client_id,
            client_secret=settings.gmail_client_secret,
            scopes=["https://www.googleapis.com/auth/gmail.send"],
        )

        service = build("gmail", "v1", credentials=creds, cache_discovery=False)

        message = MIMEText(body)
        message["to"] = to_email
        message["from"] = sender_email
        message["subject"] = subject

        raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        sent_message = service.users().messages().send(userId="me", body={"raw": raw_message}).execute()
        return True, sent_message.get("id"), "Email sent via Gmail API."


gmail_service = GmailService()
