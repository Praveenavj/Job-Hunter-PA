from typing import Optional
import base64
import binascii

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
import asyncio

from app.schemas import (
    DraftEmailRequest,
    GmailConnectLinkResponse,
    GmailConnectionStatusResponse,
    GmailDisconnectRequest,
    InterviewPrepRequest,
    JobsRequest,
    JobsResponse,
    LLMResponse,
    OutreachEmailRequest,
    OutreachEmailResponse,
    ResumeReviseRequest,
    TrackJobRequest,
    TrackJobResponse,
    NotionSummaryResponse,      
    ResumeTailorRequest, 
)
from app.services.gmail_oauth_service import gmail_oauth_service
from app.services.gmail_service import gmail_service
from app.services.job_service import job_service
from app.services.notion_service import notion_service
from app.services.openclaw_client import openclaw_client

app = FastAPI(title="Job Hunter Personal Assistant API", version="1.0.0")


def _split_subject_and_body(text: str) -> tuple[str, str]:
    lines = [line for line in text.strip().splitlines()]
    if not lines:
        return "Job Application Outreach", ""

    first = lines[0].strip()
    if first.lower().startswith("subject:"):
        subject = first.split(":", 1)[1].strip() or "Job Application Outreach"
        body = "\n".join(lines[1:]).strip()
        return subject, body

    return "Job Application Outreach", text.strip()


def _fallback_outreach_draft(recipient_name: str, role: str, company: str, resume_text: str) -> tuple[str, str]:
    placeholder_markers = {
        "please refer to the attached resume pdf for full details.",
        "resume is available as pdf and can be shared when requested.",
    }
    normalized_resume_text = (resume_text or "").strip()
    include_highlights = bool(normalized_resume_text) and normalized_resume_text.lower() not in placeholder_markers

    subject = f"Application Interest: {role} role at {company}"
    highlights_section = ""
    if include_highlights:
        highlights_section = f"Highlights from my profile:\n{normalized_resume_text}\n\n"

    body = (
        f"Hi {recipient_name},\n\n"
        f"I hope you are doing well. I am reaching out to express my interest in the {role} role at {company}. "
        "I believe my background is a strong fit for this opportunity.\n\n"
        f"{highlights_section}"
        "I would be grateful for the opportunity to discuss how I can contribute to your team. "
        "Please find my resume attached for your review.\n\n"
        "Thank you for your time and consideration."
    )
    return subject, body


def _finalize_outreach_body(body: str, sender_full_name: Optional[str] = None) -> str:
    sender_name = (sender_full_name or "Your Full Name").strip() or "Your Full Name"
    normalized = body.strip()
    lower = normalized.lower()

    if "resume attached" not in lower and "find my resume attached" not in lower:
        normalized += "\n\nPlease find my resume attached for your review."

    if "best regards" not in lower:
        normalized += f"\n\nBest Regards,\n{sender_name}"
    elif sender_name.lower() not in lower:
        normalized += f"\n{sender_name}"

    return normalized


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/jobs/search", response_model=JobsResponse)
async def search_jobs(payload: JobsRequest) -> JobsResponse:
    jobs = await job_service.search_jobs(payload.role, payload.location or "remote", payload.limit)
    return JobsResponse(
        query={"role": payload.role, "location": payload.location, "limit": payload.limit},
        jobs=jobs,
    )


@app.post("/resume/revise", response_model=LLMResponse)
async def revise_resume(payload: ResumeReviseRequest) -> LLMResponse:
    system = "You are an expert career coach and resume reviewer."
    user = (
        f"Target role: {payload.target_role}\n"
        f"Key skills: {', '.join(payload.key_skills) if payload.key_skills else 'N/A'}\n"
        "Please revise this resume for impact and ATS friendliness.\n"
        "Return:\n"
        "1) Improved resume text\n"
        "2) Top 5 changes made\n\n"
        f"Resume:\n{payload.current_resume}"
    )
    try:
        text = await openclaw_client.complete(system, user)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "LLM service is unavailable. Check OPENCLAW_API_URL and make sure the model server is running. "
                f"({exc})"
            ),
        ) from exc
    return LLMResponse(text=text)


