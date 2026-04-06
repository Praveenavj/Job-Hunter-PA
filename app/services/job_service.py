from __future__ import annotations

from typing import Any, Optional

import httpx

from app.config import settings


class JobService:
    """Multi-source jobs service for remote and Singapore searches."""

    REMOTIVE_BASE_URL = "https://remotive.com/api/remote-jobs"
    JOBICY_BASE_URL = "https://jobicy.com/api/v2/remote-jobs"
    ADZUNA_BASE_URL = "https://api.adzuna.com/v1/api/jobs/sg/search/1"

    @staticmethod
    def _normalize(value: Optional[str]) -> str:
        return (value or "").strip().lower()

    @staticmethod
    def _dedupe_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for job in jobs:
            key = str(job.get("url") or job.get("title") or job.get("company") or job.get("source"))
            if key in seen:
                continue
            seen.add(key)
            unique.append(job)
        return unique

    @staticmethod
    def _matches(job: dict[str, Any], role_query: str, location_query: str) -> bool:
        haystack = " ".join(
            str(part or "").lower()
            for part in [
                job.get("title"),
                job.get("company"),
                job.get("location"),
                job.get("type"),
                job.get("source"),
                job.get("description"),
            ]
        )

        if role_query:
            role_tokens = [token for token in role_query.split() if token]
            if not all(token in haystack for token in role_tokens):
                return False

        if location_query and location_query not in {"remote", "any", "all"}:
            loc_text = str(job.get("location") or "").lower()
            if location_query not in loc_text:
                return False

        return True

    async def _search_remotive(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(self.REMOTIVE_BASE_URL)
            response.raise_for_status()
            data = response.json()

        jobs: list[dict[str, Any]] = []
        for item in data.get("jobs", []):
            jobs.append(
                {
                    "title": item.get("title"),
                    "company": item.get("company_name"),
                    "location": item.get("candidate_required_location"),
                    "url": item.get("url"),
                    "type": item.get("job_type"),
                    "published_at": item.get("publication_date"),
                    "source": "Remotive",
                }
            )
        return jobs

    async def _search_jobicy(self, role: str, location: str) -> list[dict[str, Any]]:
        params = {"count": 50, "geo": location}

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(self.JOBICY_BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()

        jobs: list[dict[str, Any]] = []
        for item in data.get("jobs", []):
            jobs.append(
                {
                    "title": item.get("jobTitle"),
                    "company": item.get("companyName"),
                    "location": item.get("jobGeo"),
                    "url": item.get("url"),
                    "type": ", ".join(item.get("jobType", [])) if isinstance(item.get("jobType"), list) else item.get("jobType"),
                    "published_at": item.get("pubDate"),
                    "source": "Jobicy",
                }
            )
        return jobs

    async def _search_adzuna(self, role: str, location: str) -> list[dict[str, Any]]:
        if not settings.adzuna_app_id or not settings.adzuna_app_key:
            return []

        params = {
            "app_id": settings.adzuna_app_id,
            "app_key": settings.adzuna_app_key,
            "results_per_page": 50,
            "what": role,
            "where": location or "Singapore",
            "content-type": "application/json",
        }

        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(self.ADZUNA_BASE_URL, params=params)
            response.raise_for_status()
            data = response.json()

        jobs: list[dict[str, Any]] = []
        for item in data.get("results", []):
            jobs.append(
                {
                    "title": item.get("title"),
                    "company": (item.get("company") or {}).get("display_name"),
                    "location": (item.get("location") or {}).get("display_name") or location,
                    "url": item.get("redirect_url"),
                    "type": item.get("contract_time") or item.get("contract_type"),
                    "published_at": item.get("created"),
                    "source": "Adzuna",
                }
            )
        return jobs

    async def search_jobs(self, role: str, location: str = "remote", limit: int = 5) -> list[dict[str, Any]]:
        role_query = self._normalize(role)
        location_query = self._normalize(location)

        if location_query in {"remote", "any", "all", "worldwide", "global"}:
            jobs = await self._search_remotive()
            filtered = [job for job in jobs if self._matches(job, role_query, location_query)]
            return self._dedupe_jobs(filtered)[:limit]

        if location_query in {"sg", "singapore"}:
            jobs: list[dict[str, Any]] = []
            jobs.extend(await self._search_adzuna(role_query, "Singapore"))

            # Jobicy geo filters are helpful as a fallback when Adzuna has fewer matches.
            try:
                jobs.extend(await self._search_jobicy(role_query, "Singapore"))
            except Exception:
                pass

            # If Adzuna returns too few results, supplement from Remotive remote jobs too.
            if len(jobs) < limit:
                try:
                    jobs.extend(await self._search_remotive())
                except Exception:
                    pass

            filtered = [job for job in jobs if self._matches(job, role_query, location_query)]
            return self._dedupe_jobs(filtered)[:limit]

        # Generic fallback: search all sources, but keep the query strict.
        jobs: list[dict[str, Any]] = []
        try:
            jobs.extend(await self._search_adzuna(role_query, location_query or "Singapore"))
        except Exception:
            pass
        try:
            jobs.extend(await self._search_jobicy(role_query, location_query or "Singapore"))
        except Exception:
            pass
        try:
            jobs.extend(await self._search_remotive())
        except Exception:
            pass

        filtered = [job for job in jobs if self._matches(job, role_query, location_query)]
        return self._dedupe_jobs(filtered)[:limit]


job_service = JobService()
