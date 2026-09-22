"""爬虫 run 韧性单测：max_items 默认、逐条入库、失败跳过、failed_urls。

异步 run() 用 asyncio.run 驱动（与仓库其它单测一致），不依赖网络/MySQL。
"""
from __future__ import annotations

import asyncio

import pytest

from app.api import crawler as crawler_api
from app.api.crawler import CrawlerRunRequest, run
from app.crawler.fetcher import FetchError


def _listing_html(*titles: str) -> str:
    cards = []
    for i, t in enumerate(titles):
        cards.append(
            f"""
            <div class="card-content">
              <h2>{t}</h2>
              <h3>Co{i}</h3>
              <p class="location">Loc{i}</p>
              <footer class="card-footer">
                <a href="https://example.com/ext/{i}">Learn</a>
                <a href="https://example.com/jobs/{i}/">View</a>
              </footer>
            </div>
            """
        )
    return "<html><body>" + "".join(cards) + "</body></html>"


DETAIL_HTML = "<div class='content'><p>Python SQL role</p></div>"


def _fake_fetch_factory(fail_detail: dict[str, str] | None = None):
    """构造 fetch mock：默认站点 listing 与 example.com/jobs/ 详情可区分。

    fail_detail: {detail_substring: error message}
    """
    fail_detail = fail_detail or {}

    def fake_fetch(url: str) -> str:
        # 详情页 href 为绝对 URL，不会落到 BASE_URL
        if "example.com/jobs/" in url:
            for key, msg in fail_detail.items():
                if key in url:
                    raise FetchError(msg)
            return DETAIL_HTML
        # listing：BASE_URL 或 page/{n}/
        return _listing_html("JobA", "JobB", "JobC")

    return fake_fetch


def test_max_items_default_10_and_bounds():
    """契约：max_items 默认 10，ge=1 le=50。"""
    assert CrawlerRunRequest().max_items == 10
    assert CrawlerRunRequest(max_items=50).max_items == 50
    with pytest.raises(Exception):
        CrawlerRunRequest(max_items=0)
    with pytest.raises(Exception):
        CrawlerRunRequest(max_items=51)


def test_run_per_item_upsert_and_partial_failure(monkeypatch):
    """detail 异常跳过并记 failed_urls；成功条目逐条 upsert，整次仍 200 语义。"""
    calls: list[str] = []

    def fake_fetch(url: str) -> str:
        if "example.com/jobs/1/" in url:
            raise FetchError("抓取失败 timeout")
        if "example.com/jobs/" in url:
            return DETAIL_HTML
        return _listing_html("JobA", "JobB", "JobC")

    def fake_upsert_one(item: dict) -> str:
        calls.append(item["title"])
        return "inserted"

    monkeypatch.setattr(crawler_api, "fetch", fake_fetch)
    monkeypatch.setattr(crawler_api.storage, "upsert_one", fake_upsert_one)
    monkeypatch.setattr(crawler_api, "CRAWL_INTERVAL", 0)

    result = asyncio.run(run(None, pages=1, max_items=20))

    assert result["inserted"] == 2
    assert result["skipped"] == 0
    assert len(result["items"]) == 2
    assert result["items"][0]["title"] == "JobA"
    assert result["items"][1]["title"] == "JobC"
    assert len(result["failed_urls"]) == 1
    assert "jobs/1" in result["failed_urls"][0]["url"]
    assert "error" in result["failed_urls"][0]
    # 成功一条写一条：两次 upsert，且顺序与 listing 一致
    assert calls == ["JobA", "JobC"]


