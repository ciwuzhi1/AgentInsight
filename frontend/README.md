# frontend

Next.js 界面：数据集上传、中文提问分析、简历匹配、岗位爬虫。

## 启动

```bash
npm install
npm run dev    # http://localhost:3100
npx tsc --noEmit
```

## 结构

```
app/
  page.tsx              四视图主页面（SSE / 轮询 / 历史回放）
  layout.tsx            主题初始化
  auth-client.tsx       自动登录与 Bearer 注入
  components/           AppShell / Navbar / Sidebar / workspace-context
  settings/             模型与功能开关
components/
  ChatPanel             提问输入
  TimelinePanel         执行时间线（buildTimeline → TimelineCard）
  MatchPanel            简历与岗位勾选
  CrawlerPanel          岗位抓取
  ResultCard            SQL / 图表 / 结果表
  UploadPanel           CSV 上传
  HistoryPanel          任务历史与回放
  LogPanel              右侧日志
  shared.ts             API 常量与类型
```
