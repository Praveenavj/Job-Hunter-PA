#!/usr/bin/env python3
"""Test backend LLM integration"""
import asyncio, httpx, sys

BACKEND = "http://localhost:8000"

async def test_email_draft():
    async with httpx.AsyncClient(timeout=60) as client:
        try:
            r = await client.post(
                f"{BACKEND}/email/draft",
                json={
                    "purpose": "test",
                    "recipient_name": "Test User",
                    "context": "LLM integration test",
                    "tone": "professional"
                }
            )
            print(f"Status: {r.status_code}")
            print(f"Response: {r.json()}")
            return r.status_code == 200
        except Exception as e:
            print(f"❌ Error: {e}")
            return False

if __name__ == "__main__":
    ok = asyncio.run(test_email_draft())
    sys.exit(0 if ok else 1)