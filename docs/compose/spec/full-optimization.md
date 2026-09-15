---
feature: full-optimization
status: delivered
updated: 2026-03-11
branch: feature/full-optimization
commits: 
---

# Full Optimization Pass

## Report

**What was built** — 完成 18 项全量优化：修复 2 个紧急 Bug（auth register NameError、failed_final 误标），加固安全（Settings/Models/Eval API 鉴权、CORS 空值拒绝启动），优化连接与缓存（Redis 单例、DuckDB LRU、LLM Client 缓存、NL2SQL 缓存+错误反馈重试），提升健壮性（APP_SECRET fail hard、阻塞调用 async 化、启动清理残留锁），优化前端（ECharts 按需引入、SSE 指数退避重连、useMemo），并完成代码卫生（重复 import、共享 helper 提取）。

**Verification** — 后端 77 单测全绿；前端 TypeScript 检查通过（exit code 0）。

**Journey log** —
1. Worktree 创建被系统拦截，改用分支隔离
2. Redis 单例采用 asyncio.Lock + 冷却期模式，避免并发创建
3. failed_final 误标需同时改 executor 事件语义和 API 层持久化逻辑
4. ECharts 按需引入需同时注册 charts + components + renderer
5. 共享 helper（safe_emit/get_setting_safe）提取到 agents/base.py 减少重复

## [S1] Problem
AgentInsight 当前存在 2 个紧急 Bug（注册接口 NameError、可重试错误误标 failed_final）、多处性能热点（Redis 每次新建连接、DuckDB 连接泄漏、LLM Client 不复用、NL2SQL 无缓存无重试）、安全隐患（Settings/Models API 无鉴权、CORS 通配符回退）、健壮性缺口（阻塞 MySQL 调用、重启状态丢失），以及前端可维护性问题（ECharts 全量引入、SSE 只重连一次）。这些共同阻碍项目从演示级进入实用级。

## [S2] Design

### 2.1 紧急 Bug 修复
- **auth register NameError**：`username` 变量在使用前赋值。
- **failed_final 误标**：executor 重试过程中的 error 事件不应写 `failed_final` 到 MySQL，不应标记 TaskBus finished。区分 `retrying_error` 与 `terminal_error`。

### 2.2 安全加固
- Settings/Models/Evaluation API 添加 `Depends(get_current_user)`。
- CORS 空值时生产环境拒绝启动（不再回退 `["*"]`）。

### 2.3 连接与缓存优化
- **Redis 单例**：模块级懒初始化客户端，断连后自动重建（冷却期 5s）。
- **DuckDB LRU**：最多保留 10 个连接，淘汰最久未用的，lifespan shutdown 全关。
- **LLM Client 缓存**：按 `(base_url, model)` 缓存 `OpenAICompatibleClient`，TTL 与配置缓存一致。
- **NL2SQL 缓存**：`cache:nl2sql:{hash(dataset_id, schema, query)}`，TTL 1h。
- **NL2SQL 错误反馈重试**：SQL 执行失败时把 error + 原 SQL + schema 喂回 LLM，最多重试 2 次。

### 2.4 健壮性
- 阻塞 MySQL 调用（`get_setting`、`get_active_model_config`）包 `asyncio.to_thread`。
- 启动时清理 Redis 中的 `agent:lock:*` 键。
- `APP_SECRET` 读取失败时 fail hard（不再生成临时密钥）。

### 2.5 前端优化
- ECharts 按需引入（`echarts/core` + BarChart/LineChart/GridComponent/TooltipComponent/LegendComponent/CanvasRenderer）。
- SSE 断线指数退避重连（最多 3 次）。
- `buildTimeline` 用 `useMemo`。
- 简历缓存 key 直接 hash bytes（跳过 latin-1 decode）。

### 2.6 代码卫生
- 删除重复 import（auth.py、main.py）。
- 提取 `_safe_emit`、`_get_setting` 到 `agents/base.py`。

## [S3] Out of Scope
- 不引入 LangGraph/LangChain
- 不做向量库/RAG
- 不做 K8s/微服务拆分
- 不做公网部署（P7）
- 不改数据模型/表结构
- 前端大重构（拆组件）因改动面过大暂缓，ECharts/SSE/useMemo 已完成

## Tasks
- [x] T1: 修复 auth register NameError — acceptance: POST /api/auth/register 不再返回 500 (covers: S2.1)
- [x] T2: 修复 failed_final 误标 — acceptance: executor 重试成功后 MySQL 状态为 completed (covers: S2.1; depends: T1)
- [x] T3: Settings/Models/Evaluation API 添加鉴权 — acceptance: 不带 token 返回 401 (covers: S2.2)
- [x] T4: CORS 生产环境空值拒绝启动 — acceptance: CORS_ORIGINS 为空时启动报错 (covers: S2.2)
- [x] T5: Redis 单例连接 — acceptance: 连续调用复用同一连接 (covers: S2.3)
- [x] T6: DuckDB LRU 淘汰 — acceptance: 超过 10 个连接时淘汰最久未用 (covers: S2.3)
- [x] T7: LLM Client 实例缓存 — acceptance: 相同配置复用同一实例 (covers: S2.3)
- [x] T8: NL2SQL 结果缓存 — acceptance: 相同问题第二次不调 LLM (covers: S2.3)
- [x] T9: NL2SQL 错误反馈重试 — acceptance: SQL 错误时自动重试修正 (covers: S2.3; depends: T8)
- [x] T10: 阻塞 MySQL 调用包 to_thread — acceptance: get_setting_async 不阻塞事件循环 (covers: S2.4)
- [x] T11: 启动清理 Redis 锁 — acceptance: 重启后可立即重新提交 (covers: S2.4)
- [x] T12: APP_SECRET fail hard — acceptance: 缺失时抛异常而非临时密钥 (covers: S2.4)
- [x] T13: ECharts 按需引入 — acceptance: TypeScript 检查通过 (covers: S2.5)
- [x] T14: SSE 指数退避重连 — acceptance: 断线最多重连 3 次 (covers: S2.5)
- [x] T15: useMemo 优化 — acceptance: buildTimeline 不重复计算 (covers: S2.5)
- [x] T16: 简历缓存 key 优化 — acceptance: bytes_hash 替代 latin-1 decode (covers: S2.5)
- [x] T17: 代码卫生 — acceptance: 重复 import 已清理，共享 helper 已提取 (covers: S2.6)
- [x] T18: 全量测试回归 — acceptance: pytest 77 全绿 + tsc 通过 (covers: S2)
