from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from notion_client import AsyncClient

from app.config import settings


class NotionService:
    async def track_job(
        self,
        company: str,
        role: str,
        status: str,
        link: Optional[str],
        notes: Optional[str],
    ) -> tuple[str, Optional[str]]:
        if not settings.notion_api_key or not settings.notion_database_id:
            return (
                "Notion is not configured. Set NOTION_API_KEY and NOTION_DATABASE_ID.",
                None,
            )

        client = AsyncClient(auth=settings.notion_api_key)

        today = date.today()
        followup = today + timedelta(days=7)

        properties = {
            "Company": {"title": [{"text": {"content": company}}]},
            "Role": {"rich_text": [{"text": {"content": role}}]},
            "Status": {"select": {"name": status}},
            "Applied Date": {"date": {"start": str(today)}},
            "Follow-up Date": {"date": {"start": str(followup)}},
        }

        if link:
            properties["Link"] = {"url": link}

        if notes:
            properties["Notes"] = {"rich_text": [{"text": {"content": notes}}]}

        response = await client.pages.create(
            parent={"database_id": settings.notion_database_id},
            properties=properties,
        )
        return (
            f"Job tracked! Follow-up reminder set for {followup}.",
            response.get("id"),
        )

    async def get_summary(self) -> list[dict]:
        if not settings.notion_api_key or not settings.notion_database_id:
            return []

        client = AsyncClient(auth=settings.notion_api_key)
        results = await client.databases.query(database_id=settings.notion_database_id)

        apps = []
        for page in results.get("results", []):
            props = page.get("properties", {})
            company_prop = props.get("Company", {}).get("title", [])
            role_prop = props.get("Role", {}).get("rich_text", [])
            status_prop = props.get("Status", {}).get("select")
            followup_prop = props.get("Follow-up Date", {}).get("date")
            apps.append({
                "company": company_prop[0]["plain_text"] if company_prop else "",
                "role": role_prop[0]["plain_text"] if role_prop else "",
                "status": status_prop["name"] if status_prop else "Unknown",
                "followup": followup_prop["start"] if followup_prop else None,
            })
        return apps


notion_service = NotionService()