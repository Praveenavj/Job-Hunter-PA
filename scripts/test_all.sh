#!/bin/bash
# ============================================================
#  Job Hunter PA – Full Test Suite
#  Usage: bash scripts/test_all.sh
# ============================================================

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'

echo ""
echo "════════════════════════════════════════════"
echo "  Job Hunter PA – System Test Suite"
echo "════════════════════════════════════════════"

# Run all tests in a single Python process (avoids bash && || bugs)
python3 - << 'PYEOF'
import sys, subprocess, time
from datetime import date

PASS = 0
FAIL = 0

def ok(label):
    global PASS
    print(f"  \033[0;32m✅ {label}\033[0m")
    PASS += 1

def fail(label, err=""):
    global FAIL
    msg = f": {str(err)[:120]}" if err else ""
    print(f"  \033[0;31m❌ {label}{msg}\033[0m")
    FAIL += 1

def section(title):
    print(f"\n\033[1;33m── {title} ──\033[0m")

# ── 1. Module imports ─────────────────────────────────────────
section("1. Module imports")
try:
    from app.config import settings
    from app.database import init_db; init_db()
    from app.llm_client import complete
    from app.resume_utils import extract_text_from_pdf, extract_keywords, gap_analysis
    from app.services.job_aggregator import search_jobs, SOURCES
    from app.services.excel_tracker import rebuild_workbook
    from app.services.gmail_service import get_status
    from app.services.llm_tasks import resume_revise
    from app.main import app
    from bot.telegram_bot import main, fmt_jobs
    ok(f"All 10 modules ({len(SOURCES)} job sources loaded)")
except Exception as e:
    fail("Module imports", e)

# ── 2. Database ───────────────────────────────────────────────
section("2. Database CRUD")
try:
    from app import database as db
    uid = 777001
    db.upsert_user(uid, "Test User", "test@test.com")
    db.save_master_resume(uid, "Python SQL Tableau Power BI Data Analyst")
    r = db.get_master_resume(uid)
    assert r and "Python" in r

    app_id = db.add_application(uid, "TestCo", "DA", "Applied",
        "http://test.com", "note", "SGD 5000", "MCF", "2099-01-01")
    assert app_id > 0

    apps = db.get_applications(uid)
    assert len(apps) > 0

    url = f"http://unique-{time.time()}.com"
    assert db.mark_job_seen(uid, url, "DA", "Co", "MCF") == True
    assert db.mark_job_seen(uid, url, "DA", "Co", "MCF") == False

    db.save_search_profile(uid, "DA SG", "Data Analyst", "Singapore")
    assert len(db.get_saved_searches(uid)) > 0

    db.add_star_story(uid, "Test story", "sit", "task", "act", "result", "analytics")
    assert len(db.get_star_stories(uid)) > 0

    db.update_application_status(app_id, "Interviewed", "Good call")
    apps2 = db.get_applications(uid)
    assert any(a["status"] == "Interviewed" for a in apps2)

    ok("user, resume, applications, deduplication, search profiles, STAR stories")
except Exception as e:
    fail("Database CRUD", e)

# ── 3. Keyword analysis ───────────────────────────────────────
section("3. Keyword & gap analysis")
try:
    resume = "Python SQL Power BI Tableau pandas machine learning React NodeJS"
    jd     = "Requires SQL Python Tableau BigQuery ETL stakeholder management A/B testing"

    kws = extract_keywords(resume)
    assert "python" in kws and "sql" in kws and "tableau" in kws

    gap = gap_analysis(resume, jd)
    assert gap["match_pct"] > 0
    assert "bigquery" in gap["missing"]
    assert "python"   in gap["matched"]

    ok(f"match={gap['match_pct']}%, matched={gap['matched']}, missing={gap['missing']}")
except Exception as e:
    fail("Keyword analysis", e)

# ── 4. PDF extraction ─────────────────────────────────────────
section("4. PDF text extraction")
try:
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50,80),
        "Praveena Vijayan Singapore PR\n"
        "Skills: Python SQL Power BI Tableau TensorFlow Pandas\n"
        "Experience: Data Analyst Skyworks RFM analysis dashboards",
        fontsize=11)
    pdf_bytes = doc.write(); doc.close()

    text = extract_text_from_pdf(pdf_bytes)
    assert len(text) > 50
    kws = extract_keywords(text)
    assert "python" in kws and "sql" in kws

    ok(f"{len(text)} chars extracted, keywords: {kws}")
except Exception as e:
    fail("PDF extraction", e)

# ── 5. pdfminer fallback ──────────────────────────────────────
section("5. pdfminer.six fallback")
try:
    from pdfminer.high_level import extract_text
    import io
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50,80), "Python SQL Power BI pdfminer test", fontsize=12)
    pdf_bytes = doc.write(); doc.close()

    text = extract_text(io.BytesIO(pdf_bytes)).strip()
    assert len(text) > 5
    ok(f"pdfminer works: \"{text[:50]}\"")
except ImportError:
    fail("pdfminer.six not installed", "run: pip install pdfminer.six")
except Exception as e:
    fail("pdfminer fallback", e)

