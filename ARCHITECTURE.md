# ARCHITECTURE — AgentInsight 系统设计与扩展指南

本文面向想读懂或改造这个项目的人：先看目录结构与职责，再看一次提问的完整数据流，最后是五个最常用的扩展点。

## 1. 目录结构

```
AgentInsight/
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI 入口：挂载各 APIRouter、lifespan
│   │   ├── api/                     # HTTP 层（参数校验、SSE 端点、落库）
│   │   ├── agent_runtime/           # Agent 运行时：状态机/消息/注册表/编排
│   │   ├── agents/                  # 具体业务 agent（data_agent、validator_agent）
│   │   ├── tools/                   # agent 用的工具：schema_tool/sql_tool/spark_tool
│   │   ├── core/                    # config（pydantic-settings）、llm、logging
│   │   ├── data_engine/             # 数据画像/路由/DuckDB/Spark 引擎封装
│   │   ├── persistence/             # MySQL：schema.sql + pymysql 薄封装
│   │   ├── cache/                   # Redis（可降级）
│   │   ├── crawler/                 # 招聘页爬虫：fetcher/parser/storage
│   │   ├── context/                 # 预留：跨轮次上下文记忆
│   │   └── evaluation/              # 预留：NL2SQL 质量评估
│   ├── tests/
│   │   ├── conftest.py              # sys.path 指向 backend/
│   │   └── unit/                    # 纯逻辑单测（不连 MySQL/Docker）
│   └── requirements.txt
├── frontend/                        # Next.js 15 App Router 单页应用
├── spark/jobs/jd_skill_stats.py     # 容器内运行的 PySpark 作业
├── scripts/                         # gen_data.py（造数）、init_db.py（建库）
├── data/                            # demo/、large/、uploads/、out/
├── docker-compose.yml               # 只放 Redis；Spark 按需 docker run
├── .env.example                     # 全部配置项模板
├── README.md / ARCHITECTURE.md / CONTRACTS.md
```

### 逐目录职责

| 目录/文件 | 职责 | 关键约定 |
|---|---|---|
| `app/api/` | REST + SSE 端点；把 agent 事件桥接到 TaskBus 并落库 | 只做 IO 与协议，不含分析逻辑；错误返回 `{"detail": ...}` |
| `app/agent_runtime/` | `state.py` 任务状态机（8 状态，非法迁移抛 ValueError）；`message.py` agent 间消息；`registry.py` 按名注册/查找 agent；`supervisor.py` 路由决策 + `run_task` 编排 + 重试 | 不 import persistence——运行时与存储完全解耦，落库由 API 层完成 |
| `app/agents/` | `base.py` 定义 `BaseAgent.run(state, emit) -> AgentResult` 抽象；`data_agent` 画像→路由→NL2SQL→guard→执行；`validator_agent` 校验 EngineResult 结构 | agent 之间不互相调用，只通过 supervisor 调度；进度一律 `await emit(dict)` |
| `app/tools/` | `sql_tool.guard()` 只读校验 + LIMIT 规范化；`schema_tool` 取表结构；`spark_tool` Spark 作业薄封装 | 工具是无状态函数/薄类，方便单测 |
| `app/core/` | `config.py` 唯一读 .env 的地方（pydantic-settings 单例 `settings`）；`llm.py` OpenAI 兼容客户端 + MockLLMClient 规则版 NL2SQL；`logging.py` 统一 get_logger | 其他模块只 `from app.core.config import settings`，不自己读环境变量 |
| `app/data_engine/` | `profiler.profile_csv()` 生成 DatasetProfile（行数/列名/大小）；`router.choose_engine()` 按阈值选引擎；`duckdb_engine` / `spark_engine` 实现 `AnalysisEngine` 接口；`result.py` 统一的 `EngineResult` | 引擎差异被接口吃掉，agent 只面对 `EngineResult` |
| `app/persistence/` | `schema.sql` 五张表 DDL；`mysql.py` 每次新建连接、写操作封装成函数 | 连接失败抛带原始信息的异常，不静默 |
| `app/cache/` | Redis get/set 封装，连不上降级返回 None | 挂了不影响主链路 |
| `app/crawler/` | fetcher（requests，限速 0.5s）→ parser（BeautifulSoup + 技能关键词匹配）→ storage（upsert 到 jobs 表） | 仅 demo 用途，单线程顺序抓取 |
| `spark/jobs/` | `jd_skill_stats.py`：CSV 读入 → skills explode → groupBy 计数 → coalesce(1) 写单个 CSV + summary json | 在 `apache/spark:3.5.1` 容器内执行，宿主机不需要装 Spark |
| `backend/tests/unit/` | 状态机 / registry / sql_guard / router / mock_llm 纯逻辑单测 | `cd backend && python -m pytest tests/unit -q`，无需 MySQL/Docker |

## 2. 运行时数据流

一次提问从前端到图表的完整链路：

