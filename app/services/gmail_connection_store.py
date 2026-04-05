from __future__ import annotations

import base64
import hashlib
import sqlite3
from pathlib import Path

from cryptography.fernet import Fernet

from app.config import settings


class GmailConnectionStore:
    def __init__(self) -> None:
        self.db_path = Path(settings.sqlite_db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._fernet = Fernet(self._derive_fernet_key(settings.app_secret_key))

    @staticmethod
    def _derive_fernet_key(secret: str) -> bytes:
        digest = hashlib.sha256(secret.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS gmail_connections (
                    telegram_user_id INTEGER PRIMARY KEY,
                    sender_email TEXT NOT NULL,
                    refresh_token_encrypted TEXT NOT NULL,
                    scopes TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

    def upsert_connection(
        self,
        telegram_user_id: int,
        sender_email: str,
        refresh_token: str,
        scopes: str,
    ) -> None:
        encrypted = self._fernet.encrypt(refresh_token.encode("utf-8")).decode("utf-8")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO gmail_connections (telegram_user_id, sender_email, refresh_token_encrypted, scopes)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(telegram_user_id)
                DO UPDATE SET
                    sender_email=excluded.sender_email,
                    refresh_token_encrypted=excluded.refresh_token_encrypted,
                    scopes=excluded.scopes,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (telegram_user_id, sender_email, encrypted, scopes),
            )

    def get_connection(self, telegram_user_id: int) -> tuple[str, str] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT sender_email, refresh_token_encrypted FROM gmail_connections WHERE telegram_user_id = ?",
                (telegram_user_id,),
            ).fetchone()
        if not row:
            return None

        sender_email, token_enc = row
        refresh_token = self._fernet.decrypt(token_enc.encode("utf-8")).decode("utf-8")
        return sender_email, refresh_token

    def is_connected(self, telegram_user_id: int) -> tuple[bool, str | None]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT sender_email FROM gmail_connections WHERE telegram_user_id = ?",
                (telegram_user_id,),
            ).fetchone()
        if not row:
            return False, None
        return True, row[0]

    def delete_connection(self, telegram_user_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM gmail_connections WHERE telegram_user_id = ?", (telegram_user_id,))


gmail_connection_store = GmailConnectionStore()
