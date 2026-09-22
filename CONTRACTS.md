# AgentInsight MVP 契约文档（并行开发统一标准）

> **以代码为准**：本文档与后端实现（`backend/app/**`）不一致时，以代码为准；文档仅作约定与导读。接口字段、默认值、鉴权范围等以对应模块源码为最终事实来源。
>
> 本文件是所有并行子代理的唯一契约。先读本文件再动手。与你负责模块冲突时，以本文件为准；发现契约缺陷不要私自改接口，在报告里提出。与实现不一致处按上文「以代码为准」处理。

## 0. 项目基本信息

- 位置：`D:\Development\aiproject\AgentInsight`
- Python：本机 3.12.8（`D:\Development\python312`），依赖装进全局，无 venv
- 运行方式：backend 在 `backend/` 目录下 `python -m uvicorn app.main:app --port 8000`；frontend 在 `frontend/` 下 `npm run dev`（端口 3000）；MySQL 本机 3306；Redis Docker 6379
- 包导入约定：所有 backend 模块以 `app.` 开头（如 `from app.core.config import settings`）
- Python 3.12 + FastAPI + Pydantic v2；注释与 docstring 用中文、简洁；标识符英文；不要写多余的"教学式"注释

## 1. 模块归属（谁写什么，禁止越界）

| 目录/文件 | 归属 |
|---|---|
| `app/agent_runtime/{state,message,registry,supervisor}.py` | CODE-1 |
| `app/agents/base.py` | CODE-1 |
| `app/agents/{data_agent,validator_agent}.py` | CODE-2 |
| `app/tools/{schema_tool,sql_tool}.py`、`app/core/llm.py` | CODE-2 |
| `app/data_engine/duckdb_engine.py`、`app/data_engine/base.py` | CODE-2 |
| `app/data_engine/{profiler,router,spark_engine,result}.py`、`app/tools/spark_tool.py` | CODE-3 |
| `spark/jobs/jd_skill_stats.py` | CODE-3 |
| `app/main.py`、`app/api/{datasets,agent,health}.py`、`app/persistence/*`、`app/core/{config,logging}.py`、`app/cache/redis.py` | CODE-4 |
| `frontend/app/**`、`frontend/README.md` | CODE-5 |
| `backend/tests/**`、`README.md`、`ARCHITECTURE.md` | CODE-6 |
| `app/crawler/**`、`app/api/crawler.py` | CODE-7 |
| `app/context/`、`app/evaluation/` 占位 README | 已由主线程创建 |

## 2. 配置契约（CODE-4 写 `app/core/config.py`，所有人只 import 不自己读 env）

`config.py` 用 pydantic-settings：`Settings(BaseSettings)` + `model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parents[3] / ".env", extra="ignore")`，导出单例 `settings`。

字段（与根目录 `.env.example` 完全一致）：

```text
LLM_PROVIDER=openai            # openai | mock
LLM_BASE_URL=                  # 如 https://api.deepseek.com/v1
LLM_API_KEY=
LLM_MODEL=                     # 如 deepseek-chat / glm-4-flash
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_ROOT_PASSWORD=           # 仅 scripts/init_db.py 用
MYSQL_USER=agent_app
MYSQL_PASSWORD=
MYSQL_DATABASE=agentinsight
REDIS_URL=redis://127.0.0.1:6379/0
MAX_AGENT_STEPS=8
MAX_RETRY=2
SQL_MAX_ROWS=1000
SPARK_ROW_THRESHOLD=100000     # 数据行数 >= 阈值 → Spark
SPARK_IMAGE=apache/spark:3.5.1
DATA_DIR=                      # 默认 <repo>/data
UPLOAD_DIR=                    # 默认 <repo>/data/uploads
```

类型：端口/阈值/行数 int，其余 str。所有默认值给宽松缺省，缺 key 不崩（LLM 无 key 时自动降级 mock）。

## 3. 核心数据类（字段名精确一致）

### 3.1 `app/agent_runtime/state.py` — TaskState（CODE-1）

