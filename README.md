# AgentInsight — 会看数据的 AI Agent（NL2SQL 数据分析助手）

上传一份 CSV，用中文提问，Agent 自动选引擎（DuckDB / Spark）、生成 SQL、执行、校验并画图；全过程通过 SSE 实时推送执行时间线。20 万行以内的 CSV 走 DuckDB 即席查询，超过阈值自动路由到容器里的 Spark 作业。

## 架构图

```
                    ┌──────────────────────────────────────────────────┐
                    │                    浏览器（:3000）                 │
                    │   Next.js 15 单页：上传 / 提问 / 时间线 / 图表      │
                    └───────▲──────────────────────────▲───────────────┘
                            │ REST (POST /api/...)     │ SSE (EventSource)
                    ┌───────┴──────────────────────────┴───────────────┐
                    │                FastAPI 后端（:8000）               │
                    │  ┌────────────┐  ┌────────────────────────────┐  │
                    │  │  API 层     │  │  Agent Runtime             │  │
                    │  │ datasets   │──▶ Supervisor.route/run_task   │  │
                    │  │ tasks(SSE) │  │  TaskState 状态机 + Registry │  │
                    │  │ crawler    │  └──────┬──────────┬──────────┘  │
                    │  │ health     │         │          │             │
                    │  └─────┬──────┘  ┌──────▼─────┐ ┌──▼──────────┐  │
                    │        │         │ data_agent │ │validator_   │  │
                    │        │         │ NL2SQL+路由 │ │agent 结果校验│  │
                    │        │         └──┬──────┬──┘ └─────────────┘  │
                    │        │   profiler │      │ sql_tool.guard      │
                    │        │   router   │      │ MockLLM/OpenAI 兼容 │
                    │  ┌─────▼──────┐  ┌──▼───────────┐ ┌────────────┐ │
                    │  │ 爬虫(可选)  │  │ DuckDB 内存查询│ │ Spark 容器  │ │
                    │  └─────┬──────┘  └──────────────┘ │ docker run  │ │
                    │        │                          │ --rm 按需起  │ │
                    │  ┌─────▼──────────────────────────▼──────────┐  │
                    │  │   MySQL 8.0（数据集/任务/步骤/JD 落库）      │  │
                    │  └───────────────────────────────────────────┘  │
                    │   Redis（Docker，SSE 缓存，可选，挂了自动降级）     │
                    └──────────────────────────────────────────────────┘
```

## 环境要求

以下为本机已实测环境，其余相近版本一般也可：

| 组件 | 版本 | 说明 |
|---|---|---|
| OS | Windows 11 | |
| Python | 3.12.8（全局安装，无 venv） | 依赖装进全局 |
| MySQL | 8.0.26（本机 Windows 服务 MySQL80，端口 3306） | 存数据集/任务/步骤/爬虫 JD |
| Node.js | 24 | 跑 Next.js 前端 |
| Docker Desktop | 29.2.1 | 起 Redis；Spark 由后端按需 `docker run --rm` 调起 |
| Spark 镜像 | apache/spark:3.5.1 | 首次触发 Spark 链路时自动拉取 |
| Redis 镜像 | redis:7-alpine | 可选 |

Python 依赖见 `backend/requirements.txt`；前端依赖见 `frontend/package.json`。

## 五步启动

```bash
# ① 生成 demo 数据：1 万行销售明细 + 20 万行 JD
python scripts/gen_data.py

# ② 复制 .env.example 为 .env，填好 MySQL 密码后初始化库表与账号
python scripts/init_db.py

# ③ 起 Redis（可选；不起也能跑，系统自动降级）
docker compose up -d

# ④ 起后端（在 backend 目录下）
cd backend
python -m uvicorn app.main:app --port 8000

# ⑤ 起前端（新开终端，在 frontend 目录下）
cd frontend
npm install
npm run dev
```

浏览器打开 http://localhost:3000 即可使用。

LLM 配置说明：在 `.env` 里填 `LLM_API_KEY`（DeepSeek / GLM 等 OpenAI 兼容接口均可）；没有 key 时把 `LLM_PROVIDER=mock`（或留空 key），内置规则版 NL2SQL 也能跑通全链路。

## 三条 Demo

### 链路 A：DuckDB 即席查询（小文件）

1. 首页上传 `data/demo/demo_sales.csv`（1 万行销售明细）。
2. 在提问框输入：**按地区统计总销售额**（或点示例按钮）。
3. 观察 Agent 时间线：`data_agent` 画像文件 → 行数低于 10 万阈值 → 选择 **DuckDB** → 生成 SQL → 执行 → `validator_agent` 校验 → 结果卡片显示 SQL、柱状图与结果表。

### 链路 D：Spark 路由（大文件自动分流）

1. 上传 `data/large/jd_large.csv`（20 万行合成 JD，超过 10 万行阈值）。
2. 提问：**JD 中需求最多的技能 Top 10**。
3. 时间线出现 `engine: spark` 事件，后端按需 `docker run --rm apache/spark:3.5.1 spark-submit ...` 执行 `spark/jobs/jd_skill_stats.py`，对 skills 列做 explode + groupBy 计数，结果回填为图表。首次运行需拉镜像并启动 JVM，约 1–2 分钟属正常。

### 爬虫 → 导出 CSV → 喂给 Spark

