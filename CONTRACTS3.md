# CONTRACTS3 — P2 Redis 链路C / P3 评测体系 / P4 用户与安全 增量契约

> **以代码为准**：本文档与后端实现不一致时，以代码为准（鉴权范围以各 `app/api/*.py` 的 `Depends(get_current_user)` 为准）；文档仅作约定与导读。
> 基础仍是 CONTRACTS.md + CONTRACTS2.md，冲突以本文件为准；与实现冲突时以代码为准。
> 波次与归属：Wave1 = A-P2(Redis后端) ‖ A-P3(评测)；Wave2 = A-P4(auth后端) ‖ A-P2F(前端HIT徽标)；Wave3 = A-P4F(登录页+token封装)。**同波禁改对方文件**；跨波可接力但必须保留前一波行为。

## 1. P2 — Redis 链路C（A-P2 专属 Wave1）

允许文件：`app/cache/redis.py`、`app/cache/keys.py`(新)、`app/cache/policies.py`(新)、`app/agents/{resume_agent,job_agent}.py`、`app/api/agent.py`。

### 1.1 cache/keys.py
```python
def text_hash(*parts: str) -> str        # sha256 前 16 位（parts 用 \x1f 连接后 hash）
def resume_key(h: str) -> str            # f"cache:resume:{h}"
def job_key(h: str) -> str               # f"cache:job:{h}"
```
### 1.2 cache/policies.py
```python
TTL_RESUME = 24*3600; TTL_JOB = 24*3600; TTL_SCHEMA = 3600   # 常量 + get_ttl(kind) 
```
### 1.3 cache/redis.py 增量
`get_json(key) -> dict|None`、`set_json(key, obj, ttl)`（内部序列化 json；Redis 不可用/任何异常一律静默返回 None/False + warning，绝不抛出——降级原则）。

### 1.4 Agent 侧 Cache-Aside（resume_agent / job_agent）
- 计算内容 hash：resume = text_hash(纯文本全文)；job = text_hash(所有 JD 的 title+description 拼接)。
- 流程：emit `{"type":"cache","hit":bool,"key":...}` → HIT 直接用缓存 profile（message_type 不变，data 加 `"cache":"hit"`）；MISS → 原 LLM/解析路径 → `set_json(key, profile, TTL)` → data 加 `"cache":"miss"`。
- 阻塞与容错：Redis 操作全 try/except；缓存坏数据(json 解析失败)按 MISS 处理。
- **缓存必须在 LLM/解析之前**——HIT 时 0 次 LLM 调用、0 次文件解析（这是链路C 的核心叙事）。

### 1.5 任务幂等锁（api/agent.py）
- POST /api/tasks 与 /api/matches：key = f"agent:lock:{text_hash(str(sorted(body.items())))}"，`SET NX EX 300`。
- 拿到锁 → 正常创建；未拿到（重复提交执行中任务）→ 返回 `{"detail":"相同任务正在执行中","task_id":<已存在>,"status":409}`？——**定案：返回 409 + {"detail":"...","task_id":existing_task_id}**，前端识别 409 且带 task_id 时直接订阅既有任务 SSE 并提示"已接管进行中任务"。
- 锁在任务终结（completed/failed_final）时 DEL；异常路径 finally DEL。Redis 不可用 → 跳过锁照常创建（降级）。

### 1.6 SSE 事件
`{"type":"cache","hit":true,"key":"cache:job:9f2c..."}` 随事件流正常走 TaskBus（无需新落库逻辑；agent_end detail 里已有）。

### 1.7 验收（A-P2 自测）
- Redis 运行时：同一 resume_id 的 match 任务连跑两次，第二次事件流含 `{"type":"cache","hit":true}` 且 agent_end latency 显著低于首次（自测打印对比）。
- `docker stop agentinsight-redis` 后全链路仍通（无 cache 事件或 hit=false）→ `docker start` 恢复。
- 重复提交同一 match：第二次 409。
- pytest 全绿（缓存路径用 fakeredis 式 monkeypatch 或直接起真容器——**直接用真容器测**，本机有）。

## 2. P3 — 评测体系（A-P3 专属 Wave1）

