"""
Resume utilities:
  - PDF text extraction (PyMuPDF / fitz)
  - Keyword extraction from resume text
  - Keyword extraction from job descriptions
  - Gap analysis between resume and JD
"""
import re
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Comprehensive keyword bank ────────────────────────────────────────────────
TECH_KEYWORDS = {
    # Languages
    "python","java","javascript","typescript","sql","r","scala","c++","c#","go",
    "rust","kotlin","php","ruby","swift","bash","vba","matlab","julia",
    # Databases
    "mysql","postgresql","mongodb","redis","sqlite","dynamodb","snowflake",
    "bigquery","oracle","elasticsearch","cassandra","hive","presto",
    # Cloud & Infra
    "aws","gcp","azure","docker","kubernetes","terraform","ci/cd","jenkins",
    "github actions","airflow","spark","hadoop","kafka","databricks",
    # Analytics & BI
    "tableau","power bi","qlik","looker","excel","pandas","numpy","matplotlib",
    "seaborn","plotly","d3","google analytics","mixpanel",
    # ML / AI
    "machine learning","deep learning","nlp","tensorflow","pytorch","keras",
    "scikit-learn","xgboost","llm","computer vision","regression","classification",
    "clustering","neural network","transformers","bert","gpt",
    # Web & frameworks
    "react","angular","vue","node","express","django","flask","fastapi",
    "rest api","graphql","html","css",
    # Methodologies
    "agile","scrum","kanban","devops","tdd","oop","data modelling","etl",
    "data warehouse","data pipeline","a/b testing","statistics",
    # Soft / business
    "project management","stakeholder management","communication","leadership",
    "problem solving","critical thinking","business analysis","product management",
    "user research","ux","data-driven","kpi","crm","erp",
}


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """
    Extract plain text from PDF using three methods in order:
      1. PyMuPDF (fitz) – fastest, handles most PDFs
      2. pdfminer.six   – better with unusual encodings
      3. Raw text scan  – last resort byte-level search
    Returns empty string only if all three fail.
    """
    # ── Method 1: PyMuPDF ────────────────────────────────────────────────────
    try:
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = "\n".join(page.get_text() for page in doc).strip()
        if text and len(text) > 50:
            logger.info(f"PDF extracted via PyMuPDF: {len(text)} chars")
            return text
        # Try with different extraction mode
        text = "\n".join(page.get_text("text") for page in doc).strip()
        if text and len(text) > 50:
            logger.info(f"PDF extracted via PyMuPDF (text mode): {len(text)} chars")
            return text
    except Exception as e:
        logger.warning(f"PyMuPDF failed: {e}")

    # ── Method 2: pdfminer.six ───────────────────────────────────────────────
    try:
        from pdfminer.high_level import extract_text as pdfminer_extract
        import io
        text = pdfminer_extract(io.BytesIO(pdf_bytes)).strip()
        if text and len(text) > 50:
            logger.info(f"PDF extracted via pdfminer: {len(text)} chars")
            return text
    except ImportError:
        logger.warning("pdfminer.six not installed – run: pip install pdfminer.six")
    except Exception as e:
        logger.warning(f"pdfminer failed: {e}")

    # ── Method 3: Raw byte scan ──────────────────────────────────────────────
    try:
        raw = pdf_bytes.decode("latin-1", errors="replace")
        import re as _re
        # Extract printable ASCII runs from raw PDF bytes
        chunks = _re.findall(r'[A-Za-z0-9 ,.\-\n\t\'\"@:;/()]{20,}', raw)
        text = "\n".join(chunks).strip()
        if text and len(text) > 50:
            logger.warning(f"PDF extracted via raw scan (fallback): {len(text)} chars")
            return text
    except Exception as e:
        logger.error(f"Raw PDF scan failed: {e}")

    logger.error("All PDF extraction methods failed")
    return ""


def extract_keywords(text: str) -> list[str]:
    """Return sorted list of recognised keywords found in text."""
    text_lower = text.lower()
    found = []
    for kw in TECH_KEYWORDS:
        # word boundary check to avoid partial matches
        pattern = r'\b' + re.escape(kw) + r'\b'
        if re.search(pattern, text_lower):
            found.append(kw)
    return sorted(found)


def extract_jd_requirements(jd_text: str) -> dict:
    """
    Parse a job description and return structured info:
    {
        keywords: [...],
        required: [...],     # lines after 'requirements' / 'must have'
        preferred: [...],    # lines after 'nice to have' / 'preferred'
        experience_years: N or None,
    }
    """
    keywords = extract_keywords(jd_text)

    # Rough section splitting
    jd_lower = jd_text.lower()
    lines = jd_text.splitlines()

    required, preferred = [], []
    mode = None
    for line in lines:
        ll = line.lower().strip()
        if any(h in ll for h in ["requirement", "must have", "you will have", "you must"]):
            mode = "required"
        elif any(h in ll for h in ["nice to have", "preferred", "bonus", "advantage"]):
            mode = "preferred"
        stripped = line.strip().lstrip("-•*·").strip()
        if stripped and len(stripped) > 8:
            if mode == "required":
                required.append(stripped)
            elif mode == "preferred":
                preferred.append(stripped)

    # Try to extract years of experience
    exp_match = re.search(r'(\d+)\+?\s*year', jd_lower)
    experience_years = int(exp_match.group(1)) if exp_match else None

    return {
        "keywords": keywords,
        "required": required[:10],
        "preferred": preferred[:5],
        "experience_years": experience_years,
    }


def gap_analysis(resume_text: str, jd_text: str) -> dict:
    """
    Compare resume keywords vs JD keywords.
    Returns:
    {
        resume_keywords: [...],
        jd_keywords: [...],
        matched: [...],
        missing: [...],
        match_pct: 72,
    }
    """
    resume_kws = set(extract_keywords(resume_text))
    jd_kws     = set(extract_keywords(jd_text))

    matched = sorted(resume_kws & jd_kws)
    missing = sorted(jd_kws - resume_kws)
    match_pct = round(len(matched) / len(jd_kws) * 100) if jd_kws else 0

    return {
        "resume_keywords": sorted(resume_kws),
        "jd_keywords":     sorted(jd_kws),
        "matched":         matched,
        "missing":         missing,
        "match_pct":       match_pct,
    }