```python
class TaskStatus(str, Enum):
    CREATED="created"; ROUTING="routing"; RUNNING="running"
    VALIDATING="validating"; COMPLETED="completed"; FAILED="failed"
    RETRYING="retrying"; FAILED_FINAL="failed_final"

class TaskState:
    task_id: str            # uuid4
    dataset_id: str | None
    query: str
    status: TaskStatus
    current_agent: str | None
    current_step: int                       # 从 0 递增
    plan: list[str]                         # agent 名单
    context: dict                           # dataset 元数据等
    results: dict                           # {agent_name: payload}
    errors: list[str]
    retry_count: int
    final_result: dict | None
    created_at: str; updated_at: str        # ISO 时间
    def transition(self, to: TaskStatus) -> None   # 非法迁移抛 ValueError
```

合法迁移表：created→routing→running；running→validating；validating→completed / failed；failed→retrying→running；retrying 次数超过 `settings.MAX_RETRY` → failed_final；completed / failed_final 为终态。

### 3.2 `app/agent_runtime/message.py` — AgentMessage（CODE-1）

```python
@dataclass
class AgentMessage:
    message_id: str      # uuid4
    task_id: str
    sender: str          # agent 名，supervisor 用 "supervisor"
    receiver: str        # "runtime" 或下一 agent 名
    type: str            # 如 "data_result" / "validation_result" / "error"
    payload: dict
    created_at: str
```

### 3.3 `app/agents/base.py` — BaseAgent / AgentResult（CODE-1）

```python
@dataclass
class AgentResult:
    status: str                 # "ok" | "error"
    message_type: str           # 如 "data_result"
    data: dict
    next_action: str | None = None
    errors: list[str] = field(default_factory=list)

class BaseAgent(ABC):
    name: str
    @abstractmethod
    async def run(self, state: TaskState, emit: EmitFn) -> AgentResult
```

`EmitFn = Callable[[dict], Awaitable[None]]`——agent 通过 `await emit(event_dict)` 发 SSE 事件（runtime 注入）。不引入其他参数。

### 3.4 `app/agent_runtime/registry.py` — AgentRegistry（CODE-1）

`register(agent: BaseAgent)` / `get(name) -> BaseAgent`（未注册抛 KeyError）/ `names() -> list[str]`。进程内单例 `registry`。

### 3.5 `app/agent_runtime/supervisor.py` — Supervisor（CODE-1）

```python
class Supervisor:
    async def route(self, state: TaskState, emit) -> list[str]
```

MVP 规则（写成可读的 if 逻辑，预留 complexity 扩展注释）：
- 无 dataset_id → plan=["clarify"]，由 supervisor 直接产出 final_result 提示"请先上传数据集"
- query 命中求职意图关键词（简历/岗位/匹配）→ plan=["data_agent"]，context.mark="resume_future"（本轮仍走 data_agent，注释说明预留）
- 默认 → plan=["data_agent"]

`run_task(...)` 执行器也放 supervisor.py：按 plan 顺序调 agent → validator → 组装 final_result，全部步骤 try/except 包住并 emit 事件。validator 用 `registry.get("validator_agent")`。

### 3.6 `app/data_engine/base.py` 与 `result.py`（CODE-2 写 base，CODE-3 写 result）

```python
# base.py (CODE-2)
class AnalysisEngine(ABC):
    name: str
    @abstractmethod
    def register_dataset(self, dataset_id: str, name: str, path: str) -> dict   # 返回 schema
    @abstractmethod
    def get_schema(self, dataset_id: str) -> list[dict]
    @abstractmethod
    async def execute(self, dataset_id: str, sql: str) -> "EngineResult"

# result.py (CODE-3，纯数据类，CODE-2 也 import 它 —— 不得改动定义)
@dataclass
class EngineResult:
    columns: list[str]
    rows: list[list]          # 已截断
    row_count: int            # 截断后行数
    total_rows: int | None    # 未截断前总数，未知为 None
    truncated: bool
    elapsed_ms: int
    engine: str               # "duckdb" | "spark"
```

