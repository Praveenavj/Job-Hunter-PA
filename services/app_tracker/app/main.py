from fastapi import FastAPI
from pydantic import BaseModel
from notion_client import Client
import os
from datetime import date, timedelta
from dotenv import load_dotenv

load_dotenv()
app = FastAPI()
notion = Client(auth=os.getenv("NOTION_TOKEN"))
DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

class Application(BaseModel):
    company: str
    role: str
    status: str = "Applied"
    notes: str = ""

@app.post("/log")
async def log_application(app_data: Application):
    today = date.today()
    followup = today + timedelta(days=7)
    
    notion.pages.create(
        parent={"database_id": DATABASE_ID},
        properties={
            "Name": {"title": [{"text": {"content": f"{app_data.company} — {app_data.role}"}}]},
            "Company": {"rich_text": [{"text": {"content": app_data.company}}]},
            "Role": {"rich_text": [{"text": {"content": app_data.role}}]},
            "Status": {"select": {"name": app_data.status}},
            "Applied Date": {"date": {"start": str(today)}},
            "Follow-up Date": {"date": {"start": str(followup)}},
            "Notes": {"rich_text": [{"text": {"content": app_data.notes}}]},
        }
    )
    return {"logged": True, "company": app_data.company, "followup": str(followup)}

@app.get("/summary")
async def get_summary():
    results = notion.databases.query(database_id=DATABASE_ID)
    apps = []
    for page in results["results"]:
        props = page["properties"]
        apps.append({
            "company": props["Company"]["rich_text"][0]["plain_text"] if props["Company"]["rich_text"] else "",
            "role": props["Role"]["rich_text"][0]["plain_text"] if props["Role"]["rich_text"] else "",
            "status": props["Status"]["select"]["name"] if props["Status"]["select"] else "Unknown",
            "followup": props["Follow-up Date"]["date"]["start"] if props["Follow-up Date"]["date"] else None
        })
    return {"applications": apps, "total": len(apps)}

@app.get("/health")
async def health():
    return {"status": "ok", "service": "app_tracker"}