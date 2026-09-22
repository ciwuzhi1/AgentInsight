# AgentInsight V3.0 API 参考文档

> **以代码为准**：本文档与后端实现（`backend/app/api/**`）不一致时，以代码为准；文档仅作参考。鉴权范围以各路由的 `Depends(get_current_user)` 为准，请求/响应字段以对应 Pydantic 模型与 handler 返回值为准。
>
> **版本**：V3.0  
> **基础路径**：`http://localhost:8100`  
> **Swagger UI**：`/docs`  
> **内容类型**：`application/json`（文件上传为 `multipart/form-data`）

---

## 目录

- [认证机制](#认证机制)
- [错误码与格式](#错误码与格式)
- [Auth 认证接口](#auth-认证接口)
- [Datasets 数据集接口](#datasets-数据集接口)
- [Tasks 任务接口](#tasks-任务接口)
- [Resumes 简历接口](#resumes-简历接口)
- [Matches 匹配接口](#matches-匹配接口)
- [Settings 设置接口](#settings-设置接口)
- [Models 模型配置接口](#models-模型配置接口)
- [Crawler 爬虫接口](#crawler-爬虫接口)
- [Evaluation 评测接口](#evaluation-评测接口)
- [Health 健康检查接口](#health-健康检查接口)
- [Observability 可观测性接口](#observability-可观测性接口)

---

## 认证机制

### Bearer Token

所有需要认证的接口均使用 `Authorization: Bearer <token>` 请求头：

```
Authorization: Bearer eyJhbGciOiJIUzI1NiIs...
```

### SSE 免 header 兜底

SSE 接口（EventSource）无法携带自定义请求头，支持 `?token=` 查询参数：

```
GET /api/tasks/{task_id}/events?token=<jwt>
```

### 接口访问矩阵

| 接口 | 认证要求 | 数据隔离 |
|------|---------|---------|
| `POST /api/auth/register` | 无 | - |
| `POST /api/auth/login` | 无 | - |
| `GET /api/auth/me` | Bearer | - |
| `POST /api/datasets` | Bearer | user_id 落库 |
| `POST /api/tasks` | Bearer | 属主隔离 |
| `GET /api/tasks` | Bearer | 只返回本人+公共 |
| `GET /api/tasks/{id}` | Bearer | 属主隔离 |
| `GET /api/tasks/{id}/events` | Bearer/`?token=` | 属主隔离 |
| `GET /api/tasks/{id}/trace` | Bearer | 属主隔离 |
| `GET /api/tasks/{id}/export` | Bearer | 属主隔离 |
| `POST /api/resumes` | Bearer | user_id 落库 |
| `GET /api/resumes/{id}` | Bearer | 属主隔离 |
| `POST /api/matches` | Bearer | 属主隔离 |
| `GET /api/settings` | Bearer | - |
| `PUT /api/settings` | Bearer | - |
| `GET /api/models` | Bearer | - |
| `POST /api/models` | Bearer | - |
| `PUT /api/models/{id}/activate` | Bearer | - |
| `DELETE /api/models/{id}` | Bearer | - |
| `POST /api/models/{id}/test` | Bearer | - |
| `GET /api/evaluations/last` | Bearer | - |
| `POST /api/crawler/run` | 无 | - |
| `GET /api/crawler/jobs` | 无 | - |
| `POST /api/crawler/export` | 无 | - |
| `GET /api/health*` | 无 | - |
| `GET /healthz` `/readyz` `/metrics` | 无 | - |

> **数据隔离规则**：A 用户上传的资源（数据集/简历/任务），B 用户访问一律返回 404（不泄露存在性）。`user_id=NULL` 的存量数据为公共遗留，登录用户均可见。

---

## 错误码与格式

### 统一错误响应

```json
{
  "detail": "错误描述信息"
}
```

### 特殊错误响应

**409 幂等冲突**（任务重复提交）：

```json
{
  "detail": "相同任务正在执行中",
  "task_id": "abc123..."
}
```

**429 限流**：

```json
{
  "detail": "尝试过于频繁，请 30s 后重试"
}
```

**500 全局兜底**（含 request_id 便于排障）：

```json
{
  "detail": "服务器内部错误",
  "request_id": "a1b2c3d4e5f6"
}
```

### HTTP 状态码总览

| 状态码 | 含义 | 常见触发场景 |
|--------|------|-------------|
| 200 | 成功 | 读取/更新 |
| 201 | 已创建 | 注册/上传 |
| 400 | 请求参数错误 | 文件格式/字段缺失/校验失败 |
| 401 | 未认证 | token 缺失/无效/过期 |
| 404 | 不存在 | 资源不存在或无权限访问 |
| 409 | 冲突 | 用户名已存在/相同任务正在执行中 |
| 413 | 载荷过大 | 文件超过 10MB |
| 422 | 请求体校验失败 | 字段类型/长度不合法 |
| 429 | 请求过频 | 登录/注册限流 5次/分钟；任务创建 30次/分钟 |
| 500 | 服务器错误 | 未处理异常 |
| 502 | 爬虫抓取失败 | 目标站不可达 |
| 503 | 依赖不可用 | MySQL/Redis 不可用 |

---

## Auth 认证接口

### 注册

```
POST /api/auth/register
```

**请求体**：

```json
{
  "username": "alice",
  "password": "secret123"
}
```

| 字段 | 类型 | 约束 |
|------|------|------|
| username | string | 3~32 字符，全局唯一 |
| password | string | ≥6 字符 |

**响应 201**：

```json
{
  "user_id": "550e8400e29b41d4a716446655440000",
  "username": "alice"
}
```

**错误**：`400` 用户名长度不合法 / `409` 用户名已存在 / `429` 限流

---

### 登录

```
POST /api/auth/login
```

**请求体**：

```json
{
  "username": "alice",
  "password": "secret123"
}
```

**响应 200**：

```json
{
  "token": "eyJhbGciOiJIUzI1NiIs...",
  "user_id": "550e8400e29b41d4a716446655440000",
  "username": "alice"
}
```

> Token 有效期 7 天（HS256 签名）。

**错误**：`401` 用户名或密码错误（不泄露具体是哪个错误）/ `429` 限流

---

### 获取当前用户

```
GET /api/auth/me
Authorization: Bearer <token>
```

**响应 200**：

```json
{
  "user_id": "550e8400e29b41d4a716446655440000",
  "username": "alice"
}
```

**错误**：`401` token 缺失/无效/过期

---

## Datasets 数据集接口

### 上传数据集

```
POST /api/datasets
Authorization: Bearer <token>
Content-Type: multipart/form-data
```

**表单字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| file | file | CSV 文件，≤10MB |

**响应 200**：

```json
{
  "dataset_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "name": "demo_sales",
  "table_name": "ds_a1b2c3d4",
  "schema": [
    {"name": "id", "type": "INTEGER"},
    {"name": "region", "type": "VARCHAR"},
    {"name": "amount", "type": "DOUBLE"}
  ],
  "rows_estimate": 10000,
  "size_mb": 0.45,
  "engine_hint": "duckdb"
}
```

**处理流程**：保存文件 → CSV 画像（行数/列数/类型推断）→ DuckDB 视图注册 → MySQL 登记

**错误**：`400` 非 CSV/文件为空/解析失败 / `401` 未登录 / `413` 超过 10MB

---

## Tasks 任务接口

### 创建数据分析任务

```
POST /api/tasks
Authorization: Bearer <token>
```

**请求体**：

```json
{
  "dataset_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "query": "按地区统计总销售额"
}
```

| 字段 | 类型 | 约束 |
|------|------|------|
| dataset_id | string | 已上传数据集的 ID |
| query | string | 1~2000 字符 |

**响应 200**：

```json
{
  "task_id": "t-abc123def456",
  "status": "created"
}
```

**响应 409**（幂等锁，相同任务正在执行中）：

```json
{
  "detail": "相同任务正在执行中",
  "task_id": "t-existing789"
}
```

**错误**：`400` 参数校验失败 / `401` 未登录 / `404` 数据集不存在或无权限 / `429` 限流（30次/分钟）

---

### 查询任务列表

```
GET /api/tasks?limit=20&page=1
Authorization: Bearer <token>
```

**查询参数**：

| 参数 | 默认 | 范围 |
|------|------|------|
| limit | 20 | 1~100 |
| page | 1 | ≥1 |

**响应 200**：

```json
{
  "page": 1,
  "limit": 20,
  "count": 3,
  "items": [
    {
      "id": "t-abc123def456",
      "status": "completed",
      "query": "按地区统计总销售额",
      "dataset_id": "a1b2c3d4...",
      "created_at": "2026-03-11T10:30:00"
    }
  ],
  "has_more": false
}
```

---

### 查询任务详情

```
GET /api/tasks/{task_id}
Authorization: Bearer <token>
```

**响应 200**：

```json
{
  "task_id": "t-abc123def456",
  "status": "completed",
  "engine": "duckdb",
  "final_result": {
    "columns": ["region", "total_amount"],
    "rows": [["华东", 1250000], ["华南", 980000]],
    "chart_type": "bar",
    "sql": "SELECT region, SUM(amount) FROM ds_a1b2c3d4 GROUP BY region",
    "engine": "duckdb"
  },
  "steps": [
    {"agent_name": "data_agent", "status": "ok", "latency_ms": 82},
    {"agent_name": "validator_agent", "status": "ok", "latency_ms": 15}
  ]
}
```

**错误**：`401` / `404` 任务不存在或无权限

---

### SSE 事件流

```
GET /api/tasks/{task_id}/events
Authorization: Bearer <token>
# 或 EventSource: /api/tasks/{task_id}/events?token=<jwt>
```

**响应**：`Content-Type: text/event-stream`

**事件类型**：

```
data: {"type":"state","status":"running"}

data: {"type":"plan","steps":[...]}

data: {"type":"agent_start","agent":"data_agent","step":"s1"}

data: {"type":"agent_end","agent":"data_agent","status":"ok","latency_ms":82,"step":"s1"}

data: {"type":"final","result":{...}}

# 或失败终止：
data: {"type":"error","code":"ENGINE_ERROR","message":"...","terminal":true}

# 自然结束：
event: done
data: {}
```

> - 空闲 15s 发送 `: ping` 心跳注释行防断流
> - 只有 `final` 或 `terminal:true` 的 `error` 事件才会终止流
> - 重试中的 error 事件不终止流

---

### 任务 Trace 回放

```
GET /api/tasks/{task_id}/trace
Authorization: Bearer <token>
```

**响应 200**：

```json
{
  "task_id": "t-abc123def456",
  "status": "completed",
  "engine": "duckdb",
  "query": "按地区统计总销售额",
  "created_at": "2026-03-11T10:30:00",
  "completed_at": "2026-03-11T10:30:01",
  "steps": [
    {
      "step": 1,
      "agent_name": "data_agent",
      "status": "ok",
      "latency_ms": 82,
      "retry_count": 0,
      "detail": {"step_id": "s1"}
    }
  ],
  "final_result": {"columns": [...], "rows": [...]}
}
```

---

### 导出任务结果

```
GET /api/tasks/{task_id}/export?format=csv
Authorization: Bearer <token>
```

**查询参数**：`format` = `csv`（默认）| `json`

**响应**：文件下载（`Content-Disposition: attachment`）

- `csv`：`columns + rows` 拼装为 UTF-8-BOM CSV
- `json`：完整 `final_result` 对象

**错误**：`404` 任务不存在/无结果/匹配任务无表格数据

---

## Resumes 简历接口

### 上传简历

```
POST /api/resumes
Authorization: Bearer <token>
Content-Type: multipart/form-data
```

**表单字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| file | file | PDF / DOCX / TXT，≤10MB |

**响应 200**：

```json
{
  "resume_id": "a1b2c3d4",
  "filename": "my_resume.pdf"
}
```

**错误**：`400` 格式不支持/文件为空 / `401` / `413` 超 10MB

---

### 查询简历

```
GET /api/resumes/{resume_id}
Authorization: Bearer <token>
```

**响应 200**：

```json
{
  "resume_id": "a1b2c3d4",
  "filename": "my_resume.pdf",
  "profile": {
    "skills": ["Python", "FastAPI", "DuckDB"],
    "projects": ["数据中台", "推荐系统"],
    "education": "本科",
    "experience_years": 5
  },
  "created_at": "2026-03-11 10:30:00"
}
```

**错误**：`401` / `404` 不存在或无权限

---

## Matches 匹配接口

### 创建匹配任务

```
POST /api/matches
Authorization: Bearer <token>
```

**请求体**：

```json
{
  "resume_id": "a1b2c3d4",
  "job_ids": [1, 2, 3]
}
```

| 字段 | 类型 | 约束 |
|------|------|------|
| resume_id | string | 已上传简历 ID |
| job_ids | int[] | 非空；去重保序 |

**响应 200**：

```json
{
  "task_id": "t-match123abc",
  "status": "created"
}
```

**执行链路**：`resume ∥ job → match → validator → report`（resume/job 并行）

> 匹配任务的 `final_result` 含顶层 `experience_years`（与 `resume.experience_years` 同源，取自简历 profile），字段定义见 CONTRACTS2 §2.1。

**错误**：`400` job_ids 为空/岗位不存在 / `401` / `404` 简历不存在或无权限 / `409` 幂等锁 / `429` 限流

---

## Settings 设置接口

### 读取全部设置

```
GET /api/settings
Authorization: Bearer <token>
```

**响应 200**：

```json
{
  "match_llm_enabled": "false",
  "llm_fallback_mock": "auto",
  "parser_backend": "mineru_api",
  "sql_timeout": "30",
  "mineru_api_token": "",
  "tavily_api_key": ""
}
```

> 密钥类字段（`mineru_api_token` / `tavily_api_key`）显示为脱敏或空串。

---

### 更新单个设置

```
PUT /api/settings
Authorization: Bearer <token>
```

**请求体**：

```json
{
  "key": "sql_timeout",
  "value": "60"
}
```

**可写键白名单**：

| 键 | 类型 | 说明 |
|----|------|------|
| `match_llm_enabled` | `true`/`false` | 匹配结果是否启用 LLM 解读 |
| `llm_fallback_mock` | `auto`/`never` | LLM 失败是否降级 mock |
| `parser_backend` | `mineru_api`/`pymupdf` | PDF 解析后端 |
| `sql_timeout` | 数字（秒） | SQL 执行超时 |
| `mineru_api_token` | string | MinerU API token（加密落库） |
| `tavily_api_key` | string | Tavily API key（加密落库） |

**响应 200**：

```json
{
  "key": "sql_timeout",
  "updated": true
}
```

**错误**：`400` 非白名单键 / `401`

---

## Models 模型配置接口

### 查询模型列表

```
GET /api/models
Authorization: Bearer <token>
```

**响应 200**：

```json
[
  {
    "id": "m1abc...",
    "name": "DeepSeek Chat",
    "provider": "openai",
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-chat",
    "temperature": 0.0,
    "is_active": true,
    "api_key_masked": "***ab12"
  }
]
```

---

### 新增模型配置

```
POST /api/models
Authorization: Bearer <token>
```

**请求体**：

```json
{
  "name": "GLM-4 Flash",
  "provider": "openai",
  "base_url": "https://open.bigmodel.cn/api/paas/v4",
  "api_key": "your-api-key-here",
  "model": "glm-4-flash",
  "temperature": 0.0
}
```

**响应 200**：

```json
{
  "id": "m2def...",
  "name": "GLM-4 Flash",
  "status": "created"
}
```

> `api_key` 使用 Fernet 加密落库，回显时脱敏为 `***尾4位`。

---

### 激活模型

```
PUT /api/models/{model_id}/activate
Authorization: Bearer <token>
```

**响应 200**：

```json
{
  "id": "m2def...",
  "is_active": true
}
```

> 激活是事务操作：先清所有 active，再设目标为 active，保证全局唯一。激活即时生效（10s TTL 缓存兜底），无需重启。

---

### 删除模型

```
DELETE /api/models/{model_id}
Authorization: Bearer <token>
```

**响应 200**：

```json
{
  "id": "m2def...",
  "deleted": true
}
```

**错误**：`401` / `404` 模型不存在

---

### 连通性测试

```
POST /api/models/{model_id}/test
Authorization: Bearer <token>
```

**响应 200**（成功）：

```json
{
  "ok": true,
  "latency_ms": 350
}
```

**响应 200**（失败，不返回 HTTP 错误）：

```json
{
  "ok": false,
  "error": "connection timeout"
}
```

> 测试会向模型端点发送一次简单 JSON 请求，验证连通性。

---

## Crawler 爬虫接口

### 运行爬虫

```
POST /api/crawler/run
```

**请求体**（全部可选）：

```json
{
  "url": null,
  "pages": 1,
  "max_items": 10
}
```

| 字段 | 默认 | 范围 | 说明 |
|------|------|------|------|
| url | null（演示站） | - | 目标列表页 URL |
| pages | 1 | 1~20 | 抓取页数 |
| max_items | **10** | 1~50 | 最大入库条目数 |

**响应 200**：

```json
{
  "inserted": 8,
  "skipped": 1,
  "items": [
    {
      "title": "Python 开发工程师",
      "company": "示例科技",
      "location": "北京",
      "skills": ["Python", "Django", "PostgreSQL"]
    }
  ],
  "failed_urls": [
    {"url": "https://.../jobs/3", "error": "timeout"}
  ]
}
```

| 字段 | 说明 |
|------|------|
| inserted | 新入库条数 |
| skipped | 已存在（upsert 去重）跳过条数 |
| items | 本次成功解析并入库的岗位摘要（title/company/location/skills） |
| failed_urls | 失败的 listing/detail/upsert 条目 `[{url, error}]`；部分失败仍返回 200 |

> 每次请求间隔 0.5s（礼貌性限速）。逐条 upsert（成功一条写一条）；单条失败记入 `failed_urls` 并继续，不中断整次。

**错误**：`502` listing 首页整页失败 / `503` MySQL 不可用

---

### 查询岗位列表

```
GET /api/crawler/jobs?limit=20
```

**查询参数**：`limit` 默认 20，范围 1~500

**响应 200**：

```json
{
  "count": 10,
  "items": [
    {
      "id": 1,
      "title": "Python 开发工程师",
      "company": "示例科技",
      "location": "北京",
      "skills": "Python,Django",
      "description": "..."
    }
  ]
}
```

---

### 导出岗位 CSV

```
POST /api/crawler/export
```

**响应 200**：

```json
{
  "path": "data/large/jd_crawled.csv",
  "rows": 150
}
```

**CSV 列**：`title, company, location, skills, description`

---

## Evaluation 评测接口

### 查询最近评测报告

```
GET /api/evaluations/last
Authorization: Bearer <token>
```

**响应 200**：完整评测报告 JSON（NL2SQL 成功率、匹配准确率、路由准确率等指标）

**错误**：`401` / `404` 暂无评测报告（请先运行 `python -m app.evaluation.runner`）

---

## Health 健康检查接口

### 基础健康检查

```
GET /api/health
```

```json
{"status": "ok"}
```

---

### 存活探针

```
GET /api/health/live
# 或根路径别名
GET /healthz
```

```json
{"status": "alive"}
```

> K8s liveness 探针风格：进程活着即 200，不探测依赖。

---

### 就绪探针

```
GET /api/health/ready
# 或根路径别名
GET /readyz
```

**响应 200**（就绪）：

```json
{
  "ready": true,
  "mysql": "up",
  "redis": "up"
}
```

**响应 503**（未就绪）：

```json
{
  "ready": false,
  "mysql": "down",
  "redis": "degraded"
}
```

> MySQL 必须可用；Redis 允许降级（`degraded` 不阻塞就绪）。

---

### MySQL 探活

```
GET /api/health/mysql
```

```json
{"mysql": "up", "latency_ms": 2}
```

**错误**：`503` MySQL 不可用

---

### Redis 探活

```
GET /api/health/redis
```

```json
{"redis": "up"}
```

或（降级）：

```json
{"redis": "degraded"}
```

> Redis 挂了返回 `degraded` 而非 500（降级原则）。

---

## Observability 可观测性接口

### 运行指标

```
GET /metrics
```

```json
{
  "requests_total": 1520,
  "errors_total": 3,
  "avg_latency_ms": 45.2,
  "active_tasks": 2
}
```

> 单实例进程内指标；多实例部署可换 Prometheus client（接口已预留）。

---

## 限流策略汇总

| 场景 | 限流规则 | 键维度 |
|------|---------|--------|
| 登录 | 5 次/分钟 | IP + username |
| 注册 | 5 次/分钟 | IP + username |
| 任务创建（数据/匹配） | 30 次/分钟 | user_id |

**超限响应**：

```json
{
  "detail": "尝试过于频繁，请 28s 后重试"
}
```

HTTP 状态码：`429`