允许文件：`app/evaluation/{__init__,cases,metrics,runner}.py`、`app/api/evaluation.py`(新)、`app/main.py`(仅 include evaluation router)、`backend/tests/unit/test_evaluation.py`(新)。

### 2.1 cases.py —— 100 case，程序化生成但完全确定（可复查）
```python
@dataclass
class Case:
    case_id: str; kind: str        # nl2sql | match | routing | error
    payload: dict                  # nl2sql: {question, dataset:"demo_sales", expect:{tables,columns,aggs,order_by,limit_max}}
                                   # match:  {resume:{skills,education,experience_years,projects}, jobs:[...], expect:{score_min,score_max,gap_contains:[]}}
                                   # routing:{query, context:{"resume"| "dataset_id"|None}, expect_agent:"match_agent"|"data_agent"|None(clarify)}
                                   # error:  {query, dataset_id:"nonexistent"|"", expect:"graceful"}   # 任务失败但有明确 error 且不崩
def load_cases() -> list[Case]     # 60 nl2sql + 20 match + 10 routing + 10 error = 100，顺序固定
```
- nl2sql 60 条：模板组合（按{地区/城市/品类/产品/月份} × {总销售额/平均/数量/Top N/分布} + 组合条件若干），expect 写**结构性断言**（SQL 包含哪些列名/聚合函数/GROUP BY 列/LIMIT ≤N），不写全文匹配。
- match 20 条：手工设计技能重叠度从 0%→100% 的 20 组，expect 分数区间与缺口。
- routing 10 条：6 条数据问题→data_agent、3 条带 resume 上下文求职问题→match_agent、1 条无上下文→clarify。
- error 10 条：DROP/DELETE 提问、不存在 dataset_id、空 query、超长 query 等，expect 全部为 graceful（任务终态 failed_final 且 error 非空，或 4xx，不 500）。

### 2.2 metrics.py
```python
def sql_metrics(rows) -> dict   # sql_exec_ok_rate, sql_feature_pass_rate, json_validity, avg_latency_ms, p95_latency_ms
def match_metrics(rows) -> dict # score_in_range_rate, gap_accuracy_rate, avg_latency_ms
def routing_metrics(rows) -> dict  # routing_accuracy
def error_metrics(rows) -> dict    # graceful_rate
def summarize(all_rows) -> dict    # 汇总 + total/failed
```
### 2.3 runner.py —— `python -m app.evaluation.runner [--limit N] [--kind nl2sql] [--out data/eval_report.json]`
- **进程内直跑，不经过 HTTP、不写 MySQL**：nl2sql → get_llm_client().generate_json → sql_tool.guard → duckdb_engine.execute（真实 demo_sales.csv，脚本内注册视图 ds_eval0001）；match → 直接构造 AgentMessage/TaskState 调 match_agent.run；routing → Supervisor().route()；error → 构造 TaskState 走 supervisor.run_task（emit 收集事件，断言终态与 error）。
- 每 case 记录 {case_id, ok, latency_ms, detail}，失败打印 case_id+原因摘要。
- 输出：终端 Markdown 表 + `data/eval_report.json`（含全部明细）。
- **评测过程不得污染业务库**（不落 MySQL）；评测产生的 DuckDB 视图用专用 dataset_id 前缀 `eval`。

### 2.4 api/evaluation.py
`GET /api/evaluations/last` → 读 `data/eval_report.json`（无文件 404）。main.py include。

### 2.5 验收
- runner 跑完 100 case 无崩溃，输出汇总表；mock 模式下 sql_feature_pass_rate 的真实数字记录在报告（主线程填 README）。
- test_evaluation.py ≥4 例：load_cases 数量与去重、routing 3 类判定、error case graceful、metrics 计算正确。

## 3. P4 — 用户与安全（A-P4 后端 Wave2；A-P4F 前端 Wave3）