### 3.7 `app/data_engine/profiler.py`（CODE-3）

`profile_csv(path) -> DatasetProfile`，字段：`path, size_bytes, size_mb, columns: list[str], rows_estimate: int, format: str="csv"`。行数估算：读文件统计换行（可抽样：读前 64KB 估平均行长 × 文件大小，上限精确计数 5MB 内文件）。

### 3.8 `app/data_engine/router.py`（CODE-3）

`choose_engine(profile: DatasetProfile) -> str`：`rows_estimate >= settings.SPARK_ROW_THRESHOLD → "spark"`，否则 `"duckdb"`。阈值来自配置，不写死。

## 4. Data Agent 执行流程（CODE-2 + CODE-3 合作的链路）

```
data_agent.run(state, emit):
 1. 取 dataset 元数据(state.context["dataset"]) → profiler.profile_csv → emit {"type":"engine", ...}
 2. router.choose_engine(profile)
 3. duckdb 分支:
    schema_tool.get_schema → llm.generate_json(NL2SQL prompt, 注入 schema+query) → {sql, explanation}
    → sql_tool.guard(sql) 校验/补 LIMIT → duckdb_engine.execute → EngineResult
 4. spark 分支:
    spark_tool.run_skill_stats(csv_path) → spark_engine.run_job → 读输出 CSV → EngineResult(engine="spark")
 5. validator_agent.run: 校验 EngineResult 结构(列名非空/行数≤SQL_MAX_ROWS/engine 一致)
 6. 组装 final_result（见 §6），emit final 事件
```

## 5. SQL Guard（CODE-2 `app/tools/sql_tool.py`，测试重点）

规则（全部实现，供 pytest）：
1. 仅允许单条语句：去注释、去分号后不得含第二个 `;`
2. 必须以 `SELECT` 或 `WITH` 开头（大小写不敏感，允许前置括号/空白）
3. 黑名单词（独立 token 匹配，大小写不敏感）：`INSERT UPDATE DELETE DROP ALTER CREATE ATTACH DETACH COPY EXPORT INSTALL LOAD CALL SET PRAGMA GRANT REVOKE TRUNCATE VACUUM CHECKPOINT`
4. 不含 `LIMIT` 则自动追加 `LIMIT {SQL_MAX_ROWS}`；已含 LIMIT 且值 > SQL_MAX_ROWS → 改写为 SQL_MAX_ROWS（duckdb 分支用；spark 分支不经过 guard）
5. 返回 `(ok: bool, sql_clean: str, reason: str|None)`

## 6. final_result 结构（前端依赖，字段名精确）

```json
{
  "task_id": "...", "query": "...", "engine": "duckdb|spark",
  "sql": "SELECT ...",            // duckdb 分支有；spark 分支为 null
  "explanation": "一句话说明",
  "columns": ["region", "total_sales"],
  "rows": [["华东", 12345.6]],
  "row_count": 5, "truncated": false,
  "chart": {"type": "bar", "x_field": "region", "y_fields": ["total_sales"]},
  "elapsed_ms": 812
}
```

chart 规则：第一列为字符串/日期列作 x_field，其余数值列作 y_fields（最多 3 个）；type 默认 "bar"，x_field 为日期列则 "line"；无法构成图表时 `chart=null`。

## 7. SSE 事件流契约（GET /api/tasks/{task_id}/events）

`text/event-stream`，每个事件 `data: {json}\n\n`，结束发 `event: done`。事件类型：

```json
{"type":"state","status":"routing","task_id":"..."}
{"type":"agent_start","agent":"data_agent","step":1}
{"type":"engine","engine":"duckdb","rows_estimate":10000,"reason":"行数低于阈值"}
{"type":"sql","sql":"SELECT ...","explanation":"..."}        // 仅 duckdb 分支
{"type":"agent_end","agent":"data_agent","latency_ms":812,"status":"ok"}
{"type":"agent_start","agent":"validator_agent","step":2}
{"type":"agent_end","agent":"validator_agent","latency_ms":5,"status":"ok"}
{"type":"final","result":{...§6 结构...}}
{"type":"error","code":"SQL_ERROR|LLM_ERROR|ENGINE_ERROR|VALIDATION_ERROR","message":"..."}
```

