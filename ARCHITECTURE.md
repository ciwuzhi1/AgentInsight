# ARCHITECTURE — AgentInsight 系统设计与扩展指南（2026-03-11 优化后）

> 本文描述的是**已实现**的架构。全量优化 18 项已落地，详见 README「全量优化」章节。

## 1. 一句话架构

自研 Multi-Agent Runtime（DAG 计划 + 波次并行执行）+ DuckDB/Spark 双引擎路由 + MySQL 持久化 trace + Redis 单例缓存（降级安全）+ Next.js 前端，全部配置（含 LLM 与密钥）经设置中心热生效。

## 2. 目录结构与职责

```
AgentInsight/
├── backend/app/
│   ├── main.py                # FastAPI 入口：路由装配、CORS 白名单、全局异常、请求日志、TaskBus 清扫、lifespan 清理
│   ├── api/                   # HTTP 层（Settings/Models/Eval 已加鉴权）
│   │   ├── agent.py           #   任务创建 + SSE（TaskBus：事件回放/上限500/TTL清扫）+ trace 落库
│   │   ├── datasets.py        #   CSV 上传（≤10MB，413）→ 画像 → DuckDB 视图注册
│   │   ├── resumes.py         #   简历上传（pdf/docx/txt）与查询
│   │   ├── matches.py         #   匹配任务（复用 tasks+SSE 全链路）
│   │   ├── models.py          #   模型配置 CRUD/激活/连通测试（Fernet 加密 + 鉴权）
│   │   ├── settings.py        #   功能开关读写（白名单键 + 鉴权）
│   │   ├── crawler.py         #   JD 抓取/列表/导出 CSV
│   │   └── health.py          #   三件套（Redis 挂了返回 degraded 不 500）
│   ├── agent_runtime/         # ★ 自研运行时（不依赖 LangGraph/LangChain）
│   │   ├── state.py           #   TaskState：8 状态机 + plan_steps + messages
│   │   ├── planner.py         #   PlanStep DAG 两个模板（match/data）+ 拓扑校验（环/依赖/步数）
│   │   ├── executor.py        #   WorkflowExecutor：波次拓扑，波内 asyncio.gather 并行；
│   │   │                      #   指数退避重试(0.5/1/2s)；optional 步骤耗尽→跳过(Replan)；
│   │   │                      #   AgentResult→AgentMessage 包装进 state.messages
│   │   ├── supervisor.py      #   复杂度路由（简历上下文→匹配计划；否则数据计划）+ run_task 委托
│   │   ├── registry.py        #   AgentRegistry 注册表单例
│   │   └── message.py         #   AgentMessage 协议 + make_message 工厂
│   ├── agents/                # 六个 Agent，统一 BaseAgent.run(state, emit)->AgentResult
│   │   ├── data_agent         #   画像→引擎路由→NL2SQL(mock/真模型)→SQL Guard→DuckDB/Spark
│   │   ├── resume_agent       #   解析链：MinerU API→PyMuPDF 兜底→docx→txt；LLM 结构化+正则兜底
│   │   ├── job_agent          #   JD 结构化（must_have/nice_to_have/skills）
│   │   ├── match_agent        #   规则五维打分(0.5/0.2/0.1/0.1/0.1)+同义词归一+缺口；LLM 解读可开关
│   │   └── validator_agent    #   双分支校验（数据形态/匹配形态）
│   ├── data_engine/           # AnalysisEngine 抽象：duckdb_engine(内存限1GB/查询30s超时)、
│   │                          # spark_engine(docker run spark-submit)、profiler、router、EngineResult
│   ├── core/
│   │   ├── config.py          #   pydantic-settings 唯一读 .env 入口
│   │   ├── llm.py             #   LLM 工厂：DB激活配置→.env→mock 三级回退；is_mock 标记
│   │   ├── app_settings.py    #   功能开关（10s TTL 缓存，DB 不可用回退默认）
│   │   ├── crypto.py          #   Fernet 加解密；APP_SECRET 首次自动生成写回 .env
│   │   └── logging.py         #   统一 get_logger
│   ├── persistence/           # schema.sql（8 表）+ pymysql 封装（连接超时5/15/15s+瞬时重试）
│   ├── cache/                 # Redis 封装（挂了返回 None 降级）——实用化待办见 ROADMAP P2
│   ├── crawler/               # fetcher(限速0.5s)/parser(演示站)/storage(upsert jobs 表)
│   ├── context/               # 预留：跨轮上下文（未实现）
│   └── evaluation/            # 预留：评测体系（未实现，ROADMAP P3）
├── backend/tests/unit/        # 57 单测（全离线）：状态机/注册表/SQL Guard/路由/mock LLM/
│                              # planner DAG/executor 并行退避/打分/TaskBus 有界/简历解析
├── frontend/app/              # Next.js 15：主页（吸顶导航+三卡片工作台：数据分析/简历匹配/爬虫）
│   └── settings/page.tsx      #   /settings 独立设置页（模型+开关）
├── spark/jobs/jd_skill_stats.py  # 容器内 PySpark 作业（镜像未构建，ROADMAP P5）
├── scripts/                   # gen_data / init_db / bench_duckdb / chain_d_demo / api_bench
├── docker-compose.yml         # 仅 Redis（MySQL 用本机服务，Spark 按需）
└── ROADMAP.md                 # ★ 实用阶段计划表
```

