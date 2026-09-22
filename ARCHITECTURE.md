# ARCHITECTURE — AgentInsight 系统设计与扩展指南

> 图解版见 [`docs/架构说明.md`](docs/架构说明.md)。本文侧重实现细节与扩展指南。

## 1. 一句话架构

自研 Multi-Agent Runtime（DAG 计划 + 波次并行执行 + 动态复杂度路由）+ DuckDB 单引擎数据分析 + Context Engineering（Token 预算 + 上下文压缩）+ Report Synthesizer（模板汇总 + 可选 LLM 润色）+ MySQL 持久化 trace + Redis 单例缓存（降级安全）+ Next.js 前端，全部配置（含 LLM 与密钥）经设置中心热生效。

## 2. 目录结构与职责

```
AgentInsight/
├── backend/app/
│   ├── main.py                # FastAPI 入口：路由装配、CORS 白名单、全局异常、请求日志、TaskBus 清扫、lifespan 清理
│   ├── api/                   # HTTP 层（Settings/Models/Eval 已加鉴权）
│   │   ├── agent.py           #   任务创建 + SSE（TaskBus：事件回放/上限500/TTL清扫）+ trace 落库
│   │   ├── auth.py            #   注册/登录/me + get_current_user 依赖（Bearer JWT）
│   │   ├── datasets.py        #   CSV 上传（≤10MB，413）→ 画像 → DuckDB 视图注册
│   │   ├── resumes.py         #   简历上传（pdf/docx/txt）与查询
│   │   ├── matches.py         #   匹配任务（复用 tasks+SSE 全链路）
│   │   ├── models.py          #   模型配置 CRUD/激活/连通测试（Fernet 加密 + 鉴权）
│   │   ├── settings.py        #   功能开关读写（白名单键 + 鉴权）
│   │   ├── crawler.py         #   JD 抓取/列表/导出 CSV
│   │   ├── evaluation.py      #   评测报告只读接口
│   │   └── health.py          #   三件套（Redis 挂了返回 degraded 不 500）
│   ├── agent_runtime/         # ★ 自研运行时（不依赖 LangGraph/LangChain）
│   │   ├── state.py           #   TaskState：8 状态机 + plan_steps + messages
│   │   ├── planner.py         #   PlanStep DAG 模板（match/data）+ 拓扑校验（环/依赖/步数）
│   │   ├── complexity.py      #   查询复杂度评估：SIMPLE/NORMAL/COMPLEX → 决定执行链路
│   │   ├── executor.py        #   WorkflowExecutor：波次拓扑，波内 asyncio.gather 并行；
│   │   │                      #   指数退避重试(0.5/1/2s)；optional 步骤耗尽→跳过(Replan)；
│   │   │                      #   AgentResult→AgentMessage 包装进 state.messages
│   │   ├── supervisor.py      #   复杂度路由（简历上下文→匹配计划；否则按复杂度选择数据计划）
│   │   │                      #   + run_task 委托
│   │   ├── registry.py        #   AgentRegistry 注册表单例
│   │   └── message.py         #   AgentMessage 协议 + make_message 工厂
│   ├── agents/                # 七个 Agent，统一 BaseAgent.run(state, emit)->AgentResult
│   │   ├── data_agent         #   画像→复杂度评估→Few-shot 检索→NL2SQL(mock/真模型)→SQL Guard→DuckDB
│   │   ├── resume_agent       #   解析链：MinerU API→PyMuPDF 兜底→docx→txt；LLM 结构化+正则兜底
│   │   ├── job_agent          #   JD 结构化（must_have/nice_to_have/skills）
│   │   ├── match_agent        #   规则五维打分(0.5/0.2/0.1/0.1/0.1)+同义词归一+缺口；LLM 解读可开关
│   │   ├── validator_agent    #   双分支校验（数据形态/匹配形态）
│   │   ├── report_agent       #   ReportSynthesizer：模板化汇总 + 可选 LLM 润色
│   │   └── few_shot.py        #   Few-shot 检索：TF-IDF 相似度，历史成功 SQL 作为 LLM 示例
│   ├── context/               # ★ Context Engineering（V3.0 新增）
│   │   ├── budget.py          #   TokenBudget：max_input/output/context_tokens + truncate_input
│   │   ├── compressor.py      #   上下文压缩：compress_resume/job/schema/result
│   │   └── builder.py         #   上下文构建：build_match_context / build_nl2sql_context
│   ├── data_engine/           # AnalysisEngine 抽象：duckdb_engine(内存限1GB/查询30s超时)、
│   │                          # profiler、router(V3.0 仅 DuckDB 分支)、EngineResult
│   ├── core/
│   │   ├── config.py          #   pydantic-settings 唯一读 .env 入口
│   │   ├── llm.py             #   LLM 工厂：DB激活配置→.env→mock 三级回退；is_mock 标记
│   │   ├── app_settings.py    #   功能开关（10s TTL 缓存，DB 不可用回退默认）
│   │   ├── crypto.py          #   Fernet 加解密；APP_SECRET 首次自动生成写回 .env
│   │   ├── security.py        #   JWT 签发/验证 + 密码哈希
│   │   ├── rate_limit.py      #   滑动窗口限流（auth 5/min；task 30/min）
│   │   └── logging.py         #   统一 get_logger（JSON 结构化日志）
│   ├── persistence/           # schema.sql + migrations + pymysql 封装
│   ├── cache/                 # Redis 封装（挂了返回 None 降级）+ 单例连接 + 幂等锁
│   ├── crawler/               # fetcher(限速0.5s)/parser(演示站)/storage(upsert jobs 表)
│   └── evaluation/            # 评测体系：cases / runner / metrics
├── backend/tests/unit/        # 77+ 单测（全离线）：状态机/注册表/SQL Guard/路由/mock LLM/
│                              # planner DAG/executor 并行退避/打分/TaskBus 有界/简历解析/
│                              # complexity/few_shot/context/report
├── frontend/app/              # Next.js 15：主页（吸顶导航+三卡片工作台：数据分析/简历匹配/爬虫）
│   └── settings/page.tsx      #   /settings 独立设置页（模型+开关）
├── scripts/                   # gen_data / init_db / bench_duckdb / api_bench
├── docker-compose.yml         # 仅 Redis（MySQL 用本机服务）
└── docs/                      # API.md / 架构说明 / 测试说明 / 截图
```

