# CONTRACTS2 — 阶段2 增量契约（在 CONTRACTS.md 基础上追加，冲突时以本文件为准）

> **以代码为准**：本文档与后端实现不一致时，以代码为准（如 `backend/app/agent_runtime/executor.py`、`agents/match_agent.py` 等）；文档仅作约定与导读。
>
> 波次与文件归属：Wave1 = A1(runtime)+A2(健壮性)；Wave2 = A3(业务Agent)+A4(设置中心/持久化)；Wave3 = A5(前端)+A6(测试)。**同波内禁止改对方文件**；跨波可接力（Wave2 可改 Wave1 产出的 llm.py/main.py）。

## 1. 计划结构与执行（A1 专属：`agent_runtime/{planner,executor,state,supervisor}.py`）

### 1.1 planner.py
```python
@dataclass
class PlanStep:
    id: str                      # 步骤名，如 "resume"/"job"/"match"/"data"/"validator"
    agent: str                   # agent 注册名
    depends_on: list[str]        # 依赖的 step id
    params: dict                 # 可选；params.get("optional")=True 的步骤失败可被 replan 跳过

class PlanError(Exception): ...

def build_match_plan() -> list[PlanStep]
    # [resume(无依赖), job(无依赖), match(depends_on=[resume,job], optional=False), validator(depends_on=[match])]
def build_data_plan() -> list[PlanStep]
    # [data(无依赖), validator(depends_on=[data])]
def validate_dag(steps, max_steps: int) -> list[PlanStep]   # 拓扑排序校验：无环/依赖存在/步数≤max_steps，非法抛 PlanError
```

### 1.2 state.py 增量（A1）
- `TaskState` 新增字段：`plan_steps: list`（默认 []）、`messages: list`（默认 []，元素为 AgentMessage）。
- `plan` 属性语义不变（名字列表，可由 plan_steps 派生或直接填名）。

### 1.3 executor.py（A1）
```python
class WorkflowExecutor:
    async def execute(self, state: TaskState, emit: EmitFn) -> TaskState
```
- 拓扑波次：`depends_on` 全满足的步骤组成一波，`asyncio.gather` 并行；下一波等上一波全部完成。
- 单步执行含重试：指数退避 0.5s→1s→2s（cap 4s），上限 `settings.MAX_RETRY`；每次重试 emit `{"type":"retry","step":step_id,"agent":...,"retry_count":n}`。
- 步骤失败且重试耗尽：若 `params.optional` → emit warning 事件 `{"type":"step_skipped","step":...}` 并继续；否则整个任务 failed_final。
- **AgentMessage 实用化**：每个成功的 AgentResult 经 `make_message(sender=agent.name, receiver=下游 agent 名列表, type=result.message_type, payload=result.data)` 追加到 `state.messages`，并把 message_id/type 放进 agent_end 事件的 `detail` 字段。
- 终态组装 `_finish`：若 `state.results` 含 `match_agent` → final_result 用匹配形态（§3.6）；否则数据形态（沿用 CONTRACTS.md §6，从 results["data_agent"]["final"] 取）。
- `settings.MAX_AGENT_STEPS` 在 validate_dag 中强制（首次启用）。

### 1.4 supervisor.py 升级（A1）
- `route()` 返回 `list[PlanStep]`：state.context 含 `resume` 且（含求职关键词或 query 为空）→ `build_match_plan()`；无 dataset 且无 resume → clarify（现状）；否则 `build_data_plan()`。
- `run_task()` 改为：emit `{"type":"state","status":"routing"}` → route → emit **`{"type":"plan","steps":[{id,agent,depends_on},...]}`** → transition(running) → 委托 `WorkflowExecutor().execute(state, emit)` → validator 已在 plan 内。
- 兼容：数据链路行为与事件序列同现状（老前端/测试可回归）。

## 2. SSE 事件增量（A1 发、A2 落库、A5 消费）

```json
{"type":"plan","steps":[{"id":"resume","agent":"resume_agent","depends_on":[]},{"id":"job","agent":"job_agent","depends_on":[]},{"id":"match","agent":"match_agent","depends_on":["resume","job"]}]}
{"type":"agent_start","agent":"resume_agent","step":"resume"}     // step 现在是 step id（字符串）
{"type":"agent_end","agent":"resume_agent","step":"resume","latency_ms":812,"status":"ok","detail":{"message_id":"...","message_type":"resume_profile"}}
{"type":"step_skipped","step":"job","reason":"..."}               // replan 跳过
{"type":"retry","step":"resume","agent":"resume_agent","retry_count":1}
```

