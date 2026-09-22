---
feature: observability-crawler-ux
status: in-progress
updated: 2026-03-12
branch: main
commits: 
---

# 可观测 / 爬虫 / 一致性 / 体验优化

## Report

## [S1] Problem
1. 任务卡在 `routing` 时前端无明确错误；SSE 失败后用户不知任务是否仍在跑。
2. 爬虫单页外网超时导致整次 502，中途结果不入库。
3. 匹配 `experience_years` 在 profile 与 final_result 不一致。
4. 体验：视图无法深链、空状态弱、快捷词双份、任务运行态不明显、上传/设置反馈不足。

## [S2] Design
### 可观测
- 后端：LLM/步骤超时或终态失败时 `update_task` 写入明确 `error` 文案。
- 前端：SSE 断开/重连耗尽后，轮询 `GET /api/tasks/{id}`；若 `status=completed` 则拉 `trace` 填充 final_result；显示「后台仍在执行 / 已失败」提示。

### 爬虫
- 逐条 `upsert_job`（成功一条存一条）。
- 单个 detail 超时/失败：跳过并记录 `failed_urls`，不中断整次。
- `pages` 默认仍 1；响应增加 `max_items` 默认 **20**（可传）。
- run 返回 `{inserted, skipped, items, failed_urls, errors}`。

### 数据一致性
- 匹配 final_result 中 `experience_years` 与 `resume.profile.experience_years` 一致（以 profile 为准）。

### 体验与产品
- 视图 URL 同步：`?view=dataset|analysis|match|crawler`（可刷新/分享）。
- 分析页空数据集引导：明确 CTA 去「数据集」。
- 快捷词与 `JOB_PROMPT_PRESETS` 单一来源（Sidebar import）。
- 侧栏分析项在 `running` 时显示状态角标/文案。
- 上传区/设置页文案与错误提示更清晰（进度、失败原因）。
- 历史/导出 loading 状态（若文件改动范围内）。

### 其他优化想法
- 存放 `docs/optimize-backlog.md`（**gitignore，不入库**），记录未本轮做的项。

## [S3] Out of Scope
- 正式多用户登录体系
- SQLAlchemy 迁移
- LLM 提供商更换
- Redis 架构改造

## Tasks
- [ ] T1: 后端任务超时 error — acceptance: 超时/失败任务 trace 中 error 非空且文案可读 (covers: S2 可观测)
- [ ] T2: 前端 SSE 失败轮询 /tasks/{id} — acceptance: SSE 失败后 UI 能反映 completed/failed (covers: S2 可观测)
- [ ] T3: 爬虫逐条入库 + 失败 URL — acceptance: run 返回 failed_urls；部分成功仍入库 (covers: S2 爬虫)
- [ ] T4: 爬虫 max_items 默认 20 — acceptance: API 契约与前端/文档一致 (covers: S2 爬虫)
- [ ] T5: experience_years 以 profile 为准 — acceptance: final_result.experience_years == profile (covers: S2 一致性)
- [ ] T6: 视图 URL ?view= 同步 — acceptance: 切换视图 URL 变化；刷新保持 (covers: S2 体验)
- [ ] T7: 空状态 + 快捷词统一 — acceptance: 无数据集有 CTA；Sidebar 使用同一预设列表 (covers: S2 体验)
- [ ] T8: 运行态角标 + 上传/设置文案 — acceptance: running 可感知；上传错误文案可读 (covers: S2 体验)
- [ ] T9: 优化 backlog 文档 + gitignore — acceptance: 文件存在且 git 不跟踪 (covers: S2 文档)