def test_run_upsert_skipped_counts(monkeypatch):
    """已有岗位 upsert 返回 skipped 时计入 skipped。"""

    def fake_fetch(url: str) -> str:
        if "example.com/jobs/" in url:
            return DETAIL_HTML
        return _listing_html("JobA", "JobB")

    def fake_upsert_one(item: dict) -> str:
        return "skipped" if item["title"] == "JobA" else "inserted"

    monkeypatch.setattr(crawler_api, "fetch", fake_fetch)
    monkeypatch.setattr(crawler_api.storage, "upsert_one", fake_upsert_one)
    monkeypatch.setattr(crawler_api, "CRAWL_INTERVAL", 0)

    result = asyncio.run(run(None, pages=1, max_items=20))
    assert result["inserted"] == 1
    assert result["skipped"] == 1
    assert result["failed_urls"] == []


def test_run_max_items_respected(monkeypatch):
    """max_items 截断抓取与入库数量。"""

    def fake_fetch(url: str) -> str:
        if "example.com/jobs/" in url:
            return DETAIL_HTML
        return _listing_html("J1", "J2", "J3", "J4")

    upserted: list[str] = []

    def fake_upsert_one(item: dict) -> str:
        upserted.append(item["title"])
        return "inserted"

    monkeypatch.setattr(crawler_api, "fetch", fake_fetch)
    monkeypatch.setattr(crawler_api.storage, "upsert_one", fake_upsert_one)
    monkeypatch.setattr(crawler_api, "CRAWL_INTERVAL", 0)

    result = asyncio.run(run(None, pages=1, max_items=2))
    assert result["inserted"] == 2
    assert upserted == ["J1", "J2"]
    assert len(result["items"]) == 2


def test_run_listing_first_page_down_raises(monkeypatch):
    """listing 首页整页失败 → 抛 FetchError（路由层 502）。"""

    def fake_fetch(url: str) -> str:
        raise FetchError("抓取失败 listing down")

    monkeypatch.setattr(crawler_api, "fetch", fake_fetch)
    monkeypatch.setattr(crawler_api, "CRAWL_INTERVAL", 0)

    with pytest.raises(FetchError):
        asyncio.run(run(None, pages=1, max_items=20))


def test_run_listing_page2_down_keeps_partial(monkeypatch):
    """page1 成功、page2 listing 挂了：已入库结果保留，failed_urls 记 page2，不抛。"""

    def fake_fetch(url: str) -> str:
        if "page/2" in url:
            raise FetchError("抓取失败 page2")
        if "example.com/jobs/" in url:
            return DETAIL_HTML
        return _listing_html("JobA", "JobB", "JobC")

    def fake_upsert_one(item: dict) -> str:
        return "inserted"

    monkeypatch.setattr(crawler_api, "fetch", fake_fetch)
    monkeypatch.setattr(crawler_api.storage, "upsert_one", fake_upsert_one)
    monkeypatch.setattr(crawler_api, "CRAWL_INTERVAL", 0)

    result = asyncio.run(run(None, pages=2, max_items=20))
    assert result["inserted"] >= 1
    assert result["items"][0]["title"] == "JobA"
    assert len(result["failed_urls"]) == 1
    assert "page/2" in result["failed_urls"][0]["url"]


def test_run_upsert_exception_recorded(monkeypatch):
    """单条 upsert 异常：跳过并记 failed_urls，其余继续。"""

    def fake_fetch(url: str) -> str:
        if "example.com/jobs/" in url:
            return DETAIL_HTML
        return _listing_html("JobA", "JobB")

    def fake_upsert_one(item: dict) -> str:
        if item["title"] == "JobA":
            raise RuntimeError("db write fail")
        return "inserted"

    monkeypatch.setattr(crawler_api, "fetch", fake_fetch)
    monkeypatch.setattr(crawler_api.storage, "upsert_one", fake_upsert_one)
    monkeypatch.setattr(crawler_api, "CRAWL_INTERVAL", 0)

    result = asyncio.run(run(None, pages=1, max_items=20))
    assert result["inserted"] == 1
    assert result["items"][0]["title"] == "JobB"
    assert len(result["failed_urls"]) == 1
    assert "db write fail" in result["failed_urls"][0]["error"]
