"""爬虫模块：fetch / parse / storage（契约 §12）。"""
from app.crawler.fetcher import FetchError, fetch
from app.crawler.parser import (
    BASE_URL,
    SKILL_KEYWORDS,
    extract_skills,
    listing_url,
    parse_detail,
    parse_listing,
)
from app.crawler.storage import upsert_jobs

__all__ = [
    "BASE_URL",
    "SKILL_KEYWORDS",
    "FetchError",
    "fetch",
    "listing_url",
    "parse_listing",
    "parse_detail",
    "extract_skills",
    "upsert_jobs",
]