### 2.1 匹配形态 final_result（A1 组装，前端 A5 依赖字段名）
```json
{"task_id":"...","query":"...","engine":"multi_agent","elapsed_ms":1234,
 "resume":{"resume_id":"...","filename":"...","skills":["Python"],"experience_years":3,"education":"本科"},
 "jobs":[{"id":10,"title":"...","company":"..."}],
 "score":82,                       // 0~100 整数
 "dimensions":{"skill":78,"project":70,"experience":80,"education":100,"engineering":60},
 "skill_gap":["Docker","Kubernetes"],
 "interpretation":"...",           // LLM 解读或模板文案
 "interpretation_source":"llm|mock",
 "experience_years":3}             // 顶层冗余；与 resume.experience_years 同源（profile），便于直接读取
```

## 3. 三个业务 Agent（A3 专属：`agents/{resume_agent,job_agent,match_agent}.py`、`tools/mineru_client.py`；validator_agent 也在 A3）

### 3.1 ResumeAgent（name="resume_agent"）
- 输入：`state.context["resume"] = {"resume_id","path","filename"}`（API 层放入）。
- 解析链（阻塞全 to_thread）：PDF → `mineru_client.parse_pdf(path)`（settings/app_settings 有 mineru_api_token 时）→ 失败/未配置 → `pymupdf` 文本层（`import pymupdf`；fitz 别名勿用）；DOCX → python-docx 遍历段落；TXT 直读。统一产出纯文本。
- LLM 结构化 → `{"skills":[],"projects":[],"education":"","experience_years":0,"highlights":[]}`；mock 兜底：技能=SKILL_KEYWORDS 匹配 + 正则抓学历/年限。
- 成功后 `mysql.save_resume(resume_id, filename, path, profile)`（A4 提供）；AgentResult.data = `{"resume_id","filename","profile",...}`，message_type="resume_profile"。
- 解析失败 → AgentResult error（必经步骤，失败即任务失败）。

### 3.2 JobAgent（name="job_agent"）
- 输入：`state.context["jobs"] = [{"id","title","company","skills","description"},...]`（API 按 job_ids 从 jobs 表查好放入）或 `state.context["jd_text"]`。
- LLM 结构化 → `{"jobs":[{"id","title","must_have":[],"nice_to_have":[],"skills":[]}], "level":""}`；mock：直接用 skills 逗号串拆分 + description 关键词（复用 `app.crawler.parser.extract_skills`）。
- AgentResult.data = `{"jobs":[...]}`，message_type="job_profile"。

### 3.3 MatchAgent（name="match_agent"）
- **输入只从消息取**：`state.messages` 中 receiver 含 "match_agent" 的消息；resume_profile 找不到时兜底读 `state.results["resume_agent"]["data"]`。
- 规则打分（无 LLM）：技能归一（lower/trim + 同义词表：js↔javascript、k8s↔kubernetes、ml↔机器学习 等 ≥10 组）；分项与权重：skill 0.5（交集/必须项）、project 0.2（resume 项目数与相关性，mock 时按 profile.projects 是否命中 JD 关键词）、experience 0.1（JD 无经验要求给满分，有则线性）、education 0.1（本科=80 硕士=100 博士=100 大专=60 其他=50）、engineering 0.1（resume 技能含 Docker/K8s/Git/CI 类）；score=四舍五入 0~100。
- skill_gap = JD must_have/skills 中 resume 缺失的归一技能。
- **LLM 解读开关**：`app_settings.get_setting("match_llm_enabled","true")` 且 `get_llm_client()` 是真模型（非 Mock）→ 调用生成 ≤120 字中文解读；否则模板文案；异常吞掉回模板。`interpretation_source: "llm"|"mock"`。
- AgentResult.data = §2.1 中 score/dimensions/skill_gap/interpretation/interpretation_source + resume/jobs 摘要，message_type="match_result"。

### 3.4 validator_agent 升级（A3）
- 分支：results 含 match_agent → 校验匹配结构（score 0~100 int、dimensions 五键、skill_gap 为 list）；否则校验数据结构（现状）。