**端口**：前端 3100 / 后端 8100（根路径是人类导航页；接口文档 `/docs`）。

## 3. 运行时数据流

### 3.1 数据分析链路

```
POST /api/tasks {dataset_id, query}
  → 校验 dataset → TaskState → insert_task → 后台 _run
  → Supervisor.route(): assess_complexity(query)
      SIMPLE  → [data_agent]                          # 短问题+聚合词，跳过 validator
      NORMAL  → [data_agent → validator_agent]         # 默认链路
      COMPLEX → [data_agent → validator_agent → report_synthesizer]  # 增强校验+报告
  → emit {"type":"plan", steps:[...]}                   ← 前端建步骤卡
  → WorkflowExecutor:
      波1: data_agent.run(state, emit)
           → 画像 → Few-shot 检索(top-3) → NL2SQL → SQL Guard → DuckDB 执行
           → emit agent_end{step, latency, detail}
      波2: validator_agent.run（数据形态校验）
      波3: report_synthesizer.run（模板汇总 + 可选 LLM 润色）
  → _finish 组装 final{columns, rows, chart_type, sql, engine}
  → emit {"type":"final"} → 前端结果卡（SQL/图表/数据表）
```

### 3.2 匹配链路

```
POST /api/matches {resume_id, job_ids}
  → 校验 resume/jobs → TaskState(context={resume, jobs}) → insert_task → 后台 _run
  → Supervisor.route(): context 含 resume → build_match_plan()
  → emit {"type":"plan", steps:[resume∥job → match → validator → report]}
  → WorkflowExecutor:
      波1: asyncio.gather( resume_agent.run ‖ job_agent.run )    ← 真·并行
           每个成功结果 → make_message(receiver=[下游]) → state.messages
           → emit agent_end{step, latency, detail:{message_id,...}}
      波2: match_agent（只从 messages 取上游 profile，不直接调其他 agent）
      波3: validator_agent（匹配形态校验）
      波4: report_synthesizer（模板汇总 + 可选 LLM 润色）
  → _finish 组装 final{score, dimensions, skill_gap, interpretation, report}
  → emit {"type":"final"} → 前端匹配结果卡（分数环/分项条/缺口标签/报告）
并行落地证据：task_steps 表中 resume/job 两步 running→ok 交错记录。
```

