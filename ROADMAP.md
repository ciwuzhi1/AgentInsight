# ROADMAP — 从 Demo 到实用阶段（2026-08-30 制定）

> 定位转变：目前项目是**功能完整的演示级**（57 单测、两链路实测、可复现 benchmark），但离"真实可用"有七个明确缺口。本表是唯一路线图，每阶段有可验收的完成标准，做完一项勾一项。

## 一、现状审查结论（为什么还不是"实用级"）

| # | 缺口 | 现状 | 影响 |
|---|---|---|---|
| G1 | **Redis 名存实亡** | 仅健康检查；设计文档核心卖点 Cache-Aside/任务锁/幂等（链路C）完全未实现 | 简历/JD 重复解析每次重跑，面试讲不出缓存实测数据 |
| G2 | **LLM 仍是 mock** | 设置中心已支持热切换但未配真实 key；NL2SQL 真实正确率未知 | 数据分析链路本质还是规则匹配，不是智能 |
| G3 | **评测体系空壳** | `app/evaluation/` 占位 README；0 case | 项目最大差异化（分析文档反复强调）缺失 |
| G4 | **无用户体系** | 无 auth/users 表，所有数据全局共享 | 第二个人一用就串数据，谈不上实用 |
| G5 | **无部署方案** | 手动 uvicorn + npm dev；无 backend/frontend 镜像 | 换台机器跑不起来 |
| G6 | **无历史与导出** | 任务都在 MySQL 但前端看不到历史；结果不能导出 | 用完即弃，无沉淀 |
| G7 | **数据源演示级** | 爬虫只抓 fake-jobs 演示站；MinerU 客户端未用真实 token 验证；Spark 镜像未构建 | 输入是假的，输出自然只是演示 |

## 二、计划表（推荐顺序 P1→P2→P6→P3→P4→P5→P7）

| 阶段 | 内容 | 关键交付 | 验收标准（可演示/可测） | 工期* |
|---|---|---|---|---|
| **P1 真实智能** | 设置中心接入真实 LLM key（DeepSeek/GLM）；NL2SQL prompt 迭代（schema 注入已有，补 few-shot 与错误反馈重试）；MinerU 配置 token 跑通真 PDF；爬虫支持**用户粘贴任意 JD 文本**（不强依赖演示站） | 真 LLM 生成的 SQL/解读/简历画像 | 同一数据集 10 个真实问题，≥8 个生成可执行且语义正确的 SQL（人工判定）；真 PDF 简历解析出结构化画像 | 2~3 天 |
| **P2 Redis 实用化（链路C）** | Cache-Aside：JD/简历解析结果按内容 hash 缓存（TTL 24h/1h）；任务幂等锁（SET NX）；缓存命中 SSE 事件 + 前端 HIT 徽标；**命中前后延迟实测** | `cache/keys.py` 语义化键 + 三处消费点 + README 链路C 数据表 | 同一 JD 二次分析：时间线显示 `Redis HIT`，LLM 调用 0 次，延迟对比实测入 README；关掉 Redis 全链路仍通（降级） | 2~3 天 |
| **P6 部署与体验**（提前做，实用门槛） | backend/frontend Dockerfile + compose 一键全栈（含本机 MySQL 说明）；**任务历史页**（列表+回放时间线+结果）；**结果导出 CSV/JSON**；`make dev/up/test` | `docker compose up` 一键起全栈 | 全新目录 clone→compose up→浏览器可用；历史页能回放任意旧任务时间线 | 2~3 天 |
| **P3 评测体系** | `evaluation/`：100 case（NL2SQL 60 + 匹配 20 + 路由 10 + 异常 10）；runner/metrics（SQL 成功率/JSON 合法率/路由准确率/延迟/token）；回归命令进 README | `python -m app.evaluation.runner` 一键评测报告 | 指标表（真实数字）进 README；prompt 改动后回归可对比 | 3~4 天 |
| **P4 用户与安全** | JWT 登录注册 + users 表；数据集/简历/任务按 user 隔离；CORS 收紧；上传目录隔离 | auth 中间件 + 前端登录页 | 两个用户互相看不到对方数据；未带 token 访问 API 401 | 2~3 天 |
| **P5 Spark 链路D** | 构建本地 Spark 镜像（命令已留档）→ 链路D 实跑 → DuckDB vs Spark 同数据 benchmark | README 对比表（实测） | 20 万行 JD 技能统计双引擎耗时/一致性入表；时间线显示 engine=spark | 1 天 |
| **P7 线上发布** | 轻量云主机或内网穿透部署公网 demo；README 四个 Demo GIF；（可选）GitHub Actions CI（pytest+bench 冒烟） | 可访问的线上地址 + CI 绿标 | 陌生人打开链接即可完整走通两条链路 | 1~2 天 |

\* 单人投入估算。总计 **13~19 个工作日**（约 2~3 周课余节奏）。

## 三、依赖关系与原则

- P1 独立；P2 依赖 P1（缓存的是真 LLM 结果才有意义）；P3 强依赖 P1/P2（评测对象是真智能+真缓存）；P4/P5/P6 相互独立可穿插。
- 每阶段结束：README 补实测数据 → `pytest + api_bench` 回归 → commit → push。
- **不做的事**（保持克制）：Kafka/RabbitMQ/向量库/K8s（设计文档"明确不加入"清单继续有效）；LangGraph/DeepAgents（决策对照表见 README，开放式调研 Agent 留作未来适配器试点）。

## 四、进度看板

- [x] 阶段0 环境与骨架（2026-08-30）
- [x] 阶段1 链路A MVP + 爬虫 + 前端（commit 9957c2a）
- [x] 阶段2 链路B 多智能体 + 设置中心 + 健壮性 + 57 单测（commit d19c913）
- [x] 文档对齐：ARCHITECTURE 重写 / ROADMAP 建立（a1054d5）
- [x] P1 真实智能（核心）：GLM-4.5-air 经设置中心接入激活，真实 LLM 冒烟与全量评测完成
- [x] P2 Redis 链路C：Cache-Aside（实测 HIT 提速 2.4x，HIT 0 次 LLM）+ 任务幂等锁(409 接管) + HIT 徽标
- [x] P3 评测体系：100 case（60 nl2sql/20 match/10 routing/10 error）+ runner CLI + 真实 GLM 基线
- [x] P4 后端：JWT 注册/登录 + 数据隔离（实测 404/401）；登录前端页待做
- [x] P4 用户与安全：JWT 注册/登录 + 数据隔离 + **登录前端页**（/login + 全局 authFetch + SSE query-token，commit 400fb8b/d343937）
- [x] P6 部署与体验（feature/optimization 分支）：docker/backend.Dockerfile + frontend.Dockerfile + compose 一键全栈 + Makefile；任务历史面板（MySQL 持久，回放 trace + CSV/JSON 导出）；三项运行时优化（SSE 15s 心跳+前端断线重连、MySQL 连接池化、DuckDB 超时 conn.interrupt() 硬中断）
- [ ] P5 Spark 链路D
- [ ] P7 线上发布
