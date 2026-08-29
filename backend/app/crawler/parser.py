"""解析 fake-jobs 静态站：列表页 / 详情页 / 技能抽取（契约 §12）。"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

BASE_URL = "https://realpython.github.io/fake-jobs/"

# 技能关键词表，按顺序匹配去重
SKILL_KEYWORDS = [
    "Python", "SQL", "Java", "JavaScript", "Docker", "Kubernetes", "AWS",
    "Linux", "React", "Spark", "MySQL", "Redis", "FastAPI", "CSS", "HTML",
    "Pandas", "Git",
]

# 词边界匹配，避免 Java 误命中 JavaScript 等
_SKILL_PATTERNS = {
    kw: re.compile(rf"\b{re.escape(kw)}\b", re.IGNORECASE) for kw in SKILL_KEYWORDS
}


def listing_url(page: int = 1) -> str:
    """分页 URL：page=1 → 首页，>1 → page/{n}/。"""
    if page <= 1:
        return BASE_URL
    return urljoin(BASE_URL, f"page/{page}/")


def parse_listing(html: str, base_url: str) -> list[dict]:
    """解析列表页，返回 [{title, company, location, detail_url}]。"""
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []
    for card in soup.select(".card-content"):
        h2 = card.find("h2")
        h3 = card.find("h3")
        loc = card.find("p", class_="location")
        # 详情链接取 card-footer 第二个 a（第一个是 Learn 外链）
        links = card.select("footer.card-footer a") or card.select(".card-footer a")
        detail_url = links[1]["href"] if len(links) >= 2 else (links[0]["href"] if links else None)
        items.append(
            {
                "title": h2.get_text(strip=True) if h2 else "",
                "company": h3.get_text(strip=True) if h3 else None,
                "location": loc.get_text(strip=True) if loc else None,
                "detail_url": urljoin(base_url, detail_url) if detail_url else None,
            }
        )
    return items


def parse_detail(html: str) -> str:
    """解析详情页正文：取描述段落拼接前 800 字。"""
    soup = BeautifulSoup(html, "html.parser")
    box = soup.select_one("#ResultsContainer .box .content") or soup
    parts: list[str] = []
    for p in box.find_all("p"):
        # 跳过 Location / Posted 等元信息段
        if p.get("id") in ("location", "date"):
            continue
        text = p.get_text(" ", strip=True)
        if text:
            parts.append(text)
    return "\n".join(parts)[:800]


def extract_skills(text: str) -> list[str]:
    """在 title+description 里大小写不敏感匹配技能关键词，去重保序。"""
    if not text:
        return []
    return [kw for kw in SKILL_KEYWORDS if _SKILL_PATTERNS[kw].search(text)]