**端口**：前端 3100 / 后端 8100（根路径是人类导航页；接口文档 `/docs`）。

## 3. 运行时数据流（以匹配链路为例）

```
POST /api/matches {resume_id, job_ids}
  → 校验 resume/jobs → TaskState(context={resume, jobs}) → insert_task → 后台 _run
  → Supervisor.route(): context 含 resume → build_match_plan()
  → emit {"type":"plan", steps:[resume∥job → match → validator]}     ← 前端建步骤卡
  → WorkflowExecutor:
      波1: asyncio.gather( resume_agent.run ‖ job_agent.run )          ← 真·并行
           每个成功结果 → make_message(receiver=[下游]) → state.messages
           → emit agent_end{step, latency, detail:{message_id,...}}
      波2: match_agent（只从 messages 取上游 profile，不直接调其他 agent）
      波3: validator_agent（匹配形态校验）
  → _finish 组装 final{score, dimensions, skill_gap, interpretation}
  → emit {"type":"final"} → 前端匹配结果卡（分数环/分项条/缺口标签）
并行落地证据：task_steps 表中 resume/job 两步 running→ok 交错记录。
```

trace 双路落库机制不变：emit 同一回调 → ①TaskBus（SSE 回放，晚连接也能看全时间线）②`_persist_event`→MySQL（tasks/task_steps/agent_runs）。落库失败仅 warning。

## 4. 配置体系（设置中心）

- **来源优先级**：MySQL `model_configs`(激活行) → `.env` → MockLLM。模型热切换无需重启（10s TTL 缓存）。
- **功能开关**（`app_settings` 表）：`match_llm_enabled` / `llm_fallback_mock(auto|never)` / `parser_backend(mineru_api|pymupdf)` / `sql_timeout` / `mineru_api_token` / `tavily_api_key`。
- **密钥安全**：Fernet 加密落库，回显脱敏 `***尾4位`；密钥 APP_SECRET 自动生成于 .env（git 忽略）。

## 5. 健壮性清单（全部实测过，见 README 延迟表）

TaskBus 事件上限 500（drop-oldest）+ 完结任务 30min TTL 清扫 + Task 引用防 GC ｜ 上传 ≤10MB 413 ｜ DuckDB memory_limit 1GB / threads 4 / 查询 30s 强制超时 ｜ MySQL 连接超时+瞬时重试 ｜ LLM 指数退避 ｜ 全局异常处理 + 请求日志 ｜ SQL Guard（只读白名单/黑名单 token/LIMIT 规范化）。

## 6. 扩展点（阶段2 更新）

1. **加 Agent**：继承 BaseAgent 实现 run() + `_register_agents()` 注册 + `planner.py` 加模板步骤——并行/重试/消息/落库全自动获得。
2. **加引擎**：实现 AnalysisEngine 三方法 + router 加分支（Spark 即此模式）。
3. **换 LLM**：设置中心 UI 直接加配置激活（零代码零重启）；非 OpenAI 协议才需要写 BaseLLMClient 子类。
4. **改执行计划**：`planner.py` 的模板即 DAG 声明；动态 LLM 规划的接入点在 `Supervisor.route()` 注释处。
5. **框架决策**：不引入 LangGraph/DeepAgents——链路是确定性 DAG，自研 executor 仅数百行且 trace/消息协议可讲原理；对照表见 README。

## 7. 企业级工程化

- **日志**：`app/core/logging.py` JSON 行格式（contextvars 注入 request_id/task_id）；访问日志在 `main.py` 中间件
- **探针**：`/healthz`（存活）、`/readyz`（就绪，依赖 MySQL；Redis 降级不阻塞）、`/metrics`（进程内指标）
- **限流**：`app/core/rate_limit.py` 滑动窗口（auth 5/min·IP+用户名；task 30/min·用户）
- **迁移**：`app/persistence/migrations.py` 版本化迁移，lifespan 启动自动应用
- **依赖**：requirements.txt 全量精确锁版

## 8. 测试与复现

```bash
cd backend && python -m pytest tests/unit -q   # 57 全绿，离线
python scripts/api_bench.py --n 10             # 接口延迟分层实测（L1~L4）
python scripts/bench_duckdb.py                 # DuckDB 20万行聚合基准
```