trace 双路落库机制不变：emit 同一回调 → ①TaskBus（SSE 回放，晚连接也能看全时间线）②`_persist_event`→MySQL（tasks/task_steps/agent_runs）。落库失败仅 warning。

## 4. Context Engineering（V3.0 新增）

### 4.1 Token 预算控制

```python
@dataclass
class TokenBudget:
    max_input_tokens: int = 4000    # 单次 LLM 输入上限
    max_output_tokens: int = 1000   # 单次 LLM 输出上限
    max_context_tokens: int = 8000  # 任务总上下文上限

    def truncate_input(self, text: str) -> str:
        """超预算时按比例截断"""
```

Token 估算：中文 1 字 ≈ 1 token，英文按字符/4 粗估。

### 4.2 上下文压缩

| 压缩目标 | 保留字段 | 限制 |
|---------|---------|------|
| 简历（compress_resume） | skills / projects / education / experience_years | skills ≤20，projects ≤5 |
| JD（compress_job） | title / must_have / nice_to_have / skills | must_have ≤10，skills ≤15 |
| Schema（compress_schema） | 列名+类型 | ≤20 列 |
| 结果（compress_result） | 表头+前几行样例 | ≤5 行 |

### 4.3 上下文构建

```python
# 匹配任务上下文
build_match_context(resume, jobs, budget)
  → compress_resume + compress_job(jobs[:5]) → budget.truncate_input()

# NL2SQL 上下文
build_nl2sql_context(schema_str, query, examples)
  → schema + query + few-shot examples[:3]
```

## 5. Few-shot 检索（V3.0 新增）

```
用户提问 → TF-IDF 向量化 → 检索相似历史(top-3) → 注入 prompt → LLM 生成 SQL
                                     ↓
                             SQL 执行成功 → 存入历史库
```

- 进程内历史库，LRU 上限 500 条
- 分词：英文单词 + 中文逐字
- 相似度：TF-IDF + 余弦相似度
- 阈值：相似度 ≥ 0.1 才注入示例

## 6. Report Synthesizer（V3.0 新增）

### 6.1 模板化汇总（默认）

```python
class ReportSynthesizer(BaseAgent):
    name = "report_synthesizer"

    async def run(self, state, emit) -> AgentResult:
        results = state.results
        if "match_agent" in results:
            report = self._match_report(results)   # 匹配报告模板
        elif "data_agent" in results:
            report = self._data_report(results)    # 数据报告模板
        # 可选 LLM 润色
        if await get_setting_safe("report_llm_enabled") == "true":
            report = await self._llm_polish(report, results)
```

### 6.2 报告结构

匹配报告：

```json
{
  "summary": "匹配分析报告",
  "score": 85,
  "dimensions": {
    "skill": {"score": 90, "label": "技能匹配"},
    "project": {"score": 80, "label": "项目经验"},
    "experience": {"score": 75, "label": "工作年限"},
    "education": {"score": 85, "label": "教育背景"},
    "engineering": {"score": 80, "label": "工程能力"}
  },
  "skill_gap": ["Kubernetes", "Terraform"],
  "strengths": ["Python", "FastAPI", "DuckDB"]
}
```

