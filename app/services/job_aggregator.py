"""
Job Aggregator – searches all sources in parallel, deduplicates, ranks.
Stores seen URLs per user so the daily digest never repeats a job.
"""
import asyncio
import logging
from datetime import datetime
from app.services.job_sources import JobPosting
from app.services.job_sources.mycareersfuture import MyCareersFutureSource
from app.services.job_sources.indeed_rss import IndeedRSSSource
from app.services.job_sources.jobicy_source import JobicySource
from app.services.job_sources.adzuna_source import AdzunaSource
from app.services.job_sources.careers_gov import CareersGovSource
from app.services.job_sources.jora_rss import JoraRSSSource
from app import database as db

logger = logging.getLogger(__name__)

# All sources, priority order (6 sources total)
SOURCES = [
    MyCareersFutureSource(),
    AdzunaSource(),
    IndeedRSSSource(),
    CareersGovSource(),
    JoraRSSSource(),
    JobicySource(),
]

# Source credibility weights for ranking
SOURCE_SCORE = {
    "MyCareersFuture": 22,
    "Adzuna":          20,
    "Indeed":          18,
    "Careers@Gov":     18,
    "Jora":            15,
    "Jobicy":          13,
}


async def search_jobs(
    query: str,
    location: str = "singapore",
    limit: int = 10,
    telegram_id: int | None = None,
    new_only: bool = False,
) -> list[dict]:
    """
    Search all sources in parallel.
    If telegram_id provided and new_only=True, filters to unseen jobs only.
    Returns list of dicts ready for display.
    """
    logger.info(f"Searching: '{query}' in {location}")

    # Fan-out in parallel
    tasks = [src.search_jobs(query, location, limit=30) for src in SOURCES]
    results_per_source = await asyncio.gather(*tasks, return_exceptions=True)

    all_jobs: list[JobPosting] = []
    for i, result in enumerate(results_per_source):
        if isinstance(result, Exception):
            logger.warning(f"Source {SOURCES[i].name} failed: {result}")
        else:
            all_jobs.extend(result)

    # Deduplicate by URL (normalised)
    seen_urls: set[str] = set()
    unique: list[JobPosting] = []
    for job in all_jobs:
        norm = _norm_url(job.url)
        if norm and norm not in seen_urls:
            seen_urls.add(norm)
            unique.append(job)

    # If new_only, filter against DB seen-jobs
    if telegram_id and new_only:
        fresh = []
        for job in unique:
            is_new = db.mark_job_seen(telegram_id, job.url, job.title, job.company, job.source)
            if is_new:
                fresh.append(job)
        unique = fresh

    # Rank
    ranked = sorted(unique, key=lambda j: _score(j, query), reverse=True)

    return [_to_dict(j) for j in ranked[:limit]]


def _norm_url(url: str) -> str:
    if not url:
        return ""
    url = url.lower().strip().rstrip("/").split("?")[0]
    return url


def _score(job: JobPosting, query: str) -> float:
    score = 0.0
    q = query.lower()
    title = (job.title or "").lower()

    if q in title:
        score += 40
    elif any(w in title for w in q.split()):
        score += 20

    if job.salary_max:
        if job.salary_max >= 8000:
            score += 20
        elif job.salary_max >= 5000:
            score += 12

    score += SOURCE_SCORE.get(job.source, 10)

    if job.posted_at:
        days_old = max(0, (datetime.utcnow() - job.posted_at).days)
        if days_old <= 1:
            score += 20
        elif days_old <= 7:
            score += 14
        elif days_old <= 30:
            score += 7

    return score


def _to_dict(j: JobPosting) -> dict:
    sal_str = None
    if j.salary_min and j.salary_max:
        sal_str = f"SGD {j.salary_min:,.0f}–{j.salary_max:,.0f}/mo"
    elif j.salary_min:
        sal_str = f"SGD {j.salary_min:,.0f}+/mo"
    return {
        "title": j.title,
        "company": j.company,
        "location": j.location,
        "url": j.url,
        "source": j.source,
        "job_type": j.job_type,
        "salary": sal_str,
        "description": (j.description or "")[:300],
        "posted_at": j.posted_at.strftime("%d %b %Y") if j.posted_at else None,
    }
