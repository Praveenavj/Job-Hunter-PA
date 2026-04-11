"""
Job Hunter PA – Telegram Bot v3.1 (All bugs fixed)

Changes vs v3:
  - SELECTED_JOB dict: job data stored separately from STATE, never overwritten
  - Outreach prefill: reads from SELECTED_JOB, always has company+role
  - Auto-JD tailor: uses job description snippet automatically, skips JD prompt
  - Track menu extended: includes View Applications + Export options
  - /myapps accessible directly from track menu
  - Credit balance error: shows proper message with billing link
  - Test suite: pip install instructions embedded in error messages
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
from datetime import date, timedelta

import httpx
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile, CallbackQuery,
    InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, Message, ReplyKeyboardMarkup,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app import database as db
from app.config import settings
from app.resume_utils import extract_keywords, extract_text_from_pdf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BACKEND = settings.backend_base_url.rstrip("/")

# ── In-memory state ───────────────────────────────────────────────────────────
STATE:        dict[int, dict] = {}   # conversation state per user
PDF_STORE:    dict[int, bytes] = {}  # raw PDF bytes
JOBS_CACHE:   dict[int, list] = {}   # last job search results
SELECTED_JOB: dict[int, dict] = {}   # ← SEPARATE from STATE so flows don't overwrite it
PRACTICE:     dict[int, dict] = {}   # mock interview state


# ── Keyboards ─────────────────────────────────────────────────────────────────

def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="1. See jobs available")],
            [KeyboardButton(text="2. Revise resume")],
            [KeyboardButton(text="3. Draft email")],
            [KeyboardButton(text="4. Track application")],
            [KeyboardButton(text="5. Prepare for interviews")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def yn_kb(yes_cb: str, no_cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Yes", callback_data=yes_cb),
        InlineKeyboardButton(text="❌ No",  callback_data=no_cb),
    ]])


def track_menu_kb() -> InlineKeyboardMarkup:
    """Extended track menu with view and export options."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add new application",
                              callback_data="track_add_new")],
        [InlineKeyboardButton(text="📋 View all applications",
                              callback_data="track_view_all")],
        [InlineKeyboardButton(text="📊 Download Excel tracker",
                              callback_data="track_export")],
        [InlineKeyboardButton(text="✏️ Update status (/update)",
                              callback_data="track_update_help")],
    ])


# ── HTTP helpers ──────────────────────────────────────────────────────────────

async def api_post(path: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=90) as c:
        r = await c.post(f"{BACKEND}{path}", json=payload)
        r.raise_for_status()
        return r.json()


