"""LLM 客户端：OpenAI 兼容实现 + mock 规则版 NL2SQL（契约 §10）。"""
from __future__ import annotations

import asyncio
import json
import re
import time
from abc import ABC, abstractmethod

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class LLMError(Exception):
    """LLM 调用或 JSON 解析失败。"""


# NL2SQL system prompt：只输出 JSON、单条只读 SELECT、用给定 schema、必须带 LIMIT
NL2SQL_SYSTEM_PROMPT = (
    "你是数据分析助手，把用户的中文问题转成一条 DuckDB SQL。要求：\n"
    "1. 只输出一个 JSON 对象，格式为 {\"sql\": \"...\", \"explanation\": \"一句话中文说明\"}，"
    "不要输出任何其他文字或代码块标记。\n"
    "2. 只允许一条 SELECT 语句，只读，禁止 INSERT/UPDATE/DELETE/DDL 等任何写操作。\n"
    "3. 只能使用给定的表名和 schema 中的列名，不要编造列。\n"
    "4. 中文列值（如地区名、商品名）不需要翻译，直接匹配原值。\n"
    "5. 聚合结果必须加 LIMIT（如 LIMIT 1000），排序默认按指标降序。\n"
    "6. 日期列可直接用年份/月份函数（如 year(order_date)、month(order_date)）。"
)


class BaseLLMClient(ABC):
    """LLM 客户端统一接口。"""

    # 是否 mock 实现（供 match_agent 等判断，CONTRACTS2 §4.5）
    is_mock: bool = False

    @abstractmethod
    async def generate_json(self, system: str, user: str) -> dict:
        """调用 LLM 并解析为 dict；失败抛 LLMError。"""


class OpenAICompatibleClient(BaseLLMClient):
    """OpenAI 兼容 chat completions（deepseek/glm 等），强制 JSON 输出。"""

    is_mock = False

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> None:
        from openai import AsyncOpenAI

        # 缺省回落 .env 全局配置（设置中心来源由 get_llm_client 显式传参）
        self._base_url = base_url if base_url is not None else settings.LLM_BASE_URL
        self._api_key = api_key if api_key is not None else settings.LLM_API_KEY
        self._model = model if model is not None else settings.LLM_MODEL
        self._temperature = 0 if temperature is None else temperature
        self._client = AsyncOpenAI(
            base_url=self._base_url or None,
            api_key=self._api_key,
            timeout=settings.LLM_TIMEOUT,  # 单次请求超时（默认 60s，.env 可覆盖）
        )

    async def generate_json(self, system: str, user: str) -> dict:
        """请求 JSON 输出，解析失败按指数退避重试，仍失败抛 LLMError。"""
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                resp = await self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    response_format={"type": "json_object"},
                    temperature=self._temperature,
                )
                content = resp.choices[0].message.content or ""
                data = json.loads(content)
                if isinstance(data, dict):
                    return data
                last_err = LLMError(f"LLM 返回非 JSON 对象: {type(data).__name__}")
            except Exception as exc:  # 网络/解析失败统一重试一次
                last_err = exc
                logger.warning("LLM 调用失败(第 %d 次): %s", attempt + 1, exc)
            if attempt + 1 < 2:
                # 重试间隔指数退避：0.2s 起、逐次翻倍、上限 0.8s
                await asyncio.sleep(min(0.2 * 2**attempt, 0.8))
        raise LLMError(f"LLM 调用失败: {last_err}") from last_err


# 中文口语列名 → demo_sales.csv 列名
_COL_MAP = {
    "地区": "region", "区域": "region",
    "商品": "product", "产品": "product",
    "品类": "category", "类别": "category", "分类": "category",
    "日期": "order_date", "月份": "order_date", "时间": "order_date",
}
# 口语指标 → 列名
_METRIC_MAP = {
    "销售额": "sales", "销售": "sales", "sales": "sales",
    "销量": "quantity", "数量": "quantity", "quantity": "quantity",
}


