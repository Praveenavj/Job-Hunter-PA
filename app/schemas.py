from typing import Any, Optional

from pydantic import BaseModel, Field


class JobsRequest(BaseModel):
    role: str
    location: Optional[str] = "remote"
    limit: int = Field(default=5, ge=1, le=20)


class ResumeReviseRequest(BaseModel):
    current_resume: str
    target_role: str
    key_skills: list[str] = Field(default_factory=list)


class DraftEmailRequest(BaseModel):
    purpose: str
    recipient_name: str
    context: str
    tone: str = "professional"


class OutreachEmailRequest(BaseModel):
    telegram_user_id: int
    to_email: str
    recipient_name: str
    role: str
    company: str
    resume_text: str
    tone: str = "professional"
    send_now: bool = False


class InterviewPrepRequest(BaseModel):
    role: str
    company: str
    focus_areas: list[str] = Field(default_factory=list)


class TrackJobRequest(BaseModel):
    company: str
    role: str
    status: str = "Applied"
    link: Optional[str] = None
    notes: Optional[str] = None


class LLMResponse(BaseModel):
    text: str


class JobsResponse(BaseModel):
    query: dict[str, Any]
    jobs: list[dict[str, Any]]


class TrackJobResponse(BaseModel):
    message: str
    page_id: Optional[str] = None


class OutreachEmailResponse(BaseModel):
    subject: str
    body: str
    sent: bool
    message_id: Optional[str] = None
    status: str
    connect_url: Optional[str] = None


class GmailConnectionStatusResponse(BaseModel):
    connected: bool
    sender_email: Optional[str] = None


class GmailConnectLinkResponse(BaseModel):
    connect_url: str


class GmailDisconnectRequest(BaseModel):
    telegram_user_id: int