@app.post("/email/draft", response_model=LLMResponse)
async def draft_email(payload: DraftEmailRequest) -> LLMResponse:
    system = "You are a professional communication assistant specialized in job search emails."
    user = (
        f"Purpose: {payload.purpose}\n"
        f"Recipient name: {payload.recipient_name}\n"
        f"Tone: {payload.tone}\n"
        f"Context: {payload.context}\n\n"
        "Draft a polished email with a clear subject line and call to action."
    )
    try:
        text = await openclaw_client.complete(system, user)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "LLM service is unavailable. Check OPENCLAW_API_URL and make sure the model server is running. "
                f"({exc})"
            ),
        ) from exc
    return LLMResponse(text=text)


@app.post("/email/outreach", response_model=OutreachEmailResponse)
async def draft_outreach_email(payload: OutreachEmailRequest) -> OutreachEmailResponse:
    sender_name = (payload.sender_full_name or "Your Full Name").strip() or "Your Full Name"
    system = "You are an outreach email assistant for job seekers."
    user = (
        f"Recipient name: {payload.recipient_name}\n"
        f"Role: {payload.role}\n"
        f"Company: {payload.company}\n"
        f"Sender full name: {sender_name}\n"
        f"Tone: {payload.tone}\n\n"
        "Use the resume details below to draft a concise, high-conversion outreach email.\n"
        "The email must ask the recipient to review the attached resume.\n"
        "The email must end exactly with:\n"
        "Best Regards,\n"
        f"{sender_name}\n\n"
        "Return format strictly:\n"
        "Subject: <subject line>\n"
        "<email body>\n\n"
        f"Resume details:\n{payload.resume_text}"
    )
    try:
        llm_text = await openclaw_client.complete(system, user)
        subject, body = _split_subject_and_body(llm_text)
    except Exception:
        subject, body = _fallback_outreach_draft(
            recipient_name=payload.recipient_name,
            role=payload.role,
            company=payload.company,
            resume_text=payload.resume_text,
        )

    body = _finalize_outreach_body(body, payload.sender_full_name)

    if not payload.send_now:
        return OutreachEmailResponse(
            subject=subject,
            body=body,
            sent=False,
            message_id=None,
            status="Draft generated. Set send_now=true to send via Gmail API.",
        )

    if not payload.resume_file_base64:
        raise HTTPException(
            status_code=400,
            detail="resume_file_base64 is required when send_now=true. Please upload a resume PDF.",
        )

    connected, _ = gmail_oauth_service.get_status(payload.telegram_user_id)
    if not connected:
        connect_url = gmail_oauth_service.get_connect_url(payload.telegram_user_id)
        return OutreachEmailResponse(
            subject=subject,
            body=body,
            sent=False,
            message_id=None,
            status="Gmail is not connected for this user. Connect Gmail first, then send again.",
            connect_url=connect_url,
        )

    try:
        attachment_bytes: Optional[bytes] = None
        attachment_filename: Optional[str] = None
        if payload.resume_file_base64:
            try:
                attachment_bytes = base64.b64decode(payload.resume_file_base64)
                attachment_filename = payload.resume_filename or "resume.pdf"
            except (binascii.Error, ValueError) as exc:
                raise HTTPException(status_code=400, detail=f"Invalid resume_file_base64 payload: {exc}") from exc

        sent, message_id, status = await asyncio.to_thread(
            gmail_service.send_email,
            payload.telegram_user_id,
            payload.to_email,
            subject,
            body,
            attachment_filename,
            attachment_bytes,
        )
        return OutreachEmailResponse(
            subject=subject,
            body=body,
            sent=sent,
            message_id=message_id,
            status=status,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to send outreach email: {exc}") from exc


@app.get("/gmail/connect-link", response_model=GmailConnectLinkResponse)
async def gmail_connect_link(telegram_user_id: int = Query(..., ge=1)) -> GmailConnectLinkResponse:
    try:
        connect_url = gmail_oauth_service.get_connect_url(telegram_user_id)
        return GmailConnectLinkResponse(connect_url=connect_url)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/gmail/status/{telegram_user_id}", response_model=GmailConnectionStatusResponse)
async def gmail_connection_status(telegram_user_id: int) -> GmailConnectionStatusResponse:
    connected, sender_email = gmail_oauth_service.get_status(telegram_user_id)
    return GmailConnectionStatusResponse(connected=connected, sender_email=sender_email)


@app.post("/gmail/disconnect", response_model=GmailConnectionStatusResponse)
async def gmail_disconnect(payload: GmailDisconnectRequest) -> GmailConnectionStatusResponse:
    gmail_oauth_service.disconnect(payload.telegram_user_id)
    return GmailConnectionStatusResponse(connected=False, sender_email=None)


@app.get("/oauth/gmail/callback", response_class=HTMLResponse)
async def gmail_oauth_callback(code: str, state: str) -> HTMLResponse:
    try:
        telegram_user_id, sender_email = await gmail_oauth_service.complete_oauth_callback(code, state)
        return HTMLResponse(
            content=(
                "<html><body style='font-family: sans-serif; padding: 24px;'>"
                "<h2>Gmail connected successfully</h2>"
                f"<p>Telegram user ID: <b>{telegram_user_id}</b></p>"
                f"<p>Connected Gmail: <b>{sender_email}</b></p>"
                "<p>You can close this tab and return to Telegram.</p>"
                "</body></html>"
            ),
            status_code=200,
        )
    except Exception as exc:
        return HTMLResponse(
            content=(
                "<html><body style='font-family: sans-serif; padding: 24px;'>"
                "<h2>Gmail connection failed</h2>"
                f"<p>{exc}</p>"
                "<p>Please retry from Telegram.</p>"
                "</body></html>"
            ),
            status_code=400,
        )


@app.post("/interview/prepare", response_model=LLMResponse)
async def prepare_interview(payload: InterviewPrepRequest) -> LLMResponse:
    system = "You are a job interview preparation coach."
    focus = ", ".join(payload.focus_areas) if payload.focus_areas else "general interview readiness"
    user = (
        f"Role: {payload.role}\n"
        f"Company: {payload.company}\n"
        f"Focus areas: {focus}\n\n"
        "Create a practical interview prep plan including:\n"
        "1) likely questions with strong sample answers\n"
        "2) technical and behavioral prep checklist\n"
        "3) 3 smart questions to ask interviewer\n"
        "4) 24-hour prep timeline"
    )
    try:
        text = await openclaw_client.complete(system, user)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "LLM service is unavailable. Check OPENCLAW_API_URL and make sure the model server is running. "
                f"({exc})"
            ),
        ) from exc
    return LLMResponse(text=text)


