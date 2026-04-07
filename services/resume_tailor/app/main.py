from fastapi import FastAPI
from pydantic import BaseModel
import anthropic
import os
from dotenv import load_dotenv

load_dotenv()
app = FastAPI()
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

class TailorRequest(BaseModel):
    job_description: str
    resume_text: str
    job_title: str = ""
    company: str = ""

@app.post("/tailor")
async def tailor_resume(req: TailorRequest):
    prompt = f"""You are an expert resume writer helping a student land their first job.

JOB DESCRIPTION:
{req.job_description}

CANDIDATE'S CURRENT RESUME:
{req.resume_text}

TASK:
Rewrite the experience and project bullet points so they:
1. Use keywords from the job description naturally
2. Quantify achievements where the resume already mentions them
3. Lead with strong action verbs matching the JD's language
4. NEVER invent experience or skills not in the original resume

Return ONLY a JSON object with this structure:
{{
  "rewritten_bullets": [
    {{"original": "old bullet", "improved": "new bullet", "reason": "why this is better"}}
  ],
  "keywords_added": ["list", "of", "jd", "keywords", "used"],
  "match_score": 85
}}"""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )
    
    import json, re
    raw = message.content[0].text
    # Strip markdown code fences if present
    clean = re.sub(r"```json|```", "", raw).strip()
    return json.loads(clean)

@app.get("/health")
async def health():
    return {"status": "ok", "service": "resume_tailor"}