```
前端提问 POST /api/tasks {dataset_id, query}
        │
        ▼
api/agent.py: create_task
  1. MySQL 读数据集元数据 → TaskState(dataset_id, query)
  2. insert_task 落库（status=created）
  3. asyncio.create_task 后台执行，立即返回 {task_id, status:"created"}
        │
        ▼
后台 _run(state)：定义 emit 回调后交给 Supervisor().run_task(state, emit)
        │
        ▼
Supervisor.run_task ── emit(event) ──┬──▶ TaskBus.publish(task_id, event)
  route(): 规则生成 plan             │        │
  逐个执行 plan 中的 agent           │        └──▶ api 层 _persist_event():
  validator_agent 校验               │              state→update_task(status)
  组装 final_result（§6 结构）        │              agent_start/end→insert_task_step
        │                            │              final→update_task(completed,
        ▼                            │                 engine, final_result)
                                     │              error→update_task(failed_final)
                                     ▼
                          GET /api/tasks/{id}/events (SSE)
                          subscribe：先回放 Bus 里已有事件，再阻塞等新事件，
                          遇 final/error 终止 → event: done
                                     │
                                     ▼
                          前端 EventSource 逐条渲染时间线，
                          final 事件驱动结果卡片（SQL + ECharts + 表格）
```

### trace 如何落 MySQL

agent 本身不知道数据库的存在。它只调 `await emit(event_dict)`，API 层的 `_run` 把同一个回调接了两路：

1. **内存路**：`TaskBus.publish` 追加到该任务的事件列表并 `notify_all`，SSE 订阅者（可能晚连接）先回放全部历史再等增量，所以刷新页面也能看到完整时间线。
2. **落库路**：`_persist_event` 按事件类型映射到 `persistence/mysql.py` 的写函数——
   - `state` 事件 → `update_task(status)` 更新 tasks 表状态；
   - `agent_start` / `agent_end` → `insert_task_step`，记录 agent 名、状态、`latency_ms`、`retry_count`（task_steps 表，即执行 trace）；
   - `final` → `update_task(completed, engine=..., final_result=...)`，把 §6 结构整体存进 tasks.final_result JSON 列；
   - `error` → `update_task(failed_final, error=...)`。

所有落库失败只记 warning 不抛出——观测性的缺失不应该打断用户的分析请求。

### 状态机与重试

`TaskState.transition` 维护合法迁移表（created→routing→running→validating→completed；running/validating→failed→retrying→running 循环）。agent 抛异常或返回 error 时，supervisor 走 `_enter_retry`：`retry_count < settings.MAX_RETRY` 则 retrying→running 重跑当前步，耗尽则 failed→failed_final 终态。`completed` 与 `failed_final` 都是终态，不可迁出。

## 3. 五个扩展点

### 3.1 加一个新 Agent（两步走）

1. 在 `app/agents/` 新建文件，继承 `BaseAgent`，实现 `async def run(self, state, emit) -> AgentResult`。进度通过 `await emit({"type": "...", ...})` 发出；返回 `AgentResult(status="ok", message_type=..., data={...})`，supervisor 会把 `data` 存进 `state.results[你的名字]`。
2. 在 `app/api/agent.py` 的 `_register_agents()` 里照葫芦画瓢加一段「未注册则 import 并 `registry.register(XxxAgent())`」。

就这两步——调度、重试、事件落库、SSE 全部复用现成的 supervisor 管线。若要让新 agent 进入执行计划，再在 `Supervisor.route()`（见 3.5）里加一条路由规则即可。

### 3.2 加一个新引擎（比如本地 Polars）

1. 在 `app/data_engine/` 新建 `polars_engine.py`，实现 `AnalysisEngine` 三个方法：`register_dataset` / `get_schema` / `async execute(...)-> EngineResult`（阻塞计算记得 `asyncio.to_thread`）。
2. 在 `app/data_engine/router.py` 的 `choose_engine()` 加判断分支（比如按文件格式或列数），返回新引擎名。

agent 与 validator 只认 `EngineResult` 结构，不感知引擎数量，前端引擎徽标直接渲染 `engine` 字符串。

### 3.3 换一个 LLM（三项配置，零代码）

`.env` 改三项即可，任何 OpenAI 兼容服务（DeepSeek / GLM / 通义 / vLLM 自部署）都行：

```env
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_API_KEY=sk-xxx
LLM_MODEL=deepseek-chat
```

`app/core/llm.py` 的工厂 `get_llm_client()` 自动选择：key 无效或 `LLM_PROVIDER=mock` 时降级 `MockLLMClient`（规则版 NL2SQL，离线可跑）。要接非 OpenAI 协议的服务，再实现一个 `BaseLLMClient` 子类挂进工厂即可。

### 3.4 调路由阈值

`SPARK_ROW_THRESHOLD`（默认 100000）决定行数多少以上走 Spark。改 `.env` 后重启后端即生效；`router.choose_engine` 读的是运行时配置，不写死。想按查询复杂度分流（简单聚合即使大表也留 DuckDB），见下一个扩展点。

### 3.5 Planner 接入点

`app/agent_runtime/supervisor.py` 的 `Supervisor.route()`，方法 docstring 与 `# 预留扩展` 注释处：当前 MVP 是单 agent 直通（无数据集→clarify，求职意图打 mark，默认→data_agent）。接 Planner 时在 `route()` 里做查询复杂度评估（是否需要多步分解、多 agent 协作），返回更长的 `plan` 列表——`run_task` 已经按 plan 顺序逐个执行 agent，无需改动编排逻辑。

## 4. 测试

```bash
cd backend
python -m pytest tests/unit -q
```

36 个用例覆盖：状态机合法/非法迁移与重试耗尽、agent 注册表、SQL guard 白/黑名单与 LIMIT 规范化、引擎路由阈值两侧（含 monkeypatch 配置）、MockLLM 的 GROUP BY 生成与兜底。全部用例不依赖 MySQL / Docker / 网络。