@app.post("/notion/track", response_model=TrackJobResponse)
async def track_job(payload: TrackJobRequest) -> TrackJobResponse:
    try:
        message, page_id = await notion_service.track_job(
            company=payload.company,
            role=payload.role,
            status=payload.status,
            link=payload.link,
            notes=payload.notes,
        )
        return TrackJobResponse(message=message, page_id=page_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to track job: {exc}") from exc
    
@app.post("/resume/tailor", response_model=LLMResponse)
async def tailor_resume(payload: ResumeTailorRequest) -> LLMResponse:
    system = (
        "You are an expert resume writer helping a candidate land their target job. "
        "Return ONLY valid JSON — no markdown fences, no preamble."
    )
    user = (
        f"Job title: {payload.job_title}\n"
        f"Company: {payload.company}\n\n"
        f"Job description:\n{payload.job_description}\n\n"
        f"Candidate resume:\n{payload.resume_text}\n\n"
        "Rewrite the experience and project bullet points so they:\n"
        "1. Use keywords from the job description naturally\n"
        "2. Quantify achievements where the resume already mentions them\n"
        "3. Lead with strong action verbs matching the JD language\n"
        "4. NEVER invent experience or skills not in the original resume\n\n"
        'Return JSON only:\n'
        '{\n'
        '  "rewritten_bullets": [\n'
        '    {"original": "old bullet", "improved": "new bullet", "reason": "why better"}\n'
        '  ],\n'
        '  "keywords_added": ["keyword1", "keyword2"],\n'
        '  "match_score": 85\n'
        '}'
    )
    try:
        text = await openclaw_client.complete(system, user)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"LLM unavailable: {exc}") from exc
    return LLMResponse(text=text)


@app.get("/notion/summary", response_model=NotionSummaryResponse)
async def get_notion_summary() -> NotionSummaryResponse:
    try:
        apps = await notion_service.get_summary()
        return NotionSummaryResponse(applications=apps, total=len(apps))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to fetch summary: {exc}") from exc
