---
feature: ui-redesign
status: designed
updated: 2026-03-11
branch: feature/fewshot-dynamic-plan
commits: 
---

# 前端界面重构：问答式界面 + 毛玻璃 + 蓝色主题

## Report

## [S1] Problem
当前界面是传统三栏布局，信息密度高但不够清爽。需要模仿主流 AI 问答产品（ChatGPT/Claude）的简洁风格，以毛玻璃效果和蓝色主题提升视觉体验。

## [S2] Design

### 2.1 设计原则
- **问答式布局**：中央对话流，上下文面板可折叠
- **毛玻璃效果**：`backdrop-blur` + 半透明背景
- **蓝色主色调**：清爽蓝 `#3b82f6` 为主色
- **动态效果**：淡入淡出、滑入、脉冲动画
- **避免过度设计**：简洁 > 花哨

### 2.2 色彩系统
```css
--primary: #3b82f6;        /* 主色蓝 */
--primary-light: #60a5fa;  /* 浅蓝 */
--primary-dark: #2563eb;   /* 深蓝 */
--bg-base: #0f172a;        /* 深蓝黑背景 */
--bg-glass: rgba(30, 41, 59, 0.7);  /* 毛玻璃背景 */
--bg-card: rgba(30, 41, 59, 0.5);   /* 卡片背景 */
--text-primary: #f1f5f9;
--text-secondary: #94a3b8;
--border-glass: rgba(148, 163, 184, 0.1);
```

### 2.3 布局结构
```
┌─────────────────────────────────────────┐
│  顶部导航（毛玻璃，吸顶）                   │
├──────┬──────────────────────────────────┤
│      │                                  │
│ 侧栏  │        主内容区（问答流）           │
│ 可折叠│                                  │
│      │  ┌────────────────────────────┐  │
│      │  │ 用户消息（右侧气泡）          │  │
│      │  └────────────────────────────┘  │
│      │  ┌────────────────────────────┐  │
│      │  │ AI 回复（左侧，含时间线/图表） │  │
│      │  └────────────────────────────┘  │
│      │  ┌────────────────────────────┐  │
│      │  │ 输入框（毛玻璃，固定底部）     │  │
│      │  └────────────────────────────┘  │
└──────┴──────────────────────────────────┘
```

### 2.4 组件拆分
```
frontend/app/
├── page.tsx                 # 主页（组合，≤150 行）
├── layout.tsx               # 全局布局 + 字体
├── globals.css              # 全局样式 + CSS 变量
├── components/
│   ├── Navbar.tsx           # 顶部导航（毛玻璃）
│   ├── Sidebar.tsx          # 侧栏（可折叠）
│   ├── ChatArea.tsx         # 对话主区域
│   ├── MessageBubble.tsx    # 消息气泡
│   ├── InputBar.tsx         # 输入栏
│   ├── TimelineCard.tsx     # Agent 时间线卡片
│   ├── ResultCard.tsx       # 结果展示卡片
│   ├── ChartBox.tsx         # ECharts 图表
│   └── GlassPanel.tsx       # 毛玻璃面板（复用）
```

### 2.5 动画效果
- 消息进入：`fadeInUp` 0.3s ease
- 侧栏折叠：`slideInOut` 0.2s ease
- 加载中：脉冲动画
- 按钮悬停：亮度提升 + 轻微放大

### 2.6 毛玻璃实现
```css
.glass {
  background: rgba(30, 41, 59, 0.6);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border: 1px solid rgba(148, 163, 184, 0.1);
  border-radius: 12px;
}
```

## [S3] Out of Scope
- 不改后端 API
- 不改业务逻辑
- 不引入新依赖（除已有 Tailwind/ECharts）

## Tasks
- [ ] T1: 全局样式 + CSS 变量 + 毛玻璃工具类 — acceptance: globals.css 包含完整色彩系统 (covers: S2.2)
- [ ] T2: Navbar + Sidebar 组件 — acceptance: 毛玻璃导航 + 可折叠侧栏 (covers: S2.3)
- [ ] T3: ChatArea + MessageBubble + InputBar — acceptance: 问答式对话流 (covers: S2.3)
- [ ] T4: TimelineCard + ResultCard + ChartBox — acceptance: Agent 时间线 + 结果展示 (covers: S2.4)
- [ ] T5: page.tsx 组合 + 动画 — acceptance: 完整页面可运行，动画流畅 (covers: S2.5)
- [ ] T6: TypeScript 检查 + 测试 — acceptance: tsc 通过 + pytest 全绿 (covers: S2)