CODE-4 负责 SSE 端点（用 `asyncio.Queue` 桥接 agent 的 emit）；agent 只管 `await emit(dict)`。

## 8. REST API 契约

统一前缀 `/api`，错误返回 `{"detail": "..."}`（FastAPI 默认）。

| 方法/路径 | 请求 | 响应 |
|---|---|---|
| POST /api/datasets | multipart file(CSV) | `{"dataset_id","name","table_name","schema":[{name,type}],"rows_estimate","size_mb","engine_hint"}` |
| POST /api/tasks | `{"dataset_id","query"}` | `{"task_id","status":"created"}`（后台启动 run_task） |
| GET /api/tasks/{id} | - | `{"task_id","status","engine","final_result","steps":[{agent_name,status,latency_ms}]}` |
| GET /api/tasks/{id}/events | - | SSE（§7） |
| GET /api/health | - | `{"status":"ok"}` |
| GET /api/health/mysql | - | `{"mysql":"up","latency_ms":n}` 或 503 |
| GET /api/health/redis | - | `{"redis":"up"/"degraded"}`（Redis 挂了返回 degraded 而非 500，体现文档降级原则） |
| POST /api/crawler/run | `{"url"?, "pages"?:1, "max_items"?:10}`（max_items 范围 1~50） | `{"inserted":n,"skipped":m,"items":[{title,company,location,skills[]}],"failed_urls":[{url,error}]}` |
| GET /api/crawler/jobs | `?limit=20` | `{"count":n,"items":[jobs 表行]}` |
| POST /api/crawler/export | - | `{"path":"data/large/jd_crawled.csv","rows":n}`（jobs 表导出 CSV 供 Spark job 用） |

Crawler 默认 URL：`https://realpython.github.io/fake-jobs/`（静态假招聘页，demo 稳定）；留空即用它。

## 9. MySQL DDL（CODE-4 原样写入 `app/persistence/schema.sql`；`scripts/init_db.py` 会执行它）

```sql
CREATE TABLE IF NOT EXISTS datasets (
  id VARCHAR(36) PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  path VARCHAR(512) NOT NULL,
  rows_estimate BIGINT NOT NULL DEFAULT 0,
  size_bytes BIGINT NOT NULL DEFAULT 0,
  schema_json JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS tasks (
  id VARCHAR(36) PRIMARY KEY,
  dataset_id VARCHAR(36) NULL,
  query TEXT NOT NULL,
  status VARCHAR(32) NOT NULL,
  engine VARCHAR(16) NULL,
  current_step INT NOT NULL DEFAULT 0,
  final_result JSON NULL,
  error TEXT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at DATETIME NULL,
  KEY idx_tasks_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS task_steps (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  task_id VARCHAR(36) NOT NULL,
  step INT NOT NULL,
  agent_name VARCHAR(64) NOT NULL,
  status VARCHAR(32) NOT NULL,
  latency_ms INT NULL,
  retry_count INT NOT NULL DEFAULT 0,
  detail JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_steps_task (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS agent_runs (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  task_id VARCHAR(36) NOT NULL,
  agent_name VARCHAR(64) NOT NULL,
  model VARCHAR(64) NULL,
  input_tokens INT NULL,
  output_tokens INT NULL,
  latency_ms INT NULL,
  tool_name VARCHAR(64) NULL,
  error_type VARCHAR(64) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_runs_task (task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS jobs (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  title VARCHAR(255) NOT NULL,
  company VARCHAR(255) NULL,
  location VARCHAR(255) NULL,
  skills TEXT NULL,
  description MEDIUMTEXT NULL,
  source_url VARCHAR(512) NOT NULL,
  crawled_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_job (title, company, source_url(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

`persistence/mysql.py`：PyMySQL 连接（`autocommit=True, charset=utf8mb4`），`get_connection()` 每次新建、用完关；`init_pool` 不需要。写操作封装成函数（`insert_task`, `update_task_status`, `insert_task_step`, `insert_agent_run`, `upsert_job`, `list_jobs`, `insert_dataset`）。连接失败抛出带原错误信息的异常，不静默。

## 10. LLM 客户端（CODE-2 `app/core/llm.py`）

```python
class BaseLLMClient(ABC):
    async def generate_json(self, system: str, user: str) -> dict
