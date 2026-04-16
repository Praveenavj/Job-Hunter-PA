"""
Job Hunter PA — FastAPI backend v3.0
=====================================
Changes from v2.1:
  - /jobs/digest-trigger   → external cron endpoint (cron-job.org)
  - /jobs/followup-trigger → external cron endpoint
  - /resume/tailor         → now fetches full job page when URL provided
  - /email/outreach        → Gmail connected check happens at draft time
  - All LLM errors returned as text (no 500s)
"""
from __future__ import annotations
import base64
import logging
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app import database as db
from app.config import settings
from app.services import job_aggregator, gmail_service, llm_tasks
from app.resume_utils import gap_analysis

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

db.init_db()
app = FastAPI(title="Job Hunter PA", version="3.0.0")


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "version": "3.0.0"}


# ── Job Search ────────────────────────────────────────────────────────────────

class JobsRequest(BaseModel):
    role: str
    location: str = "singapore"
    limit: int = 10
    telegram_id: Optional[int] = None
    new_only: bool = False


@app.post("/jobs/search")
async def search_jobs(req: JobsRequest):
    try:
        jobs = await job_aggregator.search_jobs(
            query=req.role, location=req.location, limit=req.limit,
            telegram_id=req.telegram_id, new_only=req.new_only,
        )
        return {"jobs": jobs, "total": len(jobs)}
    except Exception as e:
        raise HTTPException(500, str(e))


# ── External cron trigger endpoints (called by cron-job.org or Railway cron) ──

async def _run_daily_digest(bot=None):
    """
    Core digest logic — shared by both the APScheduler job and the HTTP endpoint.
    If bot is None, we import and use the global bot instance.
    """
    from bot.telegram_bot import get_bot_instance
    b = bot or get_bot_instance()
    if b is None:
        logger.warning("digest-trigger: no bot instance available yet")
        return 0

    from datetime import date
    sent = 0
    for uid in db.get_all_active_users():
        try:
            searches = db.get_saved_searches(uid)
            if not searches:
                continue
            new_jobs = []
            for s in searches:
                data = await job_aggregator.search_jobs(
                    query=s["role"], location=s["location"],
                    limit=s.get("limit_", 5), telegram_id=uid, new_only=True,
                )
                new_jobs.extend(data if isinstance(data, list) else data.get("jobs", []))
            if new_jobs:
                from bot.telegram_bot import fmt_jobs
                await b.send_message(
                    uid,
                    f"🌅 *Good morning! Daily digest — {date.today()}*\n\n" + fmt_jobs(new_jobs[:10]),
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                )
            else:
                await b.send_message(uid, "☀️ *Daily digest:* No new jobs today.", parse_mode="Markdown")
            sent += 1
        except Exception as e:
            logger.error(f"Digest uid={uid}: {e}")
    return sent


async def _run_followup_check(bot=None):
    """Core follow-up + reminder logic."""
    from bot.telegram_bot import get_bot_instance
    b = bot or get_bot_instance()
    if b is None:
        return 0

    sent = 0
    for uid in db.get_all_active_users():
        try:
            # Application follow-ups
            for app_row in db.get_followup_due(uid):
                await b.send_message(
                    uid,
                    f"⏰ *Follow-up reminder!*\n\n"
                    f"🏢 *{app_row['company']}* — {app_row['role']}\n"
                    f"📅 Applied: {app_row['applied_date']}\n\n"
                    f"Reply `/update {app_row['id']} Interviewed` to update status.",
                    parse_mode="Markdown",
                )
                sent += 1

            # Custom reminders (from /remindme — proper table, not fake applications)
            for reminder in db.get_pending_reminders(uid):
                await b.send_message(
                    uid,
                    f"🔔 *Reminder:*\n\n_{reminder['text']}_\n\n"
                    f"_(Use /myreminders to see all your reminders)_",
                    parse_mode="Markdown",
                )
                db.mark_reminder_done(reminder["id"])
                sent += 1
        except Exception as e:
            logger.error(f"Followup uid={uid}: {e}")
    return sent


@app.post("/jobs/digest-trigger")
async def trigger_digest(x_cron_secret: str = Header(None)):
    """
    Called by cron-job.org at 09:00 Asia/Singapore.
    Protected by X-Cron-Secret header matching settings.cron_secret.
    """
    if x_cron_secret != settings.cron_secret:
        raise HTTPException(401, "Invalid cron secret")
    sent = await _run_daily_digest()
    return {"status": "triggered", "users_notified": sent}


@app.post("/jobs/followup-trigger")
async def trigger_followup(x_cron_secret: str = Header(None)):
    """
    Called by cron-job.org at 09:05 Asia/Singapore.
    Protected by X-Cron-Secret header.
    """
    if x_cron_secret != settings.cron_secret:
        raise HTTPException(401, "Invalid cron secret")
    sent = await _run_followup_check()
    return {"status": "triggered", "notifications_sent": sent}


# ── Resume ────────────────────────────────────────────────────────────────────

class ResumeReviseRequest(BaseModel):
    resume_text: str
    target_role: str
    telegram_id: Optional[int] = None


@app.post("/resume/revise")
async def revise_resume(req: ResumeReviseRequest):
    if req.telegram_id:
        db.save_master_resume(req.telegram_id, req.resume_text)
    text = await llm_tasks.resume_revise(req.resume_text, req.target_role)
    return {"text": text}


class TailorRequest(BaseModel):
    resume_text: str
    job_description: str
    job_title: str = ""
    company: str = ""
    job_url: str = ""   # NEW: pass the job URL to fetch full description


