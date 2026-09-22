# AgentInsight

多 Agent 数据分析与简历岗位匹配：上传 CSV 用中文提问即可生成 SQL、执行并出图；上传简历并勾选岗位，多智能体并行打分并输出技能缺口。执行过程经 SSE 实时展示。

技术栈：Next.js · FastAPI · MySQL · Redis（可选）· DuckDB · OpenAI 兼容 LLM

## 界面

| 深蓝 | 暖黄 |
|---|---|
| ![深蓝主题](docs/screenshots/ui-navy-compact.png) | ![暖黄主题](docs/screenshots/ui-amber-light.png) |

| 简历匹配 | 岗位爬虫 |
|---|---|
| ![简历匹配](docs/screenshots/ui-match-log.png) | ![岗位爬虫](docs/screenshots/ui-crawler-log.png) |

## 架构

```
浏览器 :3100  (Next.js 上传 / 提问 / 时间线 / 图表)
      │ REST                    │ SSE
      ▼                        ▼
FastAPI :8100  ──  Agent Runtime（Supervisor + Planner + Executor）
      │              ├─ data_agent      NL2SQL → SQL Guard → DuckDB
      │              ├─ resume ∥ job    并行画像
      │              ├─ match_agent     打分与解读
      │              ├─ validator_agent 结果校验
      │              └─ report_synthesizer  汇总报告
      ├─ MySQL     数据集 / 任务 / 步骤 / 岗位
      └─ Redis     缓存（可选，故障自动降级）
```

- 数据分析：`data → validator → report`（简单问题可跳过校验）
- 简历匹配：`resume ∥ job → match → validator → report`
- 状态机、重试、消息协议见 [ARCHITECTURE.md](ARCHITECTURE.md)

## 运行

环境：Python 3.12+ · Node.js 20+ · MySQL 8 · Docker（可选，仅 Redis）

```bash
# ① 演示数据
python scripts/gen_data.py

# ② 复制 .env.example 为 .env，填好 MySQL 密码后初始化
python scripts/init_db.py

# ③ Redis（可选）
docker compose up -d

# ④ 后端
cd backend
python -m uvicorn app.main:app --port 8100

# ⑤ 前端
cd frontend
npm install
npm run dev
```

打开 http://localhost:3100。LLM 在 `.env` 配置 `LLM_API_KEY`，或前端 `/settings` 热切换模型；无 key 时自动降级规则版 NL2SQL。

## 项目结构

```
backend/          FastAPI + Agent Runtime + 持久层
frontend/         Next.js 界面
scripts/          数据生成 / 库表初始化 / 压测脚本
docker/           可选镜像定义
docs/             API、架构图解、测试说明、界面截图
```

| 文档 | 说明 |
|------|------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 模块职责与扩展点 |
| [docs/架构说明.md](docs/架构说明.md) | 图解架构与时序 |
| [docs/API.md](docs/API.md) | 接口参考 |
| [docs/测试说明.md](docs/测试说明.md) | 测试清单与运行方式 |

测试：`cd backend && python -m pytest tests/unit -q`