async def api_get(path: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{BACKEND}{path}")
        r.raise_for_status()
        return r.json()


async def api_bytes(path: str) -> bytes:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{BACKEND}{path}")
        r.raise_for_status()
        return r.content


# ── Message helpers ───────────────────────────────────────────────────────────

async def send_long(msg: Message, text: str, pm: str = "Markdown") -> None:
    for i in range(0, len(text), 3900):
        chunk = text[i: i + 3900]
        try:
            await msg.answer(chunk, parse_mode=pm)
        except Exception:
            await msg.answer(chunk)


def fmt_jobs(jobs: list[dict]) -> str:
    if not jobs:
        return "❌ No new jobs found."
    lines = [f"📋 *Found {len(jobs)} job(s):*\n"]
    for i, j in enumerate(jobs, 1):
        sal = f"\n   💰 {j['salary']}" if j.get("salary") else ""
        typ = f" · {j['job_type']}"   if j.get("job_type") else ""
        dt  = f" · {j['posted_at']}"  if j.get("posted_at") else ""
        lines.append(
            f"*{i}. {j['title']}*\n"
            f"   🏢 {j['company']}{sal}\n"
            f"   📍 {j['location']}{typ}{dt}\n"
            f"   🔗 [{j['source']}]({j['url']})\n"
        )
    return "\n".join(lines)


async def show_myapps(msg: Message, uid: int) -> None:
    """Display tabular application tracker in Telegram."""
    try:
        data = await api_get(f"/applications/{uid}")
        apps = data.get("applications", [])
        if not apps:
            await msg.answer(
                "📊 No applications tracked yet.\n\n"
                "Use '4. Track application' or tap a job → Track application.\n\n"
                "_Tip: After searching for jobs, tap any job and select_ "
                "📊 Track application",
                parse_mode="Markdown",
            )
            return

        from collections import Counter
        counts = Counter(a["status"] for a in apps)
        icons  = {"Applied":"📤","Interviewed":"🎤","Offered":"🎉",
                  "Rejected":"❌","Withdrawn":"↩️"}
        summary = "  ".join(f"{icons.get(s,'•')}{s[:3]}:{n}"
                            for s,n in sorted(counts.items()))
        header = f"📊 *Pipeline ({len(apps)} total)*  {summary}"

        today = date.today()
        table = ["```"]
        table.append(f"{'#':<3} {'Company':<16} {'Role':<14} {'Status':<11} {'Applied'}")
        table.append("─" * 57)
        for i, a in enumerate(apps[:20], 1):
            co     = (a.get("company","")[:15]).ljust(16)
            role   = (a.get("role","")[:13]).ljust(14)
            status = (a.get("status","")[:10]).ljust(11)
            adate  = (a.get("applied_date","")[:10])
            flag   = ""
            fd = a.get("followup_date","")
            if fd and a.get("status") == "Applied":
                delta = (date.fromisoformat(fd) - today).days
                flag  = " ⚠" if delta < 0 else (" ⏰" if delta == 0 else "")
            table.append(f"{i:<3} {co} {role} {status} {adate}{flag}")
        table.append("```")

        footer = []
        if len(apps) > 20:
            footer.append(f"_Showing 20 of {len(apps)} — /export for all_")
        footer.append("_/update ID STATUS  ·  /export for Excel_")

        await msg.answer(
            header + "\n\n" + "\n".join(table) + "\n" + "\n".join(footer),
            parse_mode="Markdown",
        )
    except Exception as e:
        await msg.answer(f"❌ Error loading applications: {e}")


# ── Tailor helper ─────────────────────────────────────────────────────────────

async def _run_tailor(msg: Message, uid: int) -> None:
    state = STATE.get(uid, {})
    try:
        result = await api_post("/resume/tailor", {
            "resume_text":     state.get("resume_text", ""),
            "job_description": state.get("jd_text", "") or state.get("auto_jd", ""),
            "job_title":       state.get("job_title", ""),
            "company":         state.get("company", ""),
        })
        await send_long(msg, result.get("text", "No result."))
    except Exception as e:
        await msg.answer(f"❌ Tailor error: {e}")
    STATE.pop(uid, None)
    await msg.answer("Done! Back to main menu 👇", reply_markup=main_menu())


# ── Scheduler ─────────────────────────────────────────────────────────────────

async def daily_digest(bot: Bot) -> None:
    logger.info("Running daily digest...")
    for uid in db.get_all_active_users():
        try:
            searches = db.get_saved_searches(uid)
            if not searches:
                continue
            new_jobs: list[dict] = []
            for s in searches:
                data = await api_post("/jobs/search", {
                    "role": s["role"], "location": s["location"],
                    "limit": s.get("limit_", 5),
                    "telegram_id": uid, "new_only": True,
                })
                new_jobs.extend(data.get("jobs", []))
            if new_jobs:
                await bot.send_message(
                    uid,
                    f"🌅 *Good morning! Daily digest – {date.today()}*\n\n"
                    + fmt_jobs(new_jobs[:10]),
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                )
            else:
                await bot.send_message(
                    uid, "☀️ *Daily digest:* No new jobs today.",
                    parse_mode="Markdown",
                )
        except Exception as e:
            logger.error(f"Digest uid={uid}: {e}")


async def followup_check(bot: Bot) -> None:
    for uid in db.get_all_active_users():
        try:
            for app in db.get_followup_due(uid):
                await bot.send_message(
                    uid,
                    f"⏰ *Follow-up reminder!*\n\n"
                    f"🏢 *{app['company']}* – {app['role']}\n"
                    f"📅 Applied: {app['applied_date']}\n\n"
                    f"Reply `/update {app['id']} Interviewed` to update status.",
                    parse_mode="Markdown",
                )
        except Exception as e:
            logger.error(f"Reminder uid={uid}: {e}")


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

async def main() -> None:
    if not settings.telegram_bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN missing in .env")

    bot = Bot(token=settings.telegram_bot_token)
    dp  = Dispatcher()

    scheduler = AsyncIOScheduler(timezone=settings.daily_digest_timezone)
    scheduler.add_job(daily_digest,   "cron",
                      hour=settings.daily_digest_hour, minute=0,  args=[bot])
    scheduler.add_job(followup_check, "cron",
                      hour=settings.daily_digest_hour, minute=5,  args=[bot])
    scheduler.start()
    logger.info(f"Scheduler ready – {settings.daily_digest_hour}:00 {settings.daily_digest_timezone}")

    # ─────────────────────────────────────────────────────────────────────────
    # COMMANDS
    # ─────────────────────────────────────────────────────────────────────────

    @dp.message(CommandStart())
    async def cmd_start(msg: Message) -> None:
        uid = msg.from_user.id
        db.upsert_user(uid, msg.from_user.full_name or "")
        STATE.pop(uid, None)
        await msg.answer(
            "👋 *Welcome to Job Hunter PA!*\n\n"
            "I search jobs from *6 sources*, tailor your resume with keyword analysis, "
            "prep interviews, track applications, and send outreach emails.\n\n"
            "Choose an option 👇",
            parse_mode="Markdown",
            reply_markup=main_menu(),
        )

    @dp.message(Command("help"))
    async def cmd_help(msg: Message) -> None:
        await msg.answer(
            "*📋 All Commands*\n\n"
            "*🔍 Jobs*\n"
            "/jobs – search jobs (6 sources)\n"
            "/digest – save search for daily 9 AM digest\n\n"
            "*📄 Resume*\n"
            "/resume – upload PDF & revise with AI\n"
            "/tailor – tailor to a specific job description\n\n"
            "*📧 Email*\n"
            "/email – draft any email\n"
            "/outreach – cold outreach + Gmail send\n"
            "/gmail\\_connect · /gmail\\_status · /gmail\\_disconnect\n\n"
            "*📊 Applications*\n"
            "/track – add application (or use menu)\n"
            "/myapps – view pipeline table\n"
            "/update ID STATUS – e.g. /update 3 Interviewed\n"
            "/export – download colour-coded Excel\n\n"
            "*🎤 Interview*\n"
            "/interview – full prep guide\n"
            "/practice – live 5-question mock interview\n\n"
            "*⭐ STAR Stories*\n"
            "/addstar – save a STAR story\n"
            "/mystars – list all stories\n\n"
            "*🔔 Notifications*\n"
            "/testalert – test digest + reminders now\n"
            "/remindme – set a custom reminder\n\n"
            "/stop – cancel current action\n"
            "/status – check backend health\n",
            parse_mode="Markdown",
        )

    @dp.message(Command("status"))
    async def cmd_status(msg: Message) -> None:
        try:
            data = await api_get("/health")
            await msg.answer(f"✅ Backend online – v{data.get('version','?')}")
        except Exception as e:
            await msg.answer(f"❌ Backend unreachable: {e}")

    @dp.message(Command("stop"))
    async def cmd_stop(msg: Message) -> None:
        STATE.pop(msg.from_user.id, None)
        PRACTICE.pop(msg.from_user.id, None)
        await msg.answer("✋ Cancelled. Back to main menu.", reply_markup=main_menu())

    # ─────────────────────────────────────────────────────────────────────────
    # 1. JOB SEARCH
    # ─────────────────────────────────────────────────────────────────────────

    @dp.message(F.text == "1. See jobs available")
    @dp.message(Command("jobs"))
    async def start_jobs(msg: Message) -> None:
        STATE.pop(msg.from_user.id, None)  # clear any active flow
        STATE[msg.from_user.id] = {"step": "jobs_role"}
        await msg.answer(
            "🔍 *Job Search*\n\nWhat role are you looking for?\n"
            "_e.g. Data Analyst, Business Analyst, Software Engineer_",
            parse_mode="Markdown",
        )

    @dp.message(Command("digest"))
    async def cmd_digest(msg: Message) -> None:
        STATE[msg.from_user.id] = {"step": "digest_role"}
        await msg.answer(
            "📬 *Save Daily Search*\n\n"
            "What role should I search every morning at 9 AM?",
            parse_mode="Markdown",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # 2. RESUME
    # ─────────────────────────────────────────────────────────────────────────

    @dp.message(F.text == "2. Revise resume")
    @dp.message(Command("resume"))
    async def start_resume(msg: Message) -> None:
        uid = msg.from_user.id
        STATE.pop(uid, None)  # clear any active flow
        existing = db.get_master_resume(uid)
        if existing:
            STATE[uid] = {"step": "resume_have_existing"}
            await msg.answer(
                "📄 I have your stored resume. What would you like to do?",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✏️ Revise stored resume",
                                          callback_data="resume_use_existing")],
                    [InlineKeyboardButton(text="📤 Upload new PDF",
                                          callback_data="resume_upload_new")],
                ]),
            )
        else:
            STATE[uid] = {"step": "resume_await_pdf"}
            await msg.answer(
                "📄 *Upload your resume*\n\n"
                "Send me your resume as a *PDF file*.\n\n"
                "_Tip: If PDF extraction fails, paste your resume text directly._",
                parse_mode="Markdown",
            )

    @dp.message(Command("tailor"))
    async def cmd_tailor(msg: Message) -> None:
        uid = msg.from_user.id
        resume = db.get_master_resume(uid)
        if resume:
            STATE[uid] = {"step": "tailor_jd", "resume_text": resume}
            await msg.answer(
                "🎯 *Resume Tailor*\n\n✅ Using your stored resume.\n\n"
                "Paste the *full job description*:",
                parse_mode="Markdown",
            )
        else:
            STATE[uid] = {"step": "tailor_await_pdf"}
            await msg.answer(
                "🎯 *Resume Tailor*\n\nFirst upload your resume as a PDF.",
                parse_mode="Markdown",
            )

    # ─────────────────────────────────────────────────────────────────────────
    # 3. EMAIL
    # ─────────────────────────────────────────────────────────────────────────

    @dp.message(F.text == "3. Draft email")
    @dp.message(Command("email"))
    async def start_email(msg: Message) -> None:
        STATE.pop(msg.from_user.id, None)  # clear any active flow
        await msg.answer(
            "📧 *Email type:*",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📩 General / follow-up",
                                      callback_data="email_general")],
                [InlineKeyboardButton(text="📨 Cold outreach",
                                      callback_data="email_outreach_start")],
                [InlineKeyboardButton(text="🙏 Thank you (post-interview)",
                                      callback_data="email_thankyou")],
            ]),
        )

    @dp.message(Command("outreach"))
    async def cmd_outreach(msg: Message) -> None:
        STATE[msg.from_user.id] = {"step": "outreach_details"}
        await msg.answer(
            "📨 *Cold Outreach*\n\n"
            "Send one line:\n"
            "`email || Name || Role || Company`\n\n"
            "Example:\n`hr@google.com || Sarah Tan || Data Analyst || Google`",
            parse_mode="Markdown",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # 4. TRACK – Extended menu with view/export
    # ─────────────────────────────────────────────────────────────────────────

    @dp.message(F.text == "4. Track application")
    @dp.message(Command("track"))
    async def start_track(msg: Message) -> None:
        STATE.pop(msg.from_user.id, None)  # clear any active flow
        await msg.answer(
            "📊 *Application Tracker*\n\nWhat would you like to do?",
            parse_mode="Markdown",
            reply_markup=track_menu_kb(),
        )

    @dp.message(Command("myapps"))
    async def cmd_myapps(msg: Message) -> None:
        await show_myapps(msg, msg.from_user.id)

    @dp.message(Command("update"))
    async def cmd_update(msg: Message) -> None:
        parts = (msg.text or "").replace("/update", "").strip().split(None, 1)
        if len(parts) < 2 or not parts[0].isdigit():
            await msg.answer(
                "Usage: `/update APP_ID STATUS`\n"
                "Example: `/update 3 Interviewed`\n\n"
                "Valid statuses: Applied, Interviewed, Offered, Rejected, Withdrawn",
                parse_mode="Markdown",
            )
            return
        try:
            await api_post(f"/applications/update/{parts[0]}",
                           {"status": parts[1].strip().capitalize()})
            await msg.answer(
                f"✅ Application #{parts[0]} → *{parts[1].strip().capitalize()}*",
                parse_mode="Markdown",
            )
        except Exception as e:
            await msg.answer(f"❌ Error: {e}")

    @dp.message(Command("export"))
    async def cmd_export(msg: Message) -> None:
        await msg.answer("⏳ Building your Excel tracker...")
        try:
            xlsx = await api_bytes(f"/applications/export/{msg.from_user.id}")
            await msg.answer_document(
                document=BufferedInputFile(xlsx, filename="job_applications.xlsx"),
                caption=(
                    "📊 *Job Application Tracker*\n\n"
                    "• Sheet 1 – Applications (colour-coded by status)\n"
                    "• Sheet 2 – Dashboard (pipeline stats)\n\n"
                    "_Open in Excel or Google Sheets_"
                ),
                parse_mode="Markdown",
            )
        except Exception as e:
            await msg.answer(f"❌ Export failed: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # 5. INTERVIEW
    # ─────────────────────────────────────────────────────────────────────────

    @dp.message(F.text == "5. Prepare for interviews")
    @dp.message(Command("interview"))
    async def start_interview(msg: Message) -> None:
        STATE.pop(msg.from_user.id, None)  # clear any active flow
        STATE[msg.from_user.id] = {"step": "interview_role"}
        await msg.answer("🎤 *Interview Prep*\n\nWhat role are you interviewing for?",
                         parse_mode="Markdown")

    @dp.message(Command("practice"))
    async def cmd_practice(msg: Message) -> None:
        STATE[msg.from_user.id] = {"step": "practice_role"}
        await msg.answer(
            "🎯 *Mock Interview*\n\n"
            "5 questions with AI feedback on each answer.\n"
            "Type /stop to exit early.\n\n"
            "What role are you practising for?",
            parse_mode="Markdown",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # STAR STORIES
    # ─────────────────────────────────────────────────────────────────────────

    @dp.message(Command("addstar"))
    async def cmd_addstar(msg: Message) -> None:
        STATE[msg.from_user.id] = {"step": "star_title"}
        await msg.answer(
            "⭐ *Add STAR Story*\n\n"
            "Give this story a short title:\n"
            "_e.g. 'Led RFM analysis at Science Centre'_",
            parse_mode="Markdown",
        )

    @dp.message(Command("mystars"))
    async def cmd_mystars(msg: Message) -> None:
        uid     = msg.from_user.id
        stories = db.get_star_stories(uid)
        if not stories:
            await msg.answer("⭐ No STAR stories yet. Use /addstar to add one.")
            return
        lines = [f"⭐ *Your STAR Stories ({len(stories)})*\n"]
        for i, s in enumerate(stories, 1):
            lines.append(
                f"*{i}. {s['title']}*\n"
                f"   🏷️ {s.get('themes','')}\n"
                f"   📈 _{(s.get('result') or '')[:80]}..._\n"
            )
        lines.append("_/addstar to add more_")
        await send_long(msg, "\n".join(lines))

    # ─────────────────────────────────────────────────────────────────────────
    # NOTIFICATIONS
    # ─────────────────────────────────────────────────────────────────────────

    @dp.message(Command("testalert"))
    async def cmd_testalert(msg: Message) -> None:
        uid = msg.from_user.id
        await msg.answer("🧪 *Testing notifications now...*", parse_mode="Markdown")
        searches = db.get_saved_searches(uid)
        if not searches:
            await msg.answer(
                "⚠️ No saved searches.\n\nUse /digest to save a search first.",
                parse_mode="Markdown",
            )
        else:
            new_jobs: list[dict] = []
            for s in searches:
                try:
                    data = await api_post("/jobs/search", {
                        "role": s["role"], "location": s["location"],
                        "limit": 3, "telegram_id": uid, "new_only": True,
                    })
                    new_jobs.extend(data.get("jobs", []))
                except Exception as e:
                    logger.warning(f"testalert: {e}")
            if new_jobs:
                await msg.answer(
                    f"✅ *Digest test – {len(new_jobs)} new job(s):*\n\n"
                    + fmt_jobs(new_jobs[:5]),
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                )
            else:
                await msg.answer(
                    "✅ *Digest test:* No new jobs right now.\n"
                    "_All seen jobs filtered. Try a fresh role with /digest._",
                    parse_mode="Markdown",
                )
        due = db.get_followup_due(uid)
        if due:
            for app in due:
                await msg.answer(
                    f"⏰ *Follow-up due:*\n"
                    f"🏢 *{app['company']}* – {app['role']}\n"
                    f"Use `/update {app['id']} Interviewed`",
                    parse_mode="Markdown",
                )
        else:
            await msg.answer(
                f"✅ *Follow-up test:* No overdue follow-ups.\n"
                f"_Reminders fire {settings.followup_reminder_days} days after applying._",
                parse_mode="Markdown",
            )

    @dp.message(Command("remindme"))
    async def cmd_remindme(msg: Message) -> None:
        STATE[msg.from_user.id] = {"step": "remindme_text"}
        await msg.answer(
            "🔔 *Custom Reminder*\n\nWhat should I remind you about?",
            parse_mode="Markdown",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # GMAIL
    # ─────────────────────────────────────────────────────────────────────────

    @dp.message(Command("gmail_connect"))
    async def cmd_gmail_connect(msg: Message) -> None:
        try:
            data = await api_get(f"/gmail/connect-link?telegram_id={msg.from_user.id}")
            await msg.answer(
                f"🔗 [Tap here to connect Gmail]({data['connect_url']})\n\n"
                "_After approving, return to Telegram._",
                parse_mode="Markdown",
            )
        except Exception as e:
            await msg.answer(f"❌ Gmail not configured: {e}")

    @dp.message(Command("gmail_status"))
    async def cmd_gmail_status(msg: Message) -> None:
        try:
            data = await api_get(f"/gmail/status/{msg.from_user.id}")
            if data["connected"]:
                await msg.answer(f"✅ Gmail connected: *{data['email']}*",
                                 parse_mode="Markdown")
            else:
                await msg.answer("❌ Not connected. Use /gmail\\_connect",
                                 parse_mode="Markdown")
        except Exception as e:
            await msg.answer(f"❌ Error: {e}")

    @dp.message(Command("gmail_disconnect"))
    async def cmd_gmail_disconnect(msg: Message) -> None:
        try:
            await api_post(f"/gmail/disconnect/{msg.from_user.id}", {})
            await msg.answer("✅ Gmail disconnected.")
        except Exception as e:
            await msg.answer(f"❌ Error: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # PDF UPLOAD
    # ─────────────────────────────────────────────────────────────────────────

    @dp.message(F.document)
    async def handle_pdf(msg: Message) -> None:
        uid = msg.from_user.id
        doc = msg.document
        if not doc:
            return
        fname = (doc.file_name or "").lower()
        mime  = (doc.mime_type  or "").lower()
        if not (fname.endswith(".pdf") or mime == "application/pdf"):
            await msg.answer("⚠️ Please send a PDF file (.pdf).")
            return

        await msg.answer("⏳ Reading your PDF...")
        try:
            tf  = await msg.bot.get_file(doc.file_id)
            buf = io.BytesIO()
            await msg.bot.download_file(tf.file_path, destination=buf)
            pdf_bytes = buf.getvalue()
        except Exception as e:
            await msg.answer(f"❌ Download failed: {e}")
            return

        text = extract_text_from_pdf(pdf_bytes)
        if not text or len(text) < 50:
            await msg.answer(
                "❌ Could not extract text from this PDF.\n\n"
                "*Try one of these:*\n"
                "1. Make sure it's text-based (not a scanned image)\n"
                "2. Open PDF → select all → copy → paste here directly\n"
                "3. Export a fresh PDF from Word or Google Docs",
                parse_mode="Markdown",
            )
            return

        PDF_STORE[uid] = pdf_bytes
        word_count = len(text.split())
        step = STATE.get(uid, {}).get("step", "")

        if step in ("resume_await_pdf", "resume_upload_new"):
            db.save_master_resume(uid, text)
            STATE[uid] = {"step": "resume_target_role", "resume_text": text}
            await msg.answer(
                f"✅ Resume saved! _{word_count} words extracted_\n\n"
                "What role are you targeting?\n_e.g. Data Analyst_",
                parse_mode="Markdown",
            )
        elif step == "tailor_await_pdf":
            db.save_master_resume(uid, text)
            STATE[uid] = {"step": "tailor_jd", "resume_text": text}
            await msg.answer(
                f"✅ Resume saved!\n\nNow paste the *full job description*:",
                parse_mode="Markdown",
            )
        elif step == "outreach_await_pdf":
            pending = STATE.get(uid, {})
            pending["resume_bytes_b64"] = base64.b64encode(pdf_bytes).decode()
            pending["step"] = "outreach_confirm_send"
            STATE[uid] = pending
            await msg.answer(
                "✅ Resume ready. Send this outreach email now?",
                reply_markup=yn_kb("outreach_send_confirmed", "outreach_cancel"),
            )
        else:
            db.save_master_resume(uid, text)
            await msg.answer(
                f"✅ Resume saved! _{word_count} words_\n\nWhat would you like to do?",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✏️ Revise it",
                                          callback_data="resume_use_existing")],
                    [InlineKeyboardButton(text="🎯 Tailor to a job",
                                          callback_data="tailor_from_stored")],
                    [InlineKeyboardButton(text="📨 Send outreach",
                                          callback_data="email_outreach_start")],
                ]),
            )

    # ─────────────────────────────────────────────────────────────────────────
    # CALLBACKS
    # ─────────────────────────────────────────────────────────────────────────

    # ── Track menu ────────────────────────────────────────────────────────────
    @dp.callback_query(F.data == "track_add_new")
    async def cb_track_add(cb: CallbackQuery) -> None:
        STATE[cb.from_user.id] = {"step": "track_company"}
        await cb.message.answer("📊 *Add Application*\n\nCompany name?",
                                parse_mode="Markdown")
        await cb.answer()

    @dp.callback_query(F.data == "track_view_all")
    async def cb_track_view(cb: CallbackQuery) -> None:
        await show_myapps(cb.message, cb.from_user.id)
        await cb.answer()

    @dp.callback_query(F.data == "track_export")
    async def cb_track_export(cb: CallbackQuery) -> None:
        await cb.message.answer("⏳ Building Excel tracker...")
        try:
            xlsx = await api_bytes(f"/applications/export/{cb.from_user.id}")
            await cb.message.answer_document(
                document=BufferedInputFile(xlsx, filename="job_applications.xlsx"),
                caption="📊 Your Job Application Tracker (colour-coded, 2 sheets)",
            )
        except Exception as e:
            await cb.message.answer(f"❌ Export failed: {e}")
        await cb.answer()

    @dp.callback_query(F.data == "track_update_help")
    async def cb_track_update_help(cb: CallbackQuery) -> None:
        await cb.message.answer(
            "✏️ *Update Application Status*\n\n"
            "First use /myapps to find the application ID, then:\n\n"
            "`/update ID STATUS`\n\n"
            "Examples:\n"
            "`/update 3 Interviewed`\n"
            "`/update 5 Offered`\n"
            "`/update 2 Rejected`",
            parse_mode="Markdown",
        )
        await cb.answer()

    # ── Resume ────────────────────────────────────────────────────────────────
    @dp.callback_query(F.data == "resume_use_existing")
    async def cb_resume_use(cb: CallbackQuery) -> None:
        uid    = cb.from_user.id
        resume = db.get_master_resume(uid)
        if not resume:
            await cb.message.answer("No stored resume. Please upload a PDF first.")
        else:
            STATE[uid] = {"step": "resume_target_role", "resume_text": resume}
            await cb.message.answer("What role are you targeting?\n_e.g. Data Analyst_",
                                    parse_mode="Markdown")
        await cb.answer()

    @dp.callback_query(F.data == "resume_upload_new")
    async def cb_resume_new(cb: CallbackQuery) -> None:
        STATE[cb.from_user.id] = {"step": "resume_upload_new"}
        await cb.message.answer(
            "📤 Send your updated resume as a PDF.\n\n"
            "_Or paste the full text if your PDF doesn't extract correctly._",
            parse_mode="Markdown",
        )
        await cb.answer()

    @dp.callback_query(F.data == "tailor_from_stored")
    async def cb_tailor_stored(cb: CallbackQuery) -> None:
        uid    = cb.from_user.id
        resume = db.get_master_resume(uid)
        if resume:
            STATE[uid] = {"step": "tailor_jd", "resume_text": resume}
            await cb.message.answer("Paste the *full job description* below:",
                                    parse_mode="Markdown")
        else:
            STATE[uid] = {"step": "tailor_await_pdf"}
            await cb.message.answer("Please upload your resume PDF first.")
        await cb.answer()

    # ── Email ─────────────────────────────────────────────────────────────────
    @dp.callback_query(F.data == "email_general")
    async def cb_email_general(cb: CallbackQuery) -> None:
        STATE[cb.from_user.id] = {"step": "email_purpose"}
        await cb.message.answer(
            "What's the *purpose* of this email?\n"
            "_e.g. follow up on Data Analyst application at Google_",
            parse_mode="Markdown",
        )
        await cb.answer()

    @dp.callback_query(F.data == "email_outreach_start")
    async def cb_email_outreach(cb: CallbackQuery) -> None:
        STATE[cb.from_user.id] = {"step": "outreach_details"}
        await cb.message.answer(
            "📨 *Cold Outreach*\n\n"
            "Send one line:\n`email || Name || Role || Company`",
            parse_mode="Markdown",
        )
        await cb.answer()

    @dp.callback_query(F.data == "email_thankyou")
    async def cb_email_ty(cb: CallbackQuery) -> None:
        STATE[cb.from_user.id] = {
            "step": "email_purpose",
            "purpose_preset": "thank you after job interview",
        }
        await cb.message.answer("Who interviewed you? _(their name)_",
                                parse_mode="Markdown")
        await cb.answer()

    # ── Outreach send/cancel ──────────────────────────────────────────────────
    @dp.callback_query(F.data == "outreach_send_confirmed")
    async def cb_outreach_send(cb: CallbackQuery) -> None:
        uid   = cb.from_user.id
        state = STATE.get(uid, {})
        await cb.message.answer("📤 Sending via Gmail...")
        try:
            r = await api_post("/email/outreach", {**state, "send_now": True})
            if r.get("sent"):
                await cb.message.answer("✅ Outreach email sent!")
                db.log_email(uid, state.get("to_email",""), state.get("recipient_name",""),
                             state.get("company",""), state.get("role",""),
                             r.get("subject",""), r.get("body",""),
                             True, str(date.today() + timedelta(days=7)))
                STATE[uid] = {"step":"track_after_outreach",
                              "company": state.get("company",""),
                              "role":    state.get("role","")}
                await cb.message.answer(
                    f"Track this application to *{state.get('company','')}*?",
                    parse_mode="Markdown",
                    reply_markup=yn_kb("track_auto_yes", "track_auto_no"),
                )
            else:
                await cb.message.answer(
                    "❌ Send failed. Is Gmail connected?\nUse /gmail\\_connect",
                    parse_mode="Markdown",
                )
        except Exception as e:
            await cb.message.answer(f"❌ Error: {e}")
        await cb.answer()

    @dp.callback_query(F.data == "outreach_cancel")
    async def cb_outreach_cancel(cb: CallbackQuery) -> None:
        STATE.pop(cb.from_user.id, None)
        await cb.message.answer("Cancelled.", reply_markup=main_menu())
        await cb.answer()

    @dp.callback_query(F.data == "track_auto_yes")
    async def cb_track_yes(cb: CallbackQuery) -> None:
        uid   = cb.from_user.id
        state = STATE.get(uid, {})
        try:
            r = await api_post("/applications/add", {
                "telegram_id": uid,
                "company": state.get("company","Unknown"),
                "role":    state.get("role","Unknown"),
                "status":  "Applied",
            })
            await cb.message.answer(
                f"✅ Tracked! Follow-up: *{r.get('followup_date','')}*",
                parse_mode="Markdown",
            )
        except Exception as e:
            await cb.message.answer(f"❌ Error: {e}")
        STATE.pop(uid, None)
        await cb.message.answer("Back to main menu 👇", reply_markup=main_menu())
        await cb.answer()

    @dp.callback_query(F.data == "track_auto_no")
    async def cb_track_no(cb: CallbackQuery) -> None:
        STATE.pop(cb.from_user.id, None)
        await cb.message.answer("OK! 👇", reply_markup=main_menu())
        await cb.answer()

    # ── Track status buttons ──────────────────────────────────────────────────
    @dp.callback_query(F.data.startswith("track_status_"))
    async def cb_track_status(cb: CallbackQuery) -> None:
        uid    = cb.from_user.id
        status = cb.data.replace("track_status_","")
        state  = STATE.get(uid, {})
        try:
            r = await api_post("/applications/add", {
                "telegram_id": uid,
                "company": state.get("company","Unknown"),
                "role":    state.get("role","Unknown"),
                "status":  status,
                "url":     state.get("url",""),
                "salary":  state.get("salary",""),
                "source":  state.get("source",""),
            })
            await cb.message.answer(
                f"✅ *Tracked!*\n"
                f"🏢 {state.get('company','')} – {state.get('role','')}\n"
                f"📊 {status} | ⏰ Follow-up: {r.get('followup_date','')}",
                parse_mode="Markdown",
            )
        except Exception as e:
            await cb.message.answer(f"❌ Error: {e}")
        STATE.pop(uid, None)
        await cb.message.answer("Back to main menu 👇", reply_markup=main_menu())
        await cb.answer()

    # ── Job selection ─────────────────────────────────────────────────────────
    @dp.callback_query(F.data.startswith("select_job_"))
    async def cb_select_job(cb: CallbackQuery) -> None:
        uid  = cb.from_user.id
        idx  = int(cb.data.split("_")[-1]) - 1
        jobs = JOBS_CACHE.get(uid, [])
        if idx < 0 or idx >= len(jobs):
            await cb.answer("Invalid selection.")
            return
        job = jobs[idx]
        # ✅ Store in SELECTED_JOB (separate dict, never overwritten by STATE flows)
        SELECTED_JOB[uid] = job

        sal = f"\n💰 {job['salary']}" if job.get("salary") else ""
        await cb.message.answer(
            f"*{job['title']}*\n🏢 {job['company']}{sal}\n\nWhat would you like to do?",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎯 Tailor my resume",
                                      callback_data="job_do_tailor")],
                [InlineKeyboardButton(text="📊 Track application",
                                      callback_data="job_do_track")],
                [InlineKeyboardButton(text="📨 Send outreach",
                                      callback_data="job_do_outreach")],
                [InlineKeyboardButton(text="🎤 Interview prep",
                                      callback_data="job_do_interview")],
            ]),
        )
        await cb.answer()

    @dp.callback_query(F.data == "job_do_tailor")
    async def cb_job_tailor(cb: CallbackQuery) -> None:
        uid    = cb.from_user.id
        # ✅ Read from SELECTED_JOB, not STATE
        job    = SELECTED_JOB.get(uid, {})
        resume = db.get_master_resume(uid)
        auto_jd = job.get("description", "")

        if not resume:
            STATE[uid] = {
                "step": "tailor_await_pdf",
                "job_title": job.get("title",""),
                "company":   job.get("company",""),
                "auto_jd":   auto_jd,
            }
            await cb.message.answer(
                "Please upload your resume PDF first.\n\n"
                "_Or paste your resume text directly._",
                parse_mode="Markdown",
            )
        elif auto_jd:
            # ✅ AUTO TAILOR: use job description snippet directly, no prompt needed
            await cb.message.answer(
                f"⏳ *Auto-tailoring* your resume for:\n"
                f"*{job.get('title','')}* at *{job.get('company','')}*\n\n"
                "_Using job description from search results..._",
                parse_mode="Markdown",
            )
            STATE[uid] = {
                "resume_text": resume,
                "job_title":   job.get("title",""),
                "company":     job.get("company",""),
                "jd_text":     auto_jd,
            }
            await _run_tailor(cb.message, uid)
        else:
            # No description snippet – ask user to paste JD
            STATE[uid] = {
                "step":        "tailor_jd",
                "resume_text": resume,
                "job_title":   job.get("title",""),
                "company":     job.get("company",""),
            }
            await cb.message.answer(
                f"🎯 Tailoring for *{job.get('title','')}* at *{job.get('company','')}*\n\n"
                "Paste the *full job description* for best results:",
                parse_mode="Markdown",
            )
        await cb.answer()

    @dp.callback_query(F.data == "job_do_track")
    async def cb_job_track(cb: CallbackQuery) -> None:
        uid = cb.from_user.id
        # ✅ Read from SELECTED_JOB
        job = SELECTED_JOB.get(uid, {})
        try:
            r = await api_post("/applications/add", {
                "telegram_id": uid,
                "company": job.get("company","Unknown"),
                "role":    job.get("title","Unknown"),
                "status":  "Applied",
                "url":     job.get("url",""),
                "salary":  job.get("salary",""),
                "source":  job.get("source",""),
            })
            await cb.message.answer(
                f"✅ *Tracked!*\n"
                f"🏢 {job.get('company','')} – {job.get('title','')}\n"
                f"⏰ Follow-up: {r.get('followup_date','')}",
                parse_mode="Markdown",
            )
        except Exception as e:
            await cb.message.answer(f"❌ Error: {e}")
        await cb.answer()

    @dp.callback_query(F.data == "job_do_outreach")
    async def cb_job_outreach(cb: CallbackQuery) -> None:
        uid = cb.from_user.id
        # ✅ Read from SELECTED_JOB — no STATE dependency, never blank
        job = SELECTED_JOB.get(uid, {})
        company = job.get("company","")
        role    = job.get("title","")

        # Store prefills in STATE for the outreach flow
        STATE[uid] = {
            "step":            "outreach_details",
            "prefill_company": company,
            "prefill_role":    role,
        }
        await cb.message.answer(
            f"📨 *Outreach for {role} at {company}*\n\n"
            "Send:\n`email || Recipient Name`\n"
            "_(role and company are pre-filled from the job card)_\n\n"
            "Or full format:\n`email || Name || Role || Company`",
            parse_mode="Markdown",
        )
        await cb.answer()

    @dp.callback_query(F.data == "job_do_interview")
    async def cb_job_interview(cb: CallbackQuery) -> None:
        uid = cb.from_user.id
        # ✅ Read from SELECTED_JOB
        job = SELECTED_JOB.get(uid, {})
        await cb.message.answer(
            f"⏳ Building interview guide for *{job.get('title','')}* "
            f"at *{job.get('company','')}*...",
            parse_mode="Markdown",
        )
        try:
            r = await api_post("/interview/prepare", {
                "role":        job.get("title",""),
                "company":     job.get("company",""),
                "focus_areas": [],
            })
            await send_long(cb.message, r.get("text",""))
        except Exception as e:
            await cb.message.answer(f"❌ Error: {e}")
        await cb.answer()

    @dp.callback_query(F.data == "outreach_do_send_check")
    async def cb_outreach_do_send(cb: CallbackQuery) -> None:
        uid   = cb.from_user.id
        state = STATE.get(uid, {})
        try:
            gd = await api_get(f"/gmail/status/{uid}")
            ok = gd.get("connected", False)
        except Exception:
            ok = False
        if not ok:
            await cb.message.answer(
                "❌ Gmail not connected.\nUse /gmail\\_connect first.",
                parse_mode="Markdown",
            )
            await cb.answer()
            return
        pdf = PDF_STORE.get(uid)
        if not pdf:
            STATE[uid] = {**state, "step": "outreach_await_pdf"}
            await cb.message.answer("📎 Upload your resume PDF to attach to the email.")
        else:
            state["resume_bytes_b64"] = base64.b64encode(pdf).decode()
            state["send_now"] = True
            await cb.message.answer("📤 Sending...")
            try:
                r = await api_post("/email/outreach", state)
                if r.get("sent"):
                    await cb.message.answer("✅ Outreach email sent!")
                    db.log_email(uid, state.get("to_email",""), state.get("recipient_name",""),
                                 state.get("company",""), state.get("role",""),
                                 r.get("subject",""), r.get("body",""),
                                 True, str(date.today() + timedelta(days=7)))
                else:
                    await cb.message.answer(
                        "❌ Send failed. Check /gmail\\_status",
                        parse_mode="Markdown",
                    )
            except Exception as e:
                await cb.message.answer(f"❌ Error: {e}")
            STATE.pop(uid, None)
        await cb.answer()

    # ─────────────────────────────────────────────────────────────────────────
    # TEXT STATE MACHINE
    # ─────────────────────────────────────────────────────────────────────────

    @dp.message(F.text)
    async def handle_text(msg: Message) -> None:
        uid   = msg.from_user.id
        text  = msg.text.strip()
        state = STATE.get(uid, {})
        step  = state.get("step", "")

        # ── Job search ────────────────────────────────────────────────────────
        if step == "jobs_role":
            STATE[uid] = {**state, "step": "jobs_location", "role": text}
            await msg.answer("📍 Location?\n_Singapore / Remote / Worldwide_",
                             parse_mode="Markdown")

        elif step == "jobs_location":
            STATE[uid] = {**state, "step": "jobs_limit", "location": text}
            await msg.answer("How many results? _(1–20, default 10)_",
                             parse_mode="Markdown")

        elif step == "jobs_limit":
            try:
                limit = max(1, min(20, int(text)))
            except ValueError:
                limit = 10
            role = state.get("role","")
            loc  = state.get("location","singapore")
            await msg.answer(
                f"🔍 Searching *{limit}* jobs for *{role}* in *{loc}*...\n"
                "_Checking 6 sources in parallel..._",
                parse_mode="Markdown",
            )
            try:
                data = await api_post("/jobs/search", {
                    "role": role, "location": loc,
                    "limit": limit, "telegram_id": uid,
                })
                jobs = data.get("jobs",[])
                JOBS_CACHE[uid] = jobs
                await send_long(msg, fmt_jobs(jobs), pm="Markdown")
                if jobs:
                    buttons = [
                        [InlineKeyboardButton(
                            text=f"{i}. {j['title'][:28]} @ {j['company'][:18]}",
                            callback_data=f"select_job_{i}",
                        )]
                        for i,j in enumerate(jobs[:10],1)
                    ]
                    await msg.answer(
                        "👆 Select a job to tailor resume, track, or prep interview:",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
                    )
            except Exception as e:
                await msg.answer(f"❌ Search error: {e}")
            STATE.pop(uid, None)

        # ── Digest ────────────────────────────────────────────────────────────
        elif step == "digest_role":
            STATE[uid] = {**state, "step": "digest_location", "role": text}
            await msg.answer("📍 Location? _(Singapore / Remote)_",
                             parse_mode="Markdown")

        elif step == "digest_location":
            role = state.get("role","")
            db.save_search_profile(uid, f"{role} in {text}", role, text)
            STATE.pop(uid, None)
            await msg.answer(
                f"✅ Saved! Every morning at *{settings.daily_digest_hour}:00 AM* "
                f"({settings.daily_digest_timezone}) I'll push new *{role}* jobs in *{text}*.\n\n"
                "Use /testalert to test right now.",
                parse_mode="Markdown",
            )

        # ── Resume revise ─────────────────────────────────────────────────────
        elif step == "resume_target_role":
            resume = state.get("resume_text","")
            await msg.answer("⏳ Analysing your resume... _(~15 seconds)_",
                             parse_mode="Markdown")
            try:
                r = await api_post("/resume/revise", {
                    "resume_text": resume,
                    "target_role": text,
                    "telegram_id": uid,
                })
                await send_long(msg, r.get("text",""))
            except Exception as e:
                await msg.answer(f"❌ Error: {e}")
            STATE.pop(uid, None)
            await msg.answer("Done! Back to main menu 👇", reply_markup=main_menu())

        # ── Resume tailor ─────────────────────────────────────────────────────
        elif step == "tailor_jd":
            STATE[uid] = {**state, "step": "tailor_job_title", "jd_text": text}
            await msg.answer("Job title?\n_e.g. Data Analyst_", parse_mode="Markdown")

        elif step == "tailor_job_title":
            STATE[uid] = {**state, "step": "tailor_company", "job_title": text}
            await msg.answer("Company name?")

        elif step == "tailor_company":
            STATE[uid] = {**state, "company": text}
            await msg.answer("⏳ Tailoring your resume... _(~20 seconds)_",
                             parse_mode="Markdown")
            await _run_tailor(msg, uid)

        # ── Email general ─────────────────────────────────────────────────────
        elif step == "email_purpose":
            preset  = state.get("purpose_preset","")
            purpose = preset or text
            STATE[uid] = {**state, "step": "email_recipient", "purpose": purpose}
            await msg.answer("Recipient's name?")

        elif step == "email_recipient":
            STATE[uid] = {**state, "step": "email_context", "recipient_name": text}
            await msg.answer(
                "Any extra context?\n"
                "_e.g. applied via LinkedIn last week, met at NUS career fair_",
                parse_mode="Markdown",
            )

        elif step == "email_context":
            await msg.answer("⏳ Drafting email...")
            try:
                r = await api_post("/email/draft", {
                    "purpose":        state.get("purpose",""),
                    "recipient_name": state.get("recipient_name",""),
                    "context":        text,
                    "tone":           "professional",
                })
                await send_long(msg, r.get("text",""))
            except Exception as e:
                await msg.answer(f"❌ Error: {e}")
            STATE.pop(uid, None)
            await msg.answer("Done! Back to main menu 👇", reply_markup=main_menu())

        # ── Outreach ──────────────────────────────────────────────────────────
        elif step == "outreach_details":
            parts        = [p.strip() for p in text.split("||")]
            prefill_co   = state.get("prefill_company","")
            prefill_role = state.get("prefill_role","")

            if len(parts) < 2:
                await msg.answer(
                    "Please send:\n"
                    "`email || Name`  _(when role+company pre-filled from job card)_\n"
                    "or\n"
                    "`email || Name || Role || Company`  _(manual)_",
                    parse_mode="Markdown",
                )
                return

            to_email = parts[0]
            to_name  = parts[1]
            role     = parts[2] if len(parts) > 2 else prefill_role
            company  = parts[3] if len(parts) > 3 else prefill_co

            if not role or not company:
                await msg.answer(
                    "❌ Missing role or company.\n\n"
                    "Format: `email || Name || Role || Company`\n"
                    "Example: `hr@grab.com || Sarah || Data Analyst || Grab`\n\n"
                    "_Or tap a job first → Send outreach (role/company auto-filled)_",
                    parse_mode="Markdown",
                )
                return

            kws = ""
            stored = db.get_master_resume(uid)
            if stored:
                kws = ", ".join(extract_keywords(stored)[:12])

            await msg.answer("⏳ Drafting outreach email...")
            try:
                r = await api_post("/email/outreach", {
                    "telegram_id":       uid,
                    "to_email":          to_email,
                    "recipient_name":    to_name,
                    "role":              role,
                    "company":           company,
                    "sender_name":       msg.from_user.full_name or "Candidate",
                    "resume_highlights": kws,
                    "send_now":          False,
                })
                subject = r.get("subject","")
                body    = r.get("body","")
                await send_long(msg, f"📧 *Draft:*\n\n*Subject:* {subject}\n\n{body}")
                STATE[uid] = {
                    "step":             "outreach_confirm",
                    "telegram_id":      uid,
                    "to_email":         to_email,
                    "recipient_name":   to_name,
                    "role":             role,
                    "company":          company,
                    "sender_name":      msg.from_user.full_name or "",
                    "resume_highlights": kws,
                    "subject":          subject,
                    "body":             body,
                }
                await msg.answer(
                    "Send this now via Gmail?",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="✅ Send via Gmail",
                                              callback_data="outreach_do_send_check")],
                        [InlineKeyboardButton(text="📋 Keep as draft",
                                              callback_data="outreach_cancel")],
                    ]),
                )
            except Exception as e:
                await msg.answer(f"❌ Error: {e}")
                STATE.pop(uid, None)

        # ── Track application ─────────────────────────────────────────────────
        elif step == "track_company":
            STATE[uid] = {**state, "step": "track_role", "company": text}
            await msg.answer("Role you applied for?")

        elif step == "track_role":
            STATE[uid] = {**state, "step": "track_status", "role": text}
            await msg.answer(
                "Status?",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="📤 Applied",
                                          callback_data="track_status_Applied")],
                    [InlineKeyboardButton(text="🎤 Interviewed",
                                          callback_data="track_status_Interviewed")],
                    [InlineKeyboardButton(text="🎉 Offered",
                                          callback_data="track_status_Offered")],
                    [InlineKeyboardButton(text="❌ Rejected",
                                          callback_data="track_status_Rejected")],
                    [InlineKeyboardButton(text="↩️ Withdrawn",
                                          callback_data="track_status_Withdrawn")],
                ]),
            )

        # ── Interview prep ────────────────────────────────────────────────────
        elif step == "interview_role":
            STATE[uid] = {**state, "step": "interview_company", "role": text}
            await msg.answer("Which company?")

        elif step == "interview_company":
            role = state.get("role","")
            await msg.answer(
                f"⏳ Building interview guide for *{role}* at *{text}*...",
                parse_mode="Markdown",
            )
            try:
                r = await api_post("/interview/prepare", {
                    "role": role, "company": text, "focus_areas": [],
                })
                await send_long(msg, r.get("text",""))
            except Exception as e:
                await msg.answer(f"❌ Error: {e}")
            STATE.pop(uid, None)
            await msg.answer("Done! Back to main menu 👇", reply_markup=main_menu())

        # ── Mock interview ────────────────────────────────────────────────────
        elif step == "practice_role":
            STATE[uid] = {**state, "step": "practice_company", "role": text}
            await msg.answer("Which company?")

        elif step == "practice_company":
            role = state.get("role","")
            await msg.answer(
                f"🎯 *Mock interview:* {role} at {text}\n\n"
                "5 questions, AI feedback after each. /stop to exit.\n\n"
                "⏳ Getting question 1...",
                parse_mode="Markdown",
            )
            from app.services.llm_tasks import practice_question
            try:
                q = await practice_question(role, text, "behavioural")
                PRACTICE[uid] = {"role": role, "company": text,
                                  "question": q, "count": 1}
                STATE[uid] = {"step": "practice_answer"}
                await msg.answer(f"❓ *Question 1 of 5:*\n\n{q}",
                                 parse_mode="Markdown")
            except Exception as e:
                await msg.answer(f"❌ Error: {e}")
                STATE.pop(uid, None)

        elif step == "practice_answer":
            ctx   = PRACTICE.get(uid, {})
            count = ctx.get("count",1)
            await msg.answer("📝 Evaluating your answer...")
            from app.services.llm_tasks import evaluate_answer, practice_question
            try:
                fb = await evaluate_answer(ctx.get("question",""), text, ctx.get("role",""))
                await send_long(msg, fb)
                if count < 5:
                    q_type = "technical" if count % 2 == 0 else "behavioural"
                    nq = await practice_question(ctx.get("role",""),
                                                 ctx.get("company",""), q_type)
                    PRACTICE[uid] = {**ctx, "question": nq, "count": count+1}
                    await msg.answer(f"❓ *Question {count+1} of 5:*\n\n{nq}",
                                     parse_mode="Markdown")
                else:
                    await msg.answer(
                        "🎉 *Mock interview complete!* 5 questions done.\n"
                        "Use /practice for another round.",
                        parse_mode="Markdown",
                    )
                    STATE.pop(uid, None); PRACTICE.pop(uid, None)
                    await msg.answer("Back to main menu 👇", reply_markup=main_menu())
            except Exception as e:
                await msg.answer(f"❌ Error: {e}")

        # ── STAR story ────────────────────────────────────────────────────────
        elif step == "star_title":
            STATE[uid] = {**state, "step": "star_situation", "title": text}
            await msg.answer("📖 *Situation*\n\nWhat was the context or challenge?",
                             parse_mode="Markdown")

        elif step == "star_situation":
            STATE[uid] = {**state, "step": "star_task", "situation": text}
            await msg.answer("🎯 *Task*\n\nWhat was YOUR specific responsibility?",
                             parse_mode="Markdown")

        elif step == "star_task":
            STATE[uid] = {**state, "step": "star_action", "task": text}
            await msg.answer("⚡ *Action*\n\nWhat did YOU do? _(use 'I', be specific)_",
                             parse_mode="Markdown")

        elif step == "star_action":
            STATE[uid] = {**state, "step": "star_result", "action": text}
            await msg.answer("📈 *Result*\n\nWhat was the outcome?\n"
                             "_(include numbers/metrics if possible)_",
                             parse_mode="Markdown")

        elif step == "star_result":
            STATE[uid] = {**state, "step": "star_themes", "result": text}
            await msg.answer("🏷️ *Themes* (comma-separated)\n"
                             "_e.g. leadership, analytics, problem-solving_",
                             parse_mode="Markdown")

        elif step == "star_themes":
            s = STATE.get(uid, {})
            db.add_star_story(uid, title=s.get("title",""),
                              situation=s.get("situation",""), task=s.get("task",""),
                              action=s.get("action",""), result=s.get("result",""),
                              themes=text)
            STATE.pop(uid, None)
            await msg.answer(
                f"✅ *STAR story saved!*\n\n📌 *{s.get('title','')}*\n🏷️ Themes: {text}\n\n"
                "Use /mystars to see all stories.",
                parse_mode="Markdown",
                reply_markup=main_menu(),
            )

        # ── Custom reminder ───────────────────────────────────────────────────
        elif step == "remindme_text":
            STATE[uid] = {**state, "step": "remindme_days", "reminder_text": text}
            await msg.answer("⏰ In how many days? _(e.g. 1, 3, 7)_",
                             parse_mode="Markdown")

        elif step == "remindme_days":
            try:
                days = max(1, min(30, int(text)))
            except ValueError:
                days = 3
            reminder    = state.get("reminder_text","")
            remind_date = date.today() + timedelta(days=days)
            db.add_application(uid, company="Reminder", role=reminder,
                                status="Applied",
                                notes=f"Custom reminder",
                                followup_date=str(remind_date))
            STATE.pop(uid, None)
            await msg.answer(
                f"✅ *Reminder set!*\n\n📌 _{reminder}_\n📅 I'll remind you on *{remind_date}*",
                parse_mode="Markdown",
                reply_markup=main_menu(),
            )

        # ── Fallback ──────────────────────────────────────────────────────────
        else:
            await msg.answer("👇 Use the menu below or /help for all commands.",
                             reply_markup=main_menu())

    # ─────────────────────────────────────────────────────────────────────────
    print("\n" + "═"*55)
    print("  Job Hunter PA – Telegram Bot v3.1")
    print(f"  Backend : {BACKEND}")
    print(f"  Digest  : {settings.daily_digest_hour}:00 {settings.daily_digest_timezone}")
    print("═"*55 + "\n")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
           