### 3.5 mineru_client.py（A3）
```python
async def parse_pdf(path: str, token: str, timeout_s: int = 180) -> str
class MineruError(Exception)
```
- MinerU v4 文件解析 REST API（base https://mineru.net）：提交任务→轮询→下载结果 zip→取 full.md 文本返回；任何一步失败抛 MineruError（stderr/状态摘要进异常信息）。调用方（resume_agent）捕获后走 pymupdf 兜底，不重试。token 为空直接抛 MineruError("未配置 token")。

## 4. 设置中心后端（A4 专属：`persistence/{schema.sql,mysql.py}`、`core/{crypto.py,app_settings.py,llm.py}`、`api/{models.py,settings.py,resumes.py,matches.py}`、`main.py`）

### 4.1 新表（追加进 schema.sql，init_db 幂等重跑）
```sql
resumes(id VARCHAR(36) PRIMARY KEY, filename VARCHAR(255), path VARCHAR(512), profile_json JSON, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)
matches(id BIGINT AUTO_INCREMENT PRIMARY KEY, task_id VARCHAR(36), resume_id VARCHAR(36), job_ids_json JSON, score INT, detail_json JSON, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, KEY idx_matches_task(task_id))
model_configs(id VARCHAR(36) PRIMARY KEY, name VARCHAR(64), provider VARCHAR(32), base_url VARCHAR(255), api_key_enc VARCHAR(1024), model VARCHAR(64), temperature FLOAT DEFAULT 0, is_active TINYINT(1) DEFAULT 0, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP)
app_settings(`key` VARCHAR(64) PRIMARY KEY, value TEXT, is_secret TINYINT(1) DEFAULT 0, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP)
```
内置 app_settings 行：match_llm_enabled=true、llm_fallback_mock=auto、parser_backend=mineru_api、sql_timeout=30、mineru_api_token=(secret,空)、tavily_api_key=(secret,空)。

### 4.2 mysql.py（A4 全权负责，含健壮性超时）
- `get_connection()` 加 `connect_timeout=5, read_timeout=15, write_timeout=15`；OperationalError 时单次重连重试。
- 新函数：`save_resume(id, filename, path, profile_dict)`、`get_resume(id)`、`insert_match(task_id, resume_id, job_ids, score, detail_dict)`、`get_jobs_by_ids(ids)->list[dict]`、model_configs CRUD：`list_model_configs()`、`get_active_model_config()`、`save_model_config(cfg)->id`、`activate_model_config(id)`、`delete_model_config(id)`；settings：`get_all_settings()`、`upsert_setting(key,value,is_secret=False)`。

### 4.3 crypto.py（A4）
- Fernet 封装：`encrypt_secret(s)->str`、`decrypt_secret(s)->str`；密钥 `APP_SECRET` 从 `.env`（config.settings）读，**为空时自动生成 `Fernet.generate_key()` 并追加写回 .env 文件**（幂等）。

### 4.4 app_settings.py（A4）
- `get_setting(key, default=None)->str`（进程内 TTL 缓存 10s；secret 值解密后返回）、`set_setting(key,value,is_secret=False)`、`get_all_masked()->dict`（secret 脱敏为 `***尾4位`）。DB 不可用时回退内置默认值并 warning（降级原则）。

### 4.5 llm.py 改造（A4，在 A2 Wave1 退避改造基础上接力）
- `get_llm_client()`：先查 `model_configs` active 行（模块级 TTL 缓存 10s）→ 有则按行构造 OpenAICompatibleClient（key 解密）；无 → 原逻辑（.env）；`llm_fallback_mock=never` 且无可用配置 → 抛 LLMError("未配置模型")；auto → Mock。MockLLMClient 增加类属性 `is_mock=True`（OpenAICompatibleClient 为 False）供 match_agent 判断。

### 4.6 API（A4）
- `POST /api/resumes`：multipart file ≤10MB（超限 413），后缀限 pdf/docx/txt，存 `{UPLOAD_DIR}/resumes/{uuid8}{ext}`，`save_resume`（profile 空占位），返回 `{"resume_id","filename"}`；`GET /api/resumes/{id}` 返回 profile（无 404）。
- `POST /api/matches`：body `{"resume_id","job_ids":[]}`；校验 resume 存在、`get_jobs_by_ids` 数量>0（否则 404/400）；创建 TaskState（context 放 `resume`/`jobs`/`query="简历与 N 个岗位匹配分析"`）→ 与 /api/tasks 同一 TaskBus/后台执行；返回 `{"task_id","status":"created"}`。
- `/api/models`：GET（列表，key 脱敏 `***尾4`）、POST（name/provider/base_url/api_key/model/temperature，加密存）、PUT `/{id}/activate`（唯一 active，事务内先清后设）、DELETE `/{id}`、POST `/{id}/test`（临时构造 client 发 1 token 请求，返回 `{"ok":true,"latency_ms":n}` 或 `{"ok":false,"error":...}`）。
- `/api/settings`：GET（脱敏全量）、PUT body `{"key","value"}`（白名单键校验）。
- `main.py` include 4 个新 router（与 A2 的 middleware 改动跨波接力，注意保留）。
- matches 落库：api/agent.py 的 `_persist_event` 在 final 事件时若 result 含 `score` → `insert_match(...)`（A2 Wave1 按 §5.1 预留钩子，A4 无需改）。