class OpenAICompatibleClient(BaseLLMClient):   # from openai import AsyncOpenAI
class MockLLMClient(BaseLLMClient):            # 规则版 NL2SQL，见下
def get_llm_client() -> BaseLLMClient          # 工厂：key 缺失/provider=mock → Mock
```

- OpenAICompatibleClient：`AsyncOpenAI(base_url=settings.LLM_BASE_URL, api_key=settings.LLM_API_KEY)`，`chat.completions.create(model, messages, response_format={"type":"json_object"}, temperature=0)`；解析失败重试 1 次，仍失败抛 `LLMError`（自定义异常）。
- MockLLMClient 规则（针对 demo_sales.csv）：正则抓 "按{col}统计{sales|quantity}"、"平均"、"总计/总"、"Top {n}"、"最近" 等模式生成 GROUP BY/AVG/SUM/ORDER BY SQL；全不命中 → `SELECT * FROM {table} LIMIT 100`。返回 JSON `{"sql": "...", "explanation": "mock 规则生成"}`。
- NL2SQL system prompt 要点：只输出 JSON；只允许单条 SELECT；只读；使用给定 schema 的表名列名；中文列值不需翻译；加 LIMIT。

## 11. Spark 契约（CODE-3）

- `spark/jobs/jd_skill_stats.py`：在容器内运行（容器自带 python3 + pyspark）。参数 `--input /data/xxx.csv --output /data/out/xxx`。逻辑：读 CSV → 按 `,` explode skills 列 → trim/lower 标准化 → groupBy skill 计数 → 按 count 降序 → 结果写 `output` 目录（单个 CSV：`.coalesce(1).csv(header=True)`）。另输出 `output_summary.json`（含总行数、耗时）到 output 的父目录旁。
- `spark_engine.py`：`docker run --rm -v <DATA_DIR>:/data -v <repo>/spark/jobs:/jobs <SPARK_IMAGE> spark-submit --master local[*] --driver-memory 1g /jobs/jd_skill_stats.py --input /data/... --output /data/out/...`，`subprocess` 同步执行（包在 `asyncio.to_thread`），超时 300s，失败抛 `SparkError`，stderr 截断 2000 字符进异常。跑完读 `/data/out/<name>/` 下 part 文件回填 EngineResult(engine="spark")。
- `tools/spark_tool.py`：`run_skill_stats(csv_path) -> EngineResult` 的薄封装，供 data_agent 调用。

## 12. 爬虫契约（CODE-7）

- 目标页 `https://realpython.github.io/fake-jobs/`（纯静态，10 条/页，共多页 `page/2/`…）；requests(UA 头, timeout=15) + BeautifulSoup(html.parser)，不引入 scrapy。
- 解析：`.card-content` 内 `h2.p-title`(title)、`h3.p-subtitle`(company)、`p.location`(location)、`.card-footer-item` 第二个 a 的 href（描述详情页，进详情页抓正文前 800 字）；skills = SKILL_KEYWORDS 表（Python/SQL/Java/JavaScript/Docker/Kubernetes/AWS/Linux/React/Spark/MySQL/Redis/FastAPI/CSS/HTML…）在 title+description 里大小写不敏感匹配去重。
- `storage.py`：`upsert_job(...)` 用 `ON DUPLICATE KEY UPDATE crawled_at=NOW()`（对应 jobs 表 uq_job）。
- `fetcher.py` / `parser.py` / `storage.py` 三文件 + `run(pages, max_items) -> dict`；`api/crawler.py` 暴露 §8 三个路由（`router = APIRouter(prefix="/api/crawler", tags=["crawler"])`）；`export` 从 MySQL 读 jobs 写 `data/large/jd_crawled.csv`（列：title,company,location,skills,description）。
- robots/礼貌：单线程顺序请求，每请求 sleep 0.5s，仅 demo。

