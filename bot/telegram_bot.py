from __future__ import annotations

import asyncio
import base64
import json
import shlex

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, Message, ReplyKeyboardMarkup

from app.config import settings


PENDING_OUTREACH_BY_USER: dict[int, dict] = {}
PENDING_OUTREACH_CONFIRMATION_BY_USER: dict[int, dict] = {}
PENDING_RESUME_CHOICE_BY_USER: dict[int, dict] = {}
LAST_RESUME_PDF_BY_USER: dict[int, dict] = {}
JOB_SEARCH_STATE_BY_USER: dict[int, dict] = {}


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
        "/outreach <to_email> || <recipient_name> || <role> || <company>\n"
        "(tone is fixed to polite; always draft first)\n"
        "/gmail_connect\n"
        "/gmail_status\n"
        "/gmail_disconnect\n"
        "/track <company> || <role> || <status> || <link> || <notes>\n"
        "/interview <role> || <company> || <focus1,focus2>\n"
        "/help"
        "/tailor <job_title> || <company> || <job_description> || <resume_text>\n"
        "/status\n"
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

    async def _send_outreach_with_resume(pending: dict, resume_filename: str, resume_bytes: bytes) -> str:
        payload = {
            **pending,
            "resume_text": "Please refer to the attached resume PDF for full details.",
            "resume_filename": resume_filename or "resume.pdf",
            "resume_file_base64": base64.b64encode(resume_bytes).decode("utf-8"),
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
        return response_text

    def _job_results_keyboard(jobs: list[dict]) -> InlineKeyboardMarkup:
        rows = []
        for idx, job in enumerate(jobs, start=1):
            url = job.get("url")
            title = job.get("title") or f"Job {idx}"
            company = job.get("company") or "Unknown company"
            label = f"Open {idx}: {title} at {company}"
            if url:
                rows.append([InlineKeyboardButton(text=label, url=url)])
        return InlineKeyboardMarkup(inline_keyboard=rows)

    async def _show_jobs_results(message: Message, role: str, location: str, limit: int) -> None:
        payload = {"role": role, "location": location, "limit": limit}
        data = await api_post("/jobs/search", payload)
        jobs = data.get("jobs", [])

        if not jobs:
            await message.answer(
                f"No jobs found for {role} in {location}.\n\n"
                "Current source: Remotive, which is mainly a remote-jobs API. "
                "Try location=remote or a broader location like worldwide/EMEA."
            )
            return

        lines = [f"Top {len(jobs)} jobs for {role}:", ""]
        for idx, job in enumerate(jobs, start=1):
            job_type = job.get('type')
            entry = f"{idx}. {job.get('title')} at {job.get('company')}"
            if job_type:
                entry += f"\n   {job_type}"
            lines.append(entry)

        await message.answer("\n".join(lines), reply_markup=_job_results_keyboard(jobs))

    @dp.message(CommandStart())
    async def start_handler(message: Message) -> None:
        await message.answer("Welcome.\n\n" + _help_text(), reply_markup=_main_menu_keyboard())

    @dp.message(Command("help"))
    async def help_handler(message: Message) -> None:
        await message.answer(_help_text(), reply_markup=_main_menu_keyboard())

    @dp.message(F.text == "1. See jobs available")
    async def menu_jobs(message: Message) -> None:
        if not message.from_user:
            await message.answer("Could not determine Telegram user.")
            return

        JOB_SEARCH_STATE_BY_USER[message.from_user.id] = {"step": "role"}
        await message.answer("What role are you searching for?")

    @dp.message(F.text == "2. Revise resume")
    async def menu_resume(message: Message) -> None:
        await message.answer(
            "Please upload your resume as a PDF file.\n\n"
            "I will read it and ask you what role you're targeting."
        )
        # Set state so the next PDF upload is handled as a resume revision
        if message.from_user:
            PENDING_RESUME_CHOICE_BY_USER[message.from_user.id] = {"mode": "revise"}

    @dp.message(F.text == "3. Draft email")
    async def menu_email(message: Message) -> None:
        await message.answer(
            "Use:\n"
            "/outreach <to_email> || <recipient_name> || <role> || <company>\n\n"
            "Behavior:\n"
            "1) Bot drafts outreach first (tone: polite).\n"
            "2) Bot asks for confirmation (YES/NO).\n"
            "3) If YES, upload resume PDF and it sends via Gmail API.\n\n"
            "Before sending, run /gmail_connect once to link your own Gmail account.\n"
            "Resume PDF is required for all sent outreach emails."
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
            if not raw:
                if not message.from_user:
                    await message.answer("Could not determine Telegram user.")
                    return

                JOB_SEARCH_STATE_BY_USER[message.from_user.id] = {"step": "role"}
                await message.answer("What role are you searching for?")
                return

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

    @dp.message(F.text)
    async def job_search_flow_handler(message: Message) -> None:
        if not message.from_user:
            return

        state = JOB_SEARCH_STATE_BY_USER.get(message.from_user.id)
        if not state:
            return

        text = (message.text or "").strip()
        if not text:
            return

        step = state.get("step")

        if step == "role":
            state["role"] = text
            state["step"] = "location"
            await message.answer("What location do you want? Example: Singapore")
            return

        if step == "location":
            state["location"] = text
            state["step"] = "limit"
            await message.answer("How many results do you want? Example: 5")
            return

        if step == "limit":
            try:
                limit = max(1, min(20, int(text)))
            except ValueError:
                await message.answer("Please enter a number between 1 and 20.")
                return

            role = state.get("role", "")
            location = state.get("location", "remote")
            JOB_SEARCH_STATE_BY_USER.pop(message.from_user.id, None)
            await _show_jobs_results(message, role, location, limit)
            return

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
            "/outreach <to_email> || <recipient_name> || <role> || <company>\n\n"
            "Example:\n"
            "/outreach sally@gmail.com || Sally || Intern || Google\n\n"
            "Alternative (quoted):\n"
            "/outreach \"sally@gmail.com\" \"Sally\" \"Intern\" \"Google\"\n\n"
            "Tone is fixed to polite. Bot always drafts first and asks YES/NO before sending."
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
                if len(parts) != 4:
                    raise ValueError("Invalid outreach argument count")
                to_email, recipient_name, role, company = parts
            else:
                parts = shlex.split(raw)
                if len(parts) != 4:
                    raise ValueError("Invalid outreach argument count")
                to_email, recipient_name, role, company = parts

            tone = "polite"

            payload = {
                "telegram_user_id": message.from_user.id,
                "to_email": to_email,
                "recipient_name": recipient_name,
                "role": role,
                "company": company,
                "resume_text": "Resume is available as PDF and can be shared when requested.",
                "sender_full_name": message.from_user.full_name,
                "tone": tone,
                "send_now": False,
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
            PENDING_OUTREACH_CONFIRMATION_BY_USER[message.from_user.id] = {
                "telegram_user_id": message.from_user.id,
                "to_email": to_email,
                "recipient_name": recipient_name,
                "role": role,
                "company": company,
                "sender_full_name": message.from_user.full_name,
                "tone": tone,
                "send_now": True,
            }
            await message.answer("Send this now? Reply YES to continue or NO to cancel.")
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

    @dp.message(F.document)
    async def resume_pdf_upload_handler(message: Message) -> None:
        if not message.from_user:
            return

        document = message.document
        if not document:
            return

        file_name = (document.file_name or "").lower()
        mime_type = (document.mime_type or "").lower()
        if not (file_name.endswith(".pdf") or mime_type == "application/pdf"):
            await message.answer("Please upload a PDF file (.pdf).")
            return

        try:
            telegram_file = await bot.get_file(document.file_id)
            downloaded = await bot.download_file(telegram_file.file_path)
            resume_bytes = downloaded.read()

            # Always save as the latest uploaded resume
            LAST_RESUME_PDF_BY_USER[message.from_user.id] = {
                "filename": document.file_name or "resume.pdf",
                "bytes": resume_bytes,
            }

            # Check if this upload was triggered by "2. Revise resume" menu
            pending_choice = PENDING_RESUME_CHOICE_BY_USER.get(message.from_user.id, {})
            if pending_choice.get("mode") == "revise":
                PENDING_RESUME_CHOICE_BY_USER.pop(message.from_user.id, None)
                await message.answer(
                    "Got your resume! Now tell me:\n\n"
                    "What role are you targeting? Also share the job description if you have one.\n\n"
                    "Use: /resume <target_role> || <resume_text> || <skill1,skill2>\n\n"
                    "Or for JD-aware tailoring:\n"
                    "/tailor <job_title> || <company> || <job_description> || <resume_text>"
                )
                return

            # Otherwise handle as outreach PDF
            pending = PENDING_OUTREACH_BY_USER.get(message.from_user.id)
            if not pending:
                await message.answer(
                    "Resume PDF saved. I'll use this automatically for your next outreach.\n\n"
                    "You can now run /outreach to send a job application email."
                )
                return

            response_text = await _send_outreach_with_resume(
                pending,
                document.file_name or "resume.pdf",
                resume_bytes,
            )
            await message.answer(response_text)
            PENDING_OUTREACH_BY_USER.pop(message.from_user.id, None)

        except Exception as exc:
            await message.answer(f"Failed to process uploaded PDF: {exc}")

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

    @dp.message(Command("tailor"))
    async def tailor_handler(message: Message) -> None:
        usage = (
            "Usage: /tailor <job_title> || <company> || <job_description> || <resume_text>\n\n"
            "Example:\n"
            "/tailor Data Analyst || Google || We need SQL and Python skills... || "
            "I built dashboards using Tableau..."
        )
        try:
            raw = (message.text or "").replace("/tailor", "", 1).strip()
            parts = [x.strip() for x in raw.split("||")]
            if len(parts) != 4:
                raise ValueError("Wrong number of arguments")
            job_title, company, job_description, resume_text = parts
            payload = {
                "job_title": job_title,
                "company": company,
                "job_description": job_description,
                "resume_text": resume_text,
            }
            await message.answer("Tailoring your resume... this may take 20-30 seconds.")
            data = await api_post("/resume/tailor", payload)
            raw_text = data.get("text", "{}")

            import json, re
            clean = re.sub(r"```json|```", "", raw_text).strip()
            parsed = json.loads(clean)

            score = parsed.get("match_score", "N/A")
            keywords = ", ".join(parsed.get("keywords_added", []))
            bullets = parsed.get("rewritten_bullets", [])

            lines = [f"Match score: {score}%", f"Keywords added: {keywords}", ""]
            for i, b in enumerate(bullets, 1):
                lines.append(f"{i}. BEFORE: {b.get('original', '')}")
                lines.append(f"   AFTER:  {b.get('improved', '')}")
                lines.append(f"   WHY:    {b.get('reason', '')}")
                lines.append("")

            await message.answer("\n".join(lines))
        except (ValueError, KeyError):
            await message.answer(usage)
        except Exception as exc:
            await message.answer(f"Failed to tailor resume: {exc}\n\n{usage}")

    @dp.message(Command("status"))
    async def status_handler(message: Message) -> None:
        try:
            data = await api_get("/notion/summary")
            apps = data.get("applications", [])
            total = data.get("total", 0)

            if not apps:
                await message.answer("No applications tracked yet. Use /track to add one.")
                return

            today = __import__("datetime").date.today()
            lines = [f"Your {total} tracked application(s):\n"]
            for app in apps:
                followup = app.get("followup")
                days_left = ""
                if followup:
                    delta = (__import__("datetime").date.fromisoformat(followup) - today).days
                    if delta < 0:
                        days_left = f" — OVERDUE by {abs(delta)}d"
                    elif delta == 0:
                        days_left = " — follow up TODAY"
                    else:
                        days_left = f" — follow up in {delta}d"
                lines.append(
                    f"{app.get('company')} | {app.get('role')} | "
                    f"{app.get('status')}{days_left}"
                )

            await message.answer("\n".join(lines))
        except Exception as exc:
            await message.answer(f"Failed to fetch summary: {exc}")

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
    async def outreach_confirmation_handler(message: Message) -> None:
        if not message.from_user:
            await message.answer("Unknown command. Use /help")
            return

        user_id = message.from_user.id
        text = (message.text or "").strip().lower()

        if user_id in JOB_SEARCH_STATE_BY_USER:
            return

        pending_choice = PENDING_RESUME_CHOICE_BY_USER.get(user_id)
        if pending_choice:
            if text in {"use", "use previous", "previous", "1"}:
                last_resume = LAST_RESUME_PDF_BY_USER.get(user_id)
                if not last_resume:
                    PENDING_RESUME_CHOICE_BY_USER.pop(user_id, None)
                    PENDING_OUTREACH_BY_USER[user_id] = pending_choice
                    await message.answer("No previous resume found. Please upload your resume as a PDF.")
                    return

                try:
                    response_text = await _send_outreach_with_resume(
                        pending_choice,
                        last_resume.get("filename", "resume.pdf"),
                        last_resume.get("bytes", b""),
                    )
                    await message.answer(response_text)
                except Exception as exc:
                    await message.answer(f"Failed to send using previous resume: {exc}")
                finally:
                    PENDING_RESUME_CHOICE_BY_USER.pop(user_id, None)
                return

            if text in {"new", "upload new", "2"}:
                PENDING_OUTREACH_BY_USER[user_id] = pending_choice
                PENDING_RESUME_CHOICE_BY_USER.pop(user_id, None)
                await message.answer("Please upload your new resume as a PDF.")
                return

            await message.answer("Reply USE to send with previous resume, or NEW to upload a new PDF.")
            return

        pending_confirm = PENDING_OUTREACH_CONFIRMATION_BY_USER.get(user_id)
        if pending_confirm:
            if text in {"yes", "y"}:
                PENDING_OUTREACH_CONFIRMATION_BY_USER.pop(user_id, None)
                if user_id in LAST_RESUME_PDF_BY_USER:
                    PENDING_RESUME_CHOICE_BY_USER[user_id] = pending_confirm
                    await message.answer(
                        "I found a previously uploaded resume PDF. Reply USE to use previous, or NEW to upload a new one."
                    )
                else:
                    PENDING_OUTREACH_BY_USER[user_id] = pending_confirm
                    await message.answer("Great. Please upload your resume as a PDF to send this outreach now.")
                return

            if text in {"no", "n"}:
                PENDING_OUTREACH_CONFIRMATION_BY_USER.pop(user_id, None)
                await message.answer("Cancelled. You can run /outreach again anytime.")
                return

            await message.answer("Please reply YES to send now, or NO to cancel.")
            return

        await message.answer("Unknown command. Use /help")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
