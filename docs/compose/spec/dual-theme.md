---
feature: dual-theme
status: delivered
updated: 2026-03-12
branch: main
commits: working-tree
---

# 深蓝 / 暖黄双主题切换

## Report

**What was built** — MiMo 风格 App Shell（顶栏 + 可折叠图标侧栏 + 主内容）基础上的双主题系统：`navy`（默认深蓝）与 `amber`（暖黄），通过 `html[data-theme]` 切换整套 CSS 变量；Navbar 提供切换按钮并写入 `localStorage`（`agentinsight-theme`），`layout` 内联 script 防首屏闪烁。板块（Card/Section/Collapsible）支持 grid-rows 折叠动画；login/settings/ChartBox 已主题化，侧栏快捷提问经 CustomEvent 填入 ChatPanel。

**Verification** — `tsc --noEmit` EXIT 0；`next build` 编译与静态生成成功；只读评审 3 项 Critical（login/settings 硬编码、快捷提问未接线、Chart 轴色）已修复。

**Journey log**
1. 并行 6 子代理改 CSS/Collapsible/Sidebar/Navbar/面板，文件所有权隔离避免冲突；Collapsible 子代理失败后主会话补写并统一 API。
2. Canvas（ECharts）吃不到 CSS 变量，用 `getComputedStyle` + MutationObserver 在主题切换时重算轴色。
3. Collapsible header 内嵌 button 会非法嵌套，改为 `headerActions` 插槽放在 toggle 外侧。
4. 侧栏快捷提问与 ChatPanel 预设无法跨 AppShell/page 状态，用 `agentinsight:quick-prompt` CustomEvent 解耦。
5. 用户明确选择继续在 main 工作区，未建 worktree。

## [S1] Problem
界面仅有单一深蓝暗色主题，无法在「科技感深蓝」与「暖色纸感黄」之间切换。

## [S2] Design
- 两套 CSS 变量色板挂在 `html[data-theme]`：`navy`（默认）/ `amber`
- 组件一律 `var(--primary)` / `var(--bg-*)` / `var(--text-*)`
- Navbar 切换按钮；`localStorage` key `agentinsight-theme`；内联 script 防闪烁
- App Shell：可折叠 Sidebar（56/240）+ 顶栏 + 主内容；移动端抽屉
- 板块折叠：`grid-template-rows 1fr↔0fr`

## [S3] Out of Scope
- 浅色主题；账号级持久化；图表库深度配色调优

## Tasks
- [x] T1: globals 双色板 — acceptance: `[data-theme="amber"]` 下 primary/bg/text 全部切换 (covers: S2)
- [x] T2: ThemeToggle + Navbar — acceptance: 顶栏按钮切换主题并写入 localStorage (covers: S2)
- [x] T3: AppShell + layout/page 集成 — acceptance: 侧栏可折叠，主区三模块可折叠展示 (covers: S2)
