---
feature: fewshot-dynamic-plan
status: designed
updated: 2026-03-11
branch: feature/fewshot-dynamic-plan
commits: 
---

# Few-shot 检索 + 动态规划

## Report

## [S1] Problem
1. NL2SQL 每次从零生成 SQL，没有利用历史成功案例，准确率有提升空间
2. 工作流执行计划是固定模板，简单问题也走完整链路，延迟浪费

## [S2] Design

### 2.1 Few-shot 检索
- 维护一个进程内的成功 SQL 历史库（question → sql 映射）
- 用户提问时，用 TF-IDF 向量化检索语义最相似的 top-3 历史问题
- 将相似问题及其 SQL 作为 few-shot 示例注入 LLM prompt
- SQL 执行成功后，将 (question, sql) 存入历史库
- 历史库上限 500 条，LRU 淘汰

### 2.2 动态规划
- Supervisor.route() 中增加复杂度评估：
  - 简单问题（单聚合/单筛选）→ 跳过 validator，data → final
  - 普通问题 → data → validator（现有链路）
  - 复杂问题（多表/嵌套/对比）→ data → validator → 增强校验
- 复杂度判定基于关键词匹配（Top/对比/多表/嵌套等）
- PlanStep 增加 `priority` 字段供前端展示

## [S3] Out of Scope
- 不引入向量数据库（用进程内 TF-IDF）
- 不做跨会话持久化（历史库重启清空）
- 不改变现有 Agent 接口

## Tasks
- [ ] T1: 实现 few_shot.py 检索模块 — acceptance: 给定问题能检索出相似历史 SQL (covers: S2.1)
- [ ] T2: DataAgent 集成 few-shot — acceptance: LLM prompt 包含相似示例 (covers: S2.1; depends: T1)
- [ ] T3: 实现 complexity.py 复杂度评估 — acceptance: 简单/普通/复杂问题正确分类 (covers: S2.2)
- [ ] T4: Supervisor 集成动态规划 — acceptance: 简单问题跳过 validator (covers: S2.2; depends: T3)
- [ ] T5: 单元测试 — acceptance: pytest 全绿 (covers: S2)
