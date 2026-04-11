"""
Job Hunter PA – FastAPI backend v2.1
All endpoints. LLM errors returned as text (no 500s).
"""
from __future__ import annotations
import base64
import logging
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app import database as db
from app.config import settings
from app.services import job_aggregator, gmail_service, llm_tasks
from app.resume_utils import gap_analysis

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

db.init_db()
app = FastAPI(title="Job Hunter PA", version="2.1.0")


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.1.0"}


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


# ── Resume ────────────────────────────────────────────────────────────────────

class ResumeReviseRequest(BaseModel):
    resume_text: str
    target_role: str
    telegram_id: Optional[int] = None


@app.post("/resume/revise")
async def revise_resume(req: ResumeReviseRequest):
    if req.telegram_id:
        db.save_master_resume(req.telegram_id, req.resume_text)
    # llm_client.complete never raises - returns error string on failure
    text = await llm_tasks.resume_revise(req.resume_text, req.target_role)
    return {"text": text}


class TailorRequest(BaseModel):
    resume_text: str
    job_description: str
    job_title: str = ""
    company: str = ""


@app.post("/resume/tailor")
async def tailor_resume(req: TailorRequest):
    text = await llm_tasks.resume_tailor(
        req.resume_text, req.job_description, req.job_title, req.company
    )
    gap = gap_analysis(req.resume_text, req.job_description)
    return {"text": text, "gap": gap}


# ── Email ─────────────────────────────────────────────────────────────────────

class EmailDraftRequest(BaseModel):
    purpose: str
    recipient_name: str
    context: str
    tone: str = "professional"


@app.post("/email/draft")
async def draft_email(req: EmailDraftRequest):
    text = await llm_tasks.draft_email(
        req.purpose, req.recipient_name, req.context, req.tone
    )
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