class MockLLMClient(BaseLLMClient):
    """规则版 NL2SQL（无 key 时降级使用），针对 demo_sales.csv。"""

    is_mock = True

    async def generate_json(self, system: str, user: str) -> dict:
        await asyncio.sleep(0)  # 保持 async 语义
        return self._rule_sql(user)

    def _rule_sql(self, user: str) -> dict:
        table = "ds_demo"
        m = re.search(r"表名[:：]\s*(\S+)", user)
        if m:
            table = m.group(1)
        # 用户问题行；没有该行则用全文匹配
        qm = re.search(r"用户问题[:：]\s*(.+)", user)
        query = qm.group(1).strip() if qm else user

        limit = settings.SQL_MAX_ROWS
        sql, explanation = None, "mock 规则生成"

        # "按{列}统计..."
        group_m = re.search(r"按([\u4e00-\u9fa5A-Za-z_]+?)统计", query)
        group_col = None
        if group_m:
            word = group_m.group(1)
            group_col = _COL_MAP.get(word, word)

        # 指标列
        metric = None
        for key, col in _METRIC_MAP.items():
            if key in query.lower() or key in query:
                metric = col
                break

        top_m = re.search(r"[Tt]op\s*(\d+)", query)
        is_avg = "平均" in query
        is_sum = ("总计" in query) or ("总" in query) or ("和" in query)

        if group_col and metric:
            if is_avg:
                agg, alias, cn = f"AVG({metric})", f"avg_{metric}", "平均"
            else:
                agg, alias, cn = f"SUM({metric})", f"total_{metric}", "总计"
            sql = (
                f"SELECT {group_col}, {agg} AS {alias} FROM {table} "
                f"GROUP BY {group_col} ORDER BY {alias} DESC LIMIT {limit}"
            )
            explanation = f"mock 规则生成：按 {group_col} 统计{cn}{metric}"
        elif metric and (is_avg or is_sum):
            # 无分组的整体聚合
            if is_avg:
                agg, alias, cn = f"AVG({metric})", f"avg_{metric}", "平均"
            else:
                agg, alias, cn = f"SUM({metric})", f"total_{metric}", "总计"
            sql = f"SELECT {agg} AS {alias} FROM {table} LIMIT 1"
            explanation = f"mock 规则生成：{cn}{metric}"
        elif top_m:
            # Top n：默认按商品销量/销售额排序
            n = min(int(top_m.group(1)), limit)
            metric = metric or "sales"
            sql = (
                f"SELECT product, SUM({metric}) AS total_{metric} FROM {table} "
                f"GROUP BY product ORDER BY total_{metric} DESC LIMIT {n}"
            )
            explanation = f"mock 规则生成：{metric} Top {n} 的商品"
        elif "最近" in query:
            sql = f"SELECT * FROM {table} ORDER BY order_date DESC LIMIT 10"
            explanation = "mock 规则生成：最近 10 条记录"
        else:
            sql = f"SELECT * FROM {table} LIMIT 100"

        return {"sql": sql, "explanation": explanation}


# 激活模型配置的 TTL 缓存：10s 内复用，DB 异常也按 None 缓存避免打爆
_ACTIVE_TTL_S = 10.0
_active_cfg_cache: tuple[float, dict | None] | None = None

# LLM Client 实例缓存：按 (base_url, model) 缓存，复用 HTTP 连接池
_CLIENT_TTL_S = 60.0  # 与配置缓存联动，60s 后重建
_client_cache: dict[str, tuple[float, BaseLLMClient]] = {}


def _get_active_model_config() -> dict | None:
    """查 model_configs 激活行（10s TTL 缓存）；DB 失败回退 None 并告警。"""
    global _active_cfg_cache
    now = time.monotonic()
    if _active_cfg_cache is not None and now - _active_cfg_cache[0] < _ACTIVE_TTL_S:
        return _active_cfg_cache[1]
    try:
        from app.persistence.mysql import get_active_model_config

        cfg = get_active_model_config()
    except Exception as exc:
        logger.warning("激活模型配置查询失败，回退 .env 来源: %s", exc)
        cfg = None
    _active_cfg_cache = (now, cfg)
    return cfg


def _get_cached_client(key: str) -> BaseLLMClient | None:
    """从缓存取 client；过期或不存在返回 None。"""
    entry = _client_cache.get(key)
    if entry is None:
        return None
    ts, client = entry
    if time.monotonic() - ts > _CLIENT_TTL_S:
        _client_cache.pop(key, None)
        return None
    return client


def _set_cached_client(key: str, client: BaseLLMClient) -> BaseLLMClient:
    """缓存 client 实例。"""
    _client_cache[key] = (time.monotonic(), client)
    return client


def get_llm_client() -> BaseLLMClient:
    """工厂：优先设置中心激活的模型配置（api_key 解密），其次 .env 全局配置。

    llm_fallback_mock=never 且无任何可用配置时抛 LLMError("未配置模型")；
    auto（默认）降级 MockLLMClient。

    优化：按 (base_url, model) 缓存 OpenAICompatibleClient 实例，
    复用 HTTP 连接池，避免每次调用新建 AsyncOpenAI。
    """
    row = _get_active_model_config()
    if row:
        from app.core.crypto import decrypt_secret

        try:
            api_key = decrypt_secret(row.get("api_key_enc") or "")
        except ValueError as exc:
            logger.warning("激活模型 api_key 解密失败，忽略该配置: %s", exc)
            api_key = ""
        if api_key:
            cache_key = f"db:{row.get('base_url')}:{row.get('model')}"
            cached = _get_cached_client(cache_key)
            if cached is not None:
                return cached
            client = OpenAICompatibleClient(
                base_url=row.get("base_url"),
                api_key=api_key,
                model=row.get("model"),
                temperature=row.get("temperature"),
            )
            return _set_cached_client(cache_key, client)
        logger.warning("激活模型配置 api_key 为空，回退 .env 来源")

    key = (settings.LLM_API_KEY or "").strip()
    if settings.LLM_PROVIDER == "mock" or not key or "填" in key:
        fallback = "auto"
        try:
            from app.core.app_settings import get_setting

            fallback = (get_setting("llm_fallback_mock", "auto") or "auto").lower()
        except Exception as cfg_exc:
            logger.warning("读取 llm_fallback_mock 失败，按 auto 降级: %s", cfg_exc)
            fallback = "auto"
        if fallback == "never":
            raise LLMError("未配置模型")
        logger.warning(
            "LLM 未配置有效 key（provider=%s），降级为 MockLLMClient", settings.LLM_PROVIDER
        )
        return MockLLMClient()
    # .env 来源也缓存
    cache_key = f"env:{settings.LLM_BASE_URL}:{settings.LLM_MODEL}"
    cached = _get_cached_client(cache_key)
    if cached is not None:
        return cached
    client = OpenAICompatibleClient()
    return _set_cached_client(cache_key, client)