## 7. 动态复杂度规划（V3.0 新增）

```python
def assess_complexity(query: str) -> str:
    # COMPLEX: 对比/嵌套/多表/join/环比/同比/占比/分布/趋势/排名变化
    # SIMPLE:  短问题(<20字) + 聚合词(总计/平均/最多/最少...) + 无复杂词
    # NORMAL:  默认
```

| 级别 | 链路 | 说明 |
|------|------|------|
| SIMPLE | data_agent | 跳过 validator，快速出结果 |
| NORMAL | data → validator | 默认链路 |
| COMPLEX | data → validator → report | 增强校验 + 报告汇总 |
| 匹配链路 | resume∥job → match → validator → report | 含简历上下文时优先 |

## 8. 配置体系（设置中心）

- **来源优先级**：MySQL `model_configs`(激活行) → `.env` → MockLLM。模型热切换无需重启（10s TTL 缓存）。
- **功能开关**（`app_settings` 表）：`match_llm_enabled` / `report_llm_enabled` / `llm_fallback_mock(auto|never)` / `parser_backend(mineru_api|pymupdf)` / `sql_timeout` / `mineru_api_token` / `tavily_api_key`。
- **密钥安全**：Fernet 加密落库，回显脱敏 `***尾4位`；密钥 APP_SECRET 自动生成于 .env（git 忽略）。

## 9. 健壮性清单

TaskBus 事件上限 500（drop-oldest）+ 完结任务 30min TTL 清扫 + Task 引用防 GC ｜ 上传 ≤10MB 413 ｜ DuckDB memory_limit 1GB / threads 4 / 查询 30s 强制超时 ｜ MySQL 连接超时+瞬时重试 ｜ LLM 指数退避 ｜ 全局异常处理 + 请求日志 ｜ SQL Guard（只读白名单/黑名单 token/LIMIT 规范化）｜ 幂等锁防重复提交（Redis SET NX EX 300）｜ 限流（auth 5/min·IP+用户名；task 30/min·用户）。

## 10. 扩展点

1. **加 Agent**：继承 BaseAgent 实现 run() + `_register_agents()` 注册 + `planner.py` 加模板步骤——并行/重试/消息/落库全自动获得。
2. **换 LLM**：设置中心 UI 直接加配置激活（零代码零重启）；非 OpenAI 协议才需要写 BaseLLMClient 子类。
3. **改执行计划**：`planner.py` 的模板即 DAG 声明；动态 LLM 规划的接入点在 `Supervisor.route()` 复杂度分支处。
4. **加压缩策略**：`context/compressor.py` 加新 compress_* 函数，`context/builder.py` 调用。
5. **框架决策**：不引入 LangGraph/DeepAgents——链路是确定性 DAG，自研 executor 仅数百行且 trace/消息协议可讲原理；对照表见 README。

## 11. 企业级工程化

- **日志**：`app/core/logging.py` JSON 行格式（contextvars 注入 request_id/task_id）；访问日志在 `main.py` 中间件
- **探针**：`/healthz`（存活）、`/readyz`（就绪，依赖 MySQL；Redis 降级不阻塞）、`/metrics`（进程内指标）
- **限流**：`app/core/rate_limit.py` 滑动窗口（auth 5/min·IP+用户名；task 30/min·用户）
- **迁移**：`app/persistence/migrations.py` 版本化迁移，lifespan 启动自动应用
- **依赖**：requirements.txt 全量精确锁版

## 12. 测试与复现

```bash
cd backend && python -m pytest tests/unit -q   # 77+ 全绿，离线
python scripts/api_bench.py --n 10             # 接口延迟分层实测（L1~L4）
python -m app.evaluation.runner                # 评测 100 case
```