### 3.1 schema.sql 追加（A-P4）
```sql
users(id VARCHAR(36) PRIMARY KEY, username VARCHAR(64) NOT NULL UNIQUE, password_hash VARCHAR(255) NOT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)
-- datasets / resumes / tasks / matches 各加 user_id VARCHAR(36) NULL + KEY idx_<t>_user(user_id)
```
**存量数据约定**：user_id 为 NULL 的旧数据视为"公共遗留"，任何已登录用户可见（新写入必带 user_id，两用户隔离验收针对新数据）。

### 3.2 core/security.py（A-P4）
`hash_password(p)->str`（PBKDF2-HMAC-SHA256，100k 迭代，盐随 hash 存储 `salt$hash` 格式）、`verify_password(p, stored)->bool`、`create_token(user_id, username)->str`（PyJWT HS256，secret=APP_SECRET，exp 7d）、`decode_token(token)->{user_id,username}`（过期/篡改抛 ValueError）。

### 3.3 api/auth.py（A-P4）
`POST /api/auth/register {username,password}`（用户名 3~32 字符唯一，密码 ≥6；重名 409）→ 201 {user_id,username}；`POST /api/auth/login` → {token,user_id,username}（401 用户名或密码错误）；`GET /api/auth/me`（Bearer）。
**依赖 `get_current_user`**（fastapi Depends）：解析 Authorization: Bearer → decode_token → UserCtx{user_id,username}；缺失/无效 → 401。**应用范围（以代码 `Depends(get_current_user)` 为准）**：POST/GET /api/datasets、/api/resumes、/api/matches、/api/tasks（含 SSE/详情）以及 **/api/settings、/api/models、/api/evaluations/*** **必须登录**；仅 /api/auth/*、/api/health*、/api/crawler/* 保持开放。（本轮规划原写 settings/models/evaluations 开放，实现已收紧，以代码为准。）

### 3.4 数据隔离（A-P4）
- 写路径：insert_dataset/save_resume/insert_task/insert_match 增加 user_id 参数并落库。
- 读路径：get_dataset/get_resume/task 详情与 SSE 订阅——校验 row.user_id ∈ {NULL, 当前用户}，否则 404（不泄露存在性）。
- api_bench 脚本（scripts/api_bench.py，A-P4 一并改）：开头注册+登录拿 token，所有受影响请求带 Authorization 头；健康检查等开放接口不变。
- CORS 收紧：config.py 加 `CORS_ORIGINS: str = "http://localhost:3100,http://127.0.0.1:3100"`，main.py split 后传入（保留全局异常/中间件/sweeper）。
- **保留 A-P2 的幂等锁与 cache 事件**（agent.py 跨波接力时逐行保真）。

### 3.5 验收（A-P4 自测）
- TestClient：注册→登录→带 token 上传数据集→另一账号 GET 该 dataset 404；无 token POST /api/datasets 401；旧公共数据（user_id=NULL）两账号都可见。
- pytest 全绿；api_bench --n 2 跑通。

## 4. 前端

### 4.1 A-P2F（Wave2，只许改 frontend/app/page.tsx）
- 时间线渲染 `{"type":"cache"}` 事件：hit=true 天蓝徽标「⚡ Redis HIT」+ key 尾 4 位；hit=false 灰徽标「cache MISS」。
- 409 重复提交处理：matches/tasks 提交 catch 409 且 body 带 task_id → 提示条「已接管进行中任务」并直接订阅该 task 的 SSE（复用现有 subscribe）。

### 4.2 A-P4F（Wave3，只许改 frontend/app/login/page.tsx(新)、page.tsx、settings/page.tsx、frontend/README.md）
- 新建 /login：登录/注册双表单（深色风格统一），成功后 localStorage 存 {token,username}，跳转 /。
- page.tsx 与 settings/page.tsx：所有 fetch 统一加 `Authorization: Bearer <token>`（抽本地 helper `authFetch`，两文件各自实现副本即可）；401/403 → 清 token 跳 /login；主页导航右侧显示用户名 + 「退出」（清 token 回 /login）。
- README 增补"登录与数据隔离"两段说明。

## 5. 通用

- 全部阻塞调用 to_thread；Redis/DB 任何故障不得 500（降级/4xx）。
- 报告格式：文件清单 + 自测结果（含实测数字）+ 契约偏差。
