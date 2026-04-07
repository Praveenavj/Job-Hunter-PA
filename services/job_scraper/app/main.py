from fastapi import FastAPI, Query
import httpx
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()  

@app.get("/search")
async def search_jobs(skills: str = Query(...), location: str = Query("Singapore")):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://remotive.com/api/remote-jobs",
            params={"search": skills, "limit": 20},
            timeout=15.0
        )

    jobs = response.json().get("jobs", [])

    results = []
    for job in jobs[:5]:
        results.append({
            "title": job["title"],
            "company": job["company_name"],
            "url": job["url"]
        })

    return {"jobs": results}