@app.post("/resume/tailor")
async def tailor_resume(req: TailorRequest):
    jd = req.job_description

    # Fetch full job page if URL provided and description is short (<500 chars)
    if req.job_url and len(jd) < 500:
        try:
            full_jd = await _fetch_job_page(req.job_url)
            if full_jd and len(full_jd) > len(jd):
                jd = full_jd
                logger.info(f"Fetched full JD from {req.job_url}: {len(jd)} chars")
        except Exception as e:
            logger.warning(f"JD fetch failed for {req.job_url}: {e} — using snippet")

    text = await llm_tasks.resume_tailor(req.resume_text, jd, req.job_title, req.company)
    gap = gap_analysis(req.resume_text, jd)
    return {"text": text, "gap": gap}


async def _fetch_job_page(url: str) -> str:
    """
    Fetch full job page text. Uses BeautifulSoup if available,
    falls back to raw text extraction.
    """
    import httpx
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml",
    }
    async with httpx.AsyncClient(timeout=15, headers=headers, follow_redirects=True) as client:
        r = await client.get(url)
        r.raise_for_status()
        html = r.text

    # Try BeautifulSoup first
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        # Remove script/style tags
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        text = soup.get_text(separator="\n", strip=True)
        # Trim to 4000 chars (enough for any JD)
        return text[:4000]
    except ImportError:
        pass

    # Fallback: strip HTML tags with regex
    import re
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:4000]


# ── Email ─────────────────────────────────────────────────────────────────────

class EmailDraftRequest(BaseModel):
    purpose: str
    recipient_name: str
    context: str
    tone: str = "professional"


@app.post("/email/draft")
async def draft_email(req: EmailDraftRequest):
    text = await llm_tasks.draft_email(req.purpose, req.recipient_name, req.context, req.tone)
    return {"text": text}


class OutreachRequest(BaseModel):
    telegram_id: int
    to_email: str
    recipient_name: str
    role: str
    company: str
    sender_name: str
    resume_highlights: str = ""
    resume_bytes_b64: Optional[str] = None
    send_now: bool = False


@app.post("/email/outreach")
async def outreach_email(req: OutreachRequest):
    subject, body = await llm_tasks.draft_outreach(
        req.recipient_name, req.role, req.company,
        req.sender_name, req.resume_highlights,
    )
    if not req.send_now:
        return {"subject": subject, "body": body, "sent": False}

    att_bytes = base64.b64decode(req.resume_bytes_b64) if req.resume_bytes_b64 else None
    ok, msg_id = gmail_service.send_email(
        req.telegram_id, req.to_email, subject, body,
        attachment_bytes=att_bytes, attachment_name="resume.pdf",
    )
    if not ok:
        raise HTTPException(400, msg_id)
    return {"subject": subject, "body": body, "sent": True, "message_id": msg_id}


# ── Gmail OAuth ───────────────────────────────────────────────────────────────

@app.get("/gmail/connect-link")
async def gmail_connect_link(telegram_id: int = Query(...)):
    try:
        url = gmail_service.get_auth_url(telegram_id)
        return {"connect_url": url}
    except Exception as e:
        raise HTTPException(400, str(e))


@app.get("/gmail/status/{telegram_id}")
async def gmail_status(telegram_id: int):
    connected, email = gmail_service.get_status(telegram_id)
    return {"connected": connected, "email": email}


@app.post("/gmail/disconnect/{telegram_id}")
async def gmail_disconnect(telegram_id: int):
    gmail_service.disconnect(telegram_id)
    return {"connected": False}


@app.get("/oauth/gmail/callback", response_class=HTMLResponse)
async def oauth_callback(code: str, state: str):
    try:
        tid, email = await gmail_service.complete_oauth(code, state)
        return HTMLResponse(
            f"<h2>✅ Gmail connected!</h2><p>Account: <b>{email}</b></p>"
            "<p>You can close this tab and return to Telegram.</p>"
        )
    except Exception as e:
        return HTMLResponse(
            f"<h2>❌ Connection failed</h2><p>{e}</p>"
            "<p>Please try again from Telegram.</p>",
            status_code=400,
        )


# ── Interview ─────────────────────────────────────────────────────────────────

class InterviewPrepRequest(BaseModel):
    role: str
    company: str
    focus_areas: list[str] = []


@app.post("/interview/prepare")
async def interview_prepare(req: InterviewPrepRequest):
    text = await llm_tasks.interview_prep(req.role, req.company, req.focus_areas)
    return {"text": text}


# ── Applications ──────────────────────────────────────────────────────────────

class AppAddRequest(BaseModel):
    telegram_id: int
    company: str
    role: str
    status: str = "Applied"
    url: str = ""
    notes: str = ""
    salary: str = ""
    source: str = ""


@app.post("/applications/add")
async def add_application(req: AppAddRequest):
    from datetime import date, timedelta
    followup = str(date.today() + timedelta(days=settings.followup_reminder_days))
    app_id = db.add_application(
        req.telegram_id, req.company, req.role, req.status,
        req.url, req.notes, req.salary, req.source, followup,
    )
    return {"id": app_id, "followup_date": followup}


@app.get("/applications/{telegram_id}")
async def get_applications(telegram_id: int):
    apps = db.get_applications(telegram_id)
    return {"applications": apps, "total": len(apps)}


class AppUpdateRequest(BaseModel):
    status: str
    notes: str = ""


@app.post("/applications/update/{app_id}")
async def update_app(app_id: int, req: AppUpdateRequest):
    db.update_application_status(app_id, req.status, req.notes)
    return {"updated": True, "app_id": app_id, "status": req.status}


@app.get("/applications/export/{telegram_id}")
async def export_excel(telegram_id: int):
    from app.services.excel_tracker import get_workbook_path
    from fastapi.responses import FileResponse
    path = get_workbook_path(telegram_id)
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="job_applications.xlsx",
    )