1. 前端爬虫面板（或 `POST /api/crawler/run`，body 留空即抓默认假招聘页）抓取岗位数据入库。
2. 点「导出 CSV 供 Spark」（或 `POST /api/crawler/export`），生成 `data/large/jd_crawled.csv`。
3. 把这个 CSV 当数据集上传（行数够大即触发 Spark），或直接重新提问做技能统计——与链路 D 同一条 Spark 作业。

## Benchmark（实测于本机 2026-08-30，Windows 11 / Python 3.12 / DuckDB 1.5.5）

| 场景 | 数据规模 | 引擎 | 端到端耗时 | 峰值内存 |
|---|---|---|---|---|
| 按地区统计总销售额（含路由/校验全链路） | 1 万行 CSV | DuckDB | **82 ms**（task elapsed） | 待实测 |
| 技能 Top 10（视图注册 / 查询分列） | 20 万行 CSV | DuckDB | **注册 90 ms / 查询 105 ms** | 待实测 |
| 技能 Top 10 | 20 万行 CSV | Spark（容器） | 待实测（镜像一键构建：`docker build -f docker/spark.Dockerfile -t apache/spark:3.5.1 docker/`，pyspark 已验证装好） | 待实测 |
| 爬虫抓取 1 页（10 条含详情页 + 入库） | 10 条 | - | **17.7 s**（礼貌性 0.5s/请求 + 逐条抓详情页） | - |
| LLM 生成 SQL | - | mock 规则 0 ms（本地正则） | DeepSeek/GLM 待填 key 实测 | - |

> 测量口径：DuckDB 两行来自 `scripts/bench_duckdb.py` 与链路 A SSE final 事件的 elapsed_ms；爬虫为 `time curl` 墙钟。所有数字为真实运行结果，未预填。

## .env 变量说明

| 变量 | 说明 | 缺省行为 |
|---|---|---|
| `LLM_PROVIDER` | `openai` / `mock` | 无 key 或 `mock` 时自动降级内置规则版 NL2SQL |
| `LLM_BASE_URL` | OpenAI 兼容接口地址，如 `https://api.deepseek.com/v1` | 空 |
| `LLM_API_KEY` | 对应服务商的 key | 空则降级 mock |
| `LLM_MODEL` | 模型名，如 `deepseek-chat` / `glm-4-flash` | 空 |
| `MYSQL_HOST` / `MYSQL_PORT` | MySQL 地址 | 127.0.0.1 / 3306 |
| `MYSQL_ROOT_PASSWORD` | 仅 `scripts/init_db.py` 初始化时使用 | 空 |
| `MYSQL_USER` / `MYSQL_PASSWORD` | 应用账号（init_db 会创建并授权） | agent_app / 空 |
| `MYSQL_DATABASE` | 库名 | agentinsight |
| `REDIS_URL` | Redis 连接串（Docker 里的 Redis） | redis://127.0.0.1:6379/0 |
| `MAX_AGENT_STEPS` | 单任务最大步数上限 | 8 |
| `MAX_RETRY` | agent 单步失败重试次数，耗尽进 `failed_final` | 2 |
| `SQL_MAX_ROWS` | 结果行数上限；guard 自动补/改写 LIMIT | 1000 |
| `SPARK_ROW_THRESHOLD` | 行数 ≥ 该值路由到 Spark | 100000 |
| `SPARK_IMAGE` | spark-submit 用的镜像 | apache/spark:3.5.1 |
| `DATA_DIR` / `UPLOAD_DIR` | 数据与上传目录 | `<repo>/data` 与 `<repo>/data/uploads` |

## 常见问题

**8GB 内存可行吗？**
可行。MySQL、后端、前端都是轻量常驻；DuckDB 查询是流式内存计算，10 万行级 CSV 占用很小。最吃内存的是 Spark 容器，后端用 `--driver-memory 1g` 限制，且 `docker run --rm` 用完即销毁、不常驻。建议 Windows 给 Docker Desktop 分配 3–4GB 即可，其余留给系统。

**为什么 Spark 用容器而不是本机装 Spark？**
三点：① 免安装——不用配 JDK/SCALA/HADOOP_HOME 一堆环境变量，有 Docker 就能跑；② 环境一致——镜像自带匹配的 Python 与 PySpark 版本，避免 Windows 本机 Spark 的各种兼容坑；③ 干净——`docker run --rm` 每次作业起一个容器、跑完即删，不占常驻资源，也方便以后换镜像版本做对比。

**为什么不用 LangChain？**
本项目核心是展示 Agent 的运行时机制：状态机、消息流、SSE 事件、重试与校验。自己写 Supervisor + Registry 只有几百行，每一步都可控、可测、可解释；套 LangChain 反而把编排黑盒化，调试和教学成本都更高。LLM 只用来做 NL2SQL 这一件事，一个 OpenAI 兼容客户端（约 40 行）足够。

**Redis 挂了会怎样？**
不影响主链路。Redis 只做辅助缓存，后端对 Redis 的所有调用都有降级处理：连不上时记 warning、`GET /api/health/redis` 返回 `{"redis":"degraded"}` 而非 500，任务照常执行。SSE 事件本体走进程内 TaskBus，不依赖 Redis。想彻底省资源可以不启动 Redis 容器。

## 更多文档

- 系统设计与扩展指南（目录职责、数据流、五个扩展点）：见 [ARCHITECTURE.md](ARCHITECTURE.md)
- 前端实现讲解：见 [frontend/README.md](frontend/README.md)
