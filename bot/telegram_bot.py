from __future__ import annotations

import asyncio
import json
import shlex

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

from app.config import settings


def _help_text() -> str:
    return (
        "Job Hunter Personal Assistant\n\n"
        "1. See jobs available\n"
        "2. Revise resume\n"
        "3. Draft email\n"
        "4. Track job on Notion\n"
        "5. Prepare for interviews\n\n"
        "Commands:\n"
        "/jobs <role> | <location> | <limit>\n"
        "/resume <target_role> || <resume_text> || <skill1,skill2>\n"
        "/email <purpose> || <recipient_name> || <context> || <tone>\n"
        "/outreach <to_email> || <recipient_name> || <role> || <company> || <resume_text> || <tone> || <send_now:true|false>\n"
        "or\n"
        "/outreach \"to_email\" \"recipient_name\" \"role\" \"company\" \"resume_text\" \"tone\" \"true|false\"\n"
        "/gmail_connect\n"
        "/gmail_status\n"
        "/gmail_disconnect\n"
        "/track <company> || <role> || <status> || <link> || <notes>\n"
        "/interview <role> || <company> || <focus1,focus2>\n"
        "/help"
    )


def _main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="1. See jobs available")],
            [KeyboardButton(text="2. Revise resume")],
            [KeyboardButton(text="3. Draft email")],
            [KeyboardButton(text="4. Track job on Notion")],
            [KeyboardButton(text="5. Prepare for interviews")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


async def api_post(path: str, payload: dict) -> dict:
    url = f"{settings.backend_base_url.rstrip('/')}{path}"
    async with httpx.AsyncClient(timeout=90) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return response.json()


async def api_get(path: str) -> dict:
    url = f"{settings.backend_base_url.rstrip('/')}{path}"
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()


async def main() -> None:
    if not settings.telegram_bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN is missing. Add it in .env")

    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()

    @dp.message(CommandStart())
    async def start_handler(message: Message) -> None:
        await message.answer("Welcome.\n\n" + _help_text(), reply_markup=_main_menu_keyboard())

    @dp.message(Command("help"))
    async def help_handler(message: Message) -> None:
        await message.answer(_help_text(), reply_markup=_main_menu_keyboard())

    @dp.message(F.text == "1. See jobs available")
    async def menu_jobs(message: Message) -> None:
        await message.answer("Use: /jobs <role> | <location> | <limit>\nExample: /jobs backend engineer | remote | 5")

    @dp.message(F.text == "2. Revise resume")
    async def menu_resume(message: Message) -> None:
        await message.answer(
            "Use: /resume <target_role> || <resume_text> || <skill1,skill2>\n"
            "Example: /resume Data Analyst || I have 2 years in BI... || SQL,Python,Tableau"
        )

    @dp.message(F.text == "3. Draft email")
    async def menu_email(message: Message) -> None:
        await message.answer(
            "Draft only:\n"
            "/outreach <to_email> || <recipient_name> || <role> || <company> || <resume_text> || <tone> || false\n\n"
            "Draft + send via Gmail API:\n"
            "/outreach <to_email> || <recipient_name> || <role> || <company> || <resume_text> || <tone> || true\n\n"
            "Before sending, run /gmail_connect once to link your own Gmail account."
        )

    @dp.message(F.text == "4. Track job on Notion")
    async def menu_track(message: Message) -> None:
        await message.answer("Use: /track <company> || <role> || <status> || <link> || <notes>")

    @dp.message(F.text == "5. Prepare for interviews")
    async def menu_interview(message: Message) -> None:
        await message.answer("Use: /interview <role> || <company> || <focus1,focus2>")

    @dp.message(Command("health"))
    async def health_handler(message: Message) -> None:
        data = await api_get("/health")
        await message.answer(f"Backend status: {data.get('status', 'unknown')}")

    @dp.message(Command("jobs"))
    async def jobs_handler(message: Message) -> None:
        try:
            raw = (message.text or "").replace("/jobs", "", 1).strip()
            role, location, limit = [x.strip() for x in raw.split("|")]
            payload = {"role": role, "location": location, "limit": int(limit)}
            data = await api_post("/jobs/search", payload)

            jobs = data.get("jobs", [])
            if not jobs:
                await message.answer("No jobs found.")
                return

            lines = ["Top jobs:"]
            for idx, job in enumerate(jobs, start=1):
                lines.append(
                    f"{idx}. {job.get('title')} at {job.get('company')}\n"
                    f"   {job.get('location')} | {job.get('type')}\n"
                    f"   {job.get('url')}"
                )
            await message.answer("\n\n".join(lines))
        except Exception:
            await message.answer("Usage: /jobs <role> | <location> | <limit>")

    @dp.message(Command("resume"))
    async def resume_handler(message: Message) -> None:
        try:
            raw = (message.text or "").replace("/resume", "", 1).strip()
            target_role, resume_text, skills_csv = [x.strip() for x in raw.split("||")]
            payload = {
                "target_role": target_role,
                "current_resume": resume_text,
                "key_skills": [s.strip() for s in skills_csv.split(",") if s.strip()],
            }
            data = await api_post("/resume/revise", payload)
            await message.answer(data.get("text", "No response."))
        except Exception:
            await message.answer("Usage: /resume <target_role> || <resume_text> || <skill1,skill2>")

    @dp.message(Command("email"))
    async def email_handler(message: Message) -> None:
        try:
            raw = (message.text or "").replace("/email", "", 1).strip()
            purpose, recipient_name, context, tone = [x.strip() for x in raw.split("||")]
            payload = {
                "purpose": purpose,
                "recipient_name": recipient_name,
                "context": context,
                "tone": tone,
            }
            data = await api_post("/email/draft", payload)
            await message.answer(data.get("text", "No response."))
        except Exception:
            await message.answer("Usage: /email <purpose> || <recipient_name> || <context> || <tone>")

    @dp.message(Command("outreach"))
    async def outreach_handler(message: Message) -> None:
        usage_text = (
            "Usage:\n"
            "/outreach <to_email> || <recipient_name> || <role> || <company> || "
            "<resume_text> || <tone> || <send_now:true|false>\n\n"
            "Example:\n"
            "/outreach sally@gmail.com || Sally || Intern || Google || "
            "CS student with Python, FastAPI, Telegram bot projects. || polite || true\n\n"
            "Alternative (quoted):\n"
            "/outreach \"sally@gmail.com\" \"Sally\" \"Intern\" \"Google\" "
            "\"CS student with Python and FastAPI\" \"polite\" \"true\""
        )

        try:
            if not message.from_user:
                await message.answer("Could not determine Telegram user.")
                return

            raw = (message.text or "").replace("/outreach", "", 1).strip()
            if not raw:
                raise ValueError("Missing outreach arguments")

            if "||" in raw:
                parts = [x.strip() for x in raw.split("||")]
                if len(parts) != 7:
                    raise ValueError("Invalid outreach argument count")
                to_email, recipient_name, role, company, resume_text, tone, send_now = parts
            else:
                parts = shlex.split(raw)
                if len(parts) != 7:
                    raise ValueError("Invalid outreach argument count")
                to_email, recipient_name, role, company, resume_text, tone, send_now = parts

            payload = {
                "telegram_user_id": message.from_user.id,
                "to_email": to_email,
                "recipient_name": recipient_name,
                "role": role,
                "company": company,
                "resume_text": resume_text,
                "tone": tone,
                "send_now": send_now.lower() in {"true", "1", "yes", "y"},
            }
            data = await api_post("/email/outreach", payload)
            response_text = (
                f"Status: {data.get('status')}\n"
                f"Sent: {data.get('sent')}\n"
                f"Message ID: {data.get('message_id')}\n\n"
                f"Subject: {data.get('subject')}\n\n"
                f"{data.get('body')}"
            )
            connect_url = data.get("connect_url")
            if connect_url:
                response_text += f"\n\nConnect Gmail: {connect_url}"
            await message.answer(response_text)
        except ValueError:
            await message.answer(usage_text)
        except httpx.HTTPStatusError as exc:
            detail = "Failed to process outreach request."
            try:
                body = exc.response.json()
                detail = body.get("detail", detail)
            except json.JSONDecodeError:
                pass
            await message.answer(f"{detail}\n\nIf needed:\n{usage_text}")
        except Exception as exc:
            await message.answer(f"Failed to process outreach request: {exc}\n\nIf needed:\n{usage_text}")

    @dp.message(Command("gmail_connect"))
    async def gmail_connect_handler(message: Message) -> None:
        try:
            if not message.from_user:
                await message.answer("Could not determine Telegram user.")
                return

            data = await api_get(f"/gmail/connect-link?telegram_user_id={message.from_user.id}")
            await message.answer(f"Connect your Gmail account: {data.get('connect_url')}")
        except httpx.HTTPStatusError as exc:
            detail = "Failed to create Gmail connect link."
            try:
                body = exc.response.json()
                detail = body.get("detail", detail)
            except json.JSONDecodeError:
                pass
            await message.answer(f"Failed to create Gmail connect link: {detail}")
        except Exception:
            await message.answer("Failed to create Gmail connect link. Check backend OAuth config.")

    @dp.message(Command("gmail_status"))
    async def gmail_status_handler(message: Message) -> None:
        try:
            if not message.from_user:
                await message.answer("Could not determine Telegram user.")
                return

            data = await api_get(f"/gmail/status/{message.from_user.id}")
            await message.answer(
                f"Connected: {data.get('connected')}\n"
                f"Sender: {data.get('sender_email')}"
            )
        except httpx.HTTPStatusError as exc:
            detail = "Failed to check Gmail status."
            try:
                body = exc.response.json()
                detail = body.get("detail", detail)
            except json.JSONDecodeError:
                pass
            await message.answer(detail)
        except Exception:
            await message.answer("Failed to check Gmail status.")

    @dp.message(Command("gmail_disconnect"))
    async def gmail_disconnect_handler(message: Message) -> None:
        try:
            if not message.from_user:
                await message.answer("Could not determine Telegram user.")
                return

            data = await api_post("/gmail/disconnect", {"telegram_user_id": message.from_user.id})
            await message.answer(
                f"Connected: {data.get('connected')}\n"
                "Your Gmail has been disconnected."
            )
        except httpx.HTTPStatusError as exc:
            detail = "Failed to disconnect Gmail."
            try:
                body = exc.response.json()
                detail = body.get("detail", detail)
            except json.JSONDecodeError:
                pass
            await message.answer(detail)
        except Exception:
            await message.answer("Failed to disconnect Gmail.")

    @dp.message(Command("track"))
    async def track_handler(message: Message) -> None:
        try:
            raw = (message.text or "").replace("/track", "", 1).strip()
            company, role, status, link, notes = [x.strip() for x in raw.split("||")]
            payload = {
                "company": company,
                "role": role,
                "status": status,
                "link": link or None,
                "notes": notes or None,
            }
            data = await api_post("/notion/track", payload)
            await message.answer(f"{data.get('message')} Page ID: {data.get('page_id')}")
        except Exception:
            await message.answer("Usage: /track <company> || <role> || <status> || <link> || <notes>")

    @dp.message(Command("interview"))
    async def interview_handler(message: Message) -> None:
        try:
            raw = (message.text or "").replace("/interview", "", 1).strip()
            role, company, focus_csv = [x.strip() for x in raw.split("||")]
            payload = {
                "role": role,
                "company": company,
                "focus_areas": [s.strip() for s in focus_csv.split(",") if s.strip()],
            }
            data = await api_post("/interview/prepare", payload)
            await message.answer(data.get("text", "No response."))
        except Exception:
            await message.answer("Usage: /interview <role> || <company> || <focus1,focus2>")

    @dp.message(F.text)
    async def fallback_handler(message: Message) -> None:
        await message.answer("Unknown command. Use /help")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