## 13. 前端契约（CODE-5）

- Next.js 15 App Router + TS + Tailwind v4 + echarts v5（已写好 package.json/configs，你只写 `frontend/app/**` 与 `frontend/README.md`）。
- `layout.tsx`（html lang=zh，导入 globals.css）、`globals.css`（`@import "tailwindcss";` + 深色底样式微调）、`page.tsx`（`"use client"` 单页应用）。
- API 基址 `process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8100"`。
- 页面区块：① 数据集上传（input file + POST /api/datasets，显示 schema/行数/engine_hint）② 提问输入框 + 示例问题按钮（"按地区统计总销售额""销量 Top 5 的商品"）③ Agent 执行时间线（SSE 事件渲染：agent_start/end 按时间列出，engine 事件显示引擎徽标）④ 结果卡片（SQL 展示 + ECharts 柱状/折线图 + 结果表前 50 行）⑤ 爬虫小面板（输入 URL/页数 → POST /api/crawler/run → 显示抓到的 JD 列表，"导出 CSV 供 Spark"按钮）。
- SSE 用 `EventSource`；error/final 事件关闭连接。图表 useEffect + `echarts.init`，卸载时 dispose。
- `frontend/README.md`：中文通俗讲解（给完全没写过前端的人）：Next.js 是什么、为什么选它、组件/状态(state)概念、每个配置文件（package.json/next.config.mjs/tsconfig.json/postcss.config.mjs）各管什么、Tailwind 怎么用、ECharts 怎么画的、SSE 和普通请求的区别。用类比，少术语。

## 14. 测试契约（CODE-6，pytest，放 `backend/tests/unit/`）

- `test_state.py`：合法迁移通过、非法迁移（created→completed）抛 ValueError、超 MAX_RETRY 进 failed_final
- `test_registry.py`：注册/获取/未注册抛 KeyError
- `test_sql_guard.py`：白名单 SELECT 通过；INSERT/DROP/PRAGMA/多语句/注释内藏分号 拒绝；无 LIMIT 自动补；LIMIT 超上限被改写
- `test_router.py`：阈值上下两侧分别返回 duckdb/spark（monkeypatch settings）
- `test_mock_llm.py`：mock 规则命中"按地区统计销售额"产出含 GROUP BY 的 SQL
- 不测网络/真实 DB；导入路径按 `backend/` 为 cwd（conftest.py 里 `sys.path.insert(0, str(Path(__file__).parents[2]))`）
- 全部用例可在未启动 MySQL/Docker 时通过

## 15. 文档契约（CODE-6）

- `README.md`（中文）：项目一句话简介 → 架构图(ascii) → 环境要求（列出本机已实测的版本）→ 5 步启动（gen_data → init_db → 起服务 → 上传 → 提问）→ 两条 demo（链路A DuckDB / 链路D Spark 路由 + 爬虫→导出→Spark）→ benchmark 占位表（标"待实测"）→ 常见问题（8GB 内存、为什么容器 Spark）。
- `ARCHITECTURE.md`（中文）：目录结构表、扩展点逐条说明（新 Agent 怎么加、新引擎怎么加、换 LLM 怎么配、阈值调哪、Planner 在哪接入）。

## 16. 通用要求

- 并行者互不修改对方文件；需要别人模块的类型就 import（路径契约如上，不存在循环依赖：runtime ← agents ← tools/data_engine ← core）
- 所有 async agent 内的阻塞调用（subprocess、文件 IO、pymysql）用 `asyncio.to_thread` 包裹
- 日志用 `app/core/logging.py` 的 `get_logger(name)`（CODE-4 提供，标准 logging + 简洁 formatter）
- 报告格式：完成的文件清单 + 与契约的偏差说明（如有）+ 遗留问题
