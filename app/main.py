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
    text = await openclaw_client.complete(system, user)
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
    text = await openclaw_client.complete(system, user)
    return LLMResponse(text=text)


@app.post("/email/outreach", response_model=OutreachEmailResponse)
async def draft_outreach_email(payload: OutreachEmailRequest) -> OutreachEmailResponse:
    system = "You are an outreach email assistant for job seekers."
    user = (
        f"Recipient name: {payload.recipient_name}\n"
        f"Role: {payload.role}\n"
        f"Company: {payload.company}\n"
        f"Tone: {payload.tone}\n\n"
        "Use the resume details below to draft a concise, high-conversion outreach email.\n"
        "Return format strictly:\n"
        "Subject: <subject line>\n"
        "<email body>\n\n"
        f"Resume details:\n{payload.resume_text}"
    )
    llm_text = await openclaw_client.complete(system, user)
    subject, body = _split_subject_and_body(llm_text)

    if not payload.send_now:
        return OutreachEmailResponse(
            subject=subject,
            body=body,
            sent=False,
            message_id=None,
            status="Draft generated. Set send_now=true to send via Gmail API.",
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
        sent, message_id, status = await asyncio.to_thread(
            gmail_service.send_email,
            payload.telegram_user_id,
            payload.to_email,
            subject,
            body,
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
    text = await openclaw_client.complete(system, user)
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