## 5. 健壮性包（A2 专属 Wave1：`api/agent.py`、`api/datasets.py`、`data_engine/duckdb_engine.py`、`core/llm.py`、`main.py`）

- **TaskBus**（api/agent.py）：每任务事件上限 500（drop-oldest，被淘汰事件仍在回放头部标注 `{"type":"truncated"}`）；完结任务（completed/failed_final）30min TTL：模块级 asyncio 后台任务每 10min 清扫一次（清理 events/cond/asyncio.Task 引用）；`_run` 的 create_task 句柄存进 Bus 防止 GC；final/error 时 `insert_match` 钩子（§4.6）。
- **上传限制**（api/datasets.py）：Content-Length 预检 + 分块读累计 ≤10MB，超限 413。
- **DuckDB**（duckdb_engine.py）：每连接建后执行 `SET memory_limit='1GB'; SET threads=4;`；execute 整体 `asyncio.wait_for(settings 驱动的 app_settings.sql_timeout 默认30s)`，超时抛 EngineError("查询超时")。
- **MySQL 超时**（mysql.py）：见 §4.2——**本项由 A4 在 Wave2 实现，A2 不动 mysql.py**。
- **LLM 退避**（core/llm.py）：generate_json 内层重试间隔 0.2s→0.8s 指数；timeout 从 `settings.LLM_TIMEOUT`（默认 60，config.py 加字段）读。
- **全局兜底**（main.py）：`@app.exception_handler(Exception)` 返回 `{"detail":"服务器内部错误"}` 500 + logger.exception；HTTP 中间件记录 method/path/耗时/状态码。

## 6. 前端（Wave3 A5 专属：`frontend/app/**`）

- 时间线：按 plan 事件的 steps 构建分组；agent_start/end 用 **step id** 配对（同 step 多 retry 保留最新）；并行步骤（depends_on 相同）渲染为两列卡片；retry/step_skipped 事件可见（retry 琥珀、skipped 灰）。
- 新区块：⑥ 简历上传（pdf/docx/txt，POST /api/resumes，显示已上传 resume_id）；⑦ JD 多选（GET /api/crawler/jobs?limit=50 勾选 + "开始匹配"→POST /api/matches→复用现有 SSE 流）；匹配结果卡（score 环形/分项条形/技能缺口标签/解读段+来源徽标）；⑧ 设置区块（模型列表+新增表单+激活/测试按钮；开关下拉/输入，PUT /api/settings）。
- API_BASE 逻辑不变；错误红条沿用。

## 7. 测试（Wave3 A6 专属：`backend/tests/**`）

新增用例（全部离线，不连 MySQL/Docker/网络）：
- test_planner：两个模板结构正确、validate_dag 环/缺依赖/超步数抛 PlanError；
- test_executor：DummyAgent 模拟——并行波次（记录并发序）、重试退避次数、optional 步骤被跳过、AgentMessage 追加进 state.messages；
- test_match：规则打分（同义词归一、缺口计算、分数区间）、mock 解读标注；
- test_taskbus：事件上限 500 截断、完结任务 TTL 清扫（注入假时钟或缩短 TTL）；
- test_resume_parse：TXT→正则 profile 兜底路径（不依赖 mineru/LLM）；
- 既有 36 例不回归。

## 8. 通用

- 所有阻塞调用（mineru httpx、pymupdf、docx、pymysql）一律 `asyncio.to_thread` 或原生 async；agent 内不发 final 事件（executor 统一发）。
- Wave2 起可改 Wave1 文件，但需保留其行为（A2 的 TaskBus/中间件是回归基线）。
- 报告格式：文件清单 + 契约偏差 + 遗留问题。
