"""
Indeed – public RSS feed (no scraping, no auth, fully legitimate).
URL: https://www.indeed.com/rss?q={query}&l={location}&sort=date
"""
import httpx
import feedparser
import logging
from datetime import datetime
from . import BaseJobSource, JobPosting

logger = logging.getLogger(__name__)


class IndeedRSSSource(BaseJobSource):
    name = "Indeed"

    async def search_jobs(self, query: str, location: str = "singapore", limit: int = 20) -> list[JobPosting]:
        import urllib.parse
        loc = "Singapore" if location.lower() in ("sg", "singapore") else location
        # Use Indeed's job search page with RSS - regional endpoint avoids 404
        params = {"q": query, "l": loc, "sort": "date", "fromage": "14"}
        rss_url = f"https://www.indeed.com/rss?{urllib.parse.urlencode(params)}"
        # Fallback: sg.indeed.com
        sg_url  = f"https://sg.indeed.com/rss?{urllib.parse.urlencode(params)}"

        for url in [rss_url, sg_url]:
            try:
                async with httpx.AsyncClient(
                    timeout=15,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                        "Accept": "application/rss+xml, application/xml, text/xml",
                    },
                    follow_redirects=True,
                ) as client:
                    r = await client.get(url)
                    if r.status_code == 200:
                        feed = feedparser.parse(r.content)
                        if feed.entries:
                            break
            except Exception as e:
                logger.warning(f"Indeed URL {url} failed: {e}")
                continue
        else:
            logger.error("Indeed RSS: all URLs failed")
            return []

        jobs = []
        for entry in feed.entries[:limit]:
            try:
                posted_at = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    posted_at = datetime(*entry.published_parsed[:6])
                # Extract company from title format "Job Title - Company Name"
                raw_title = entry.get("title", "Unknown")
                if " - " in raw_title:
                    title_part, company_part = raw_title.rsplit(" - ", 1)
                else:
                    title_part, company_part = raw_title, entry.get("author", "Unknown")
                jobs.append(JobPosting(
                    title=title_part.strip(),
                    company=company_part.strip(),
                    location=loc,
                    url=entry.get("link", ""),
                    source="Indeed",
                    description=(entry.get("summary") or "")[:500],
                    posted_at=posted_at,
                ))
            except Exception as e:
                logger.debug(f"Indeed parse: {e}")
        logger.info(f"Indeed: {len(jobs)} jobs for '{query}'")
        return jobs
       