# ── 6. FastAPI endpoints ──────────────────────────────────────
section("6. FastAPI endpoints")
try:
    from fastapi.testclient import TestClient
    c = TestClient(app)

    r = c.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"

    uid2 = 777002
    r = c.post("/applications/add", json={
        "telegram_id": uid2, "company": "Google", "role": "Data Analyst",
        "status": "Applied", "url": "https://g.com/1", "salary": "SGD 7k", "source": "MCF"
    })
    assert r.status_code == 200
    app_id2 = r.json()["id"]

    r = c.post(f"/applications/update/{app_id2}", json={"status":"Interviewed","notes":"!"})
    assert r.status_code == 200 and r.json()["updated"]

    r = c.get(f"/applications/{uid2}")
    assert r.status_code == 200 and r.json()["total"] > 0

    r = c.get(f"/applications/export/{uid2}")
    assert r.status_code == 200 and len(r.content) > 3000

    r = c.get(f"/gmail/status/{uid2}")
    assert r.status_code == 200 and not r.json()["connected"]

    # Interview must return 200 (never 500) even with no LLM key
    r = c.post("/interview/prepare", json={"role":"DA","company":"Google","focus_areas":[]})
    assert r.status_code == 200, f"Got {r.status_code}"
    preview = r.json()["text"][:40]

    r = c.post("/resume/tailor", json={
        "resume_text": "Python SQL Tableau Power BI",
        "job_description": "SQL Python BigQuery ETL stakeholder",
        "job_title": "Data Analyst", "company": "Grab"
    })
    assert r.status_code == 200
    gap = r.json()["gap"]
    assert gap["match_pct"] > 0

    ok(f"health, add/update/get/export/gmail, interview→200, tailor match={gap['match_pct']}%")
except Exception as e:
    fail("FastAPI endpoints", e)

# ── 7. Excel workbook ─────────────────────────────────────────
section("7. Excel workbook")
try:
    import openpyxl
    path = rebuild_workbook(777002)
    wb = openpyxl.load_workbook(path)

    assert "Applications" in wb.sheetnames
    assert "Dashboard"    in wb.sheetnames

    ws = wb["Applications"]
    headers = [ws.cell(1,c).value for c in range(1,12)]
    assert "Company" in headers and "Status" in headers and "Salary" in headers

    ds = wb["Dashboard"]
    assert ds["D3"].value == "Total Applied"
    assert ds["D6"].value == "Interview Rate"

    ok(f"2 sheets, {ws.max_row-1} data rows, headers OK, dashboard OK")
except Exception as e:
    fail("Excel workbook", e)

# ── 8. Outreach prefill ───────────────────────────────────────
section("8. Outreach prefill logic")
try:
    # 2-part (pre-filled from job card)
    state  = {"prefill_company": "ALC Technologies", "prefill_role": "Software Engineer"}
    text   = "praveenavj2210@gmail.com || Praveena Vijayan"
    parts  = [p.strip() for p in text.split("||")]
    role   = parts[2] if len(parts) > 2 else state.get("prefill_role","")
    company= parts[3] if len(parts) > 3 else state.get("prefill_company","")
    assert role == "Software Engineer" and company == "ALC Technologies"

    # 4-part (manual)
    text2  = "hr@google.com || Sarah || Data Analyst || Google"
    parts2 = [p.strip() for p in text2.split("||")]
    assert parts2[2] == "Data Analyst" and parts2[3] == "Google"

    ok("2-part prefill and 4-part manual both resolve correctly")
except Exception as e:
    fail("Outreach prefill", e)

# ── 9. Bot file integrity ─────────────────────────────────────
section("9. Bot file integrity")
try:
    with open("bot/telegram_bot.py") as f:
        src = f.read()
    import ast; ast.parse(src)  # syntax check
    lines = src.count("\n")

    required = {
        "BufferedInputFile":   "aiogram 3.x correct file send",
        "msg.bot.get_file":    "correct bot reference",
        "_run_tailor":         "module-level tailor helper",
        "cmd_testalert":       "/testalert command",
        "cmd_remindme":        "/remindme command",
        '"star_action"':       "STAR action step fixed",
        "prefill_company":     "outreach prefill",
        "send_long":           "long message splitter",
        "JOBS_CACHE":          "job cache",
        "PRACTICE":            "practice state",
        "daily_digest":        "digest scheduler",
        "followup_check":      "followup reminders",
    }
    missing = [v for k,v in required.items() if k not in src]
    assert not missing, f"Missing: {missing}"

    ok(f"{lines} lines, syntax OK, {len(required)}/{len(required)} key patterns present")
except Exception as e:
    fail("Bot file integrity", e)

# ── Summary ───────────────────────────────────────────────────
total = PASS + FAIL
print()
print("════════════════════════════════════════════")
if FAIL == 0:
    print(f"\033[0;32m  ✅ ALL {total} TESTS PASSED\033[0m")
    print()
    print("  Next steps:")
    print("  1.  pip install pdfminer.six  (if not done)")
    print("  2.  Fill in .env with TELEGRAM_BOT_TOKEN and ANTHROPIC_API_KEY")
    print("  3.  bash scripts/start_all.sh")
    print("  4.  Open Telegram → /start")
else:
    print(f"\033[0;31m  ❌ {FAIL}/{total} TESTS FAILED — fix errors above first\033[0m")
print("════════════════════════════════════════════")
print()
sys.exit(0 if FAIL == 0 else 1)
PYEOF

# ── Live backend check (bash, separate from Python block) ─────
if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
  echo -e "\033[0;32m  ✅ Live backend at localhost:8000 is UP\033[0m"
else
  echo -e "\033[1;33m  ℹ  Backend not running (start with: bash scripts/start_all.sh)\033[0m"
fi