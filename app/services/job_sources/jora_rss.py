"""
Jora – public RSS feed, 175K+ Singapore listings.
Falls back gracefully if blocked.
"""
import httpx
import feedparser
import logging
import urllib.parse
from datetime import datetime
from . import BaseJobSource, JobPosting

logger = logging.getLogger(__name__)


class JoraRSSSource(BaseJobSource):
    name = "Jora"

    async def search_jobs(self, query: str, location: str = "singapore",
                          limit: int = 20) -> list[JobPosting]:
        loc = "singapore" if location.lower() in ("sg", "singapore") else location
        # Jora Singapore uses sg.jora.com
        params = {"k": query, "l": loc}
        urls = [
            f"https://sg.jora.com/j?{urllib.parse.urlencode(params)}&rss=1",
            f"https://jora.com/rss?{urllib.parse.urlencode({'k': query, 'l': loc})}",
        ]
        feed = None
        for url in urls:
            try:
                async with httpx.AsyncClient(
                    timeout=12,
                    headers={"User-Agent": "Mozilla/5.0", "Accept": "application/rss+xml"},
                    follow_redirects=True,
                ) as client:
                    r = await client.get(url)
                    if r.status_code == 200:
                        feed = feedparser.parse(r.content)
                        if feed.entries:
                            break
            except Exception as e:
                logger.debug(f"Jora URL {url}: {e}")
                continue

        if not feed or not feed.entries:
            logger.warning("Jora: no results (blocked or empty)")
            return []

        jobs = []
        for entry in feed.entries[:limit]:
            try:
                posted_at = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    posted_at = datetime(*entry.published_parsed[:6])
                raw_title = entry.get("title", "Unknown")
                # Jora format: "Job Title - Company Name"
                if " - " in raw_title:
                    title_part, co_part = raw_title.rsplit(" - ", 1)
                else:
                    title_part, co_part = raw_title, "Unknown"
                jobs.append(JobPosting(
                    title=title_part.strip(),
                    company=co_part.strip(),
                    location="Singapore",
                    url=entry.get("link", ""),
                    source="Jora",
                    description=(entry.get("summary") or "")[:400],
                    posted_at=posted_at,
                ))
            except Exception as e:
                logger.debug(f"Jora parse: {e}")

        logger.info(f"Jora: {len(jobs)} jobs for '{query}'")
        return jobs
    