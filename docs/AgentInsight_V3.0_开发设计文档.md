# AgentInsight V3.0 软件开发设计文档

> **版本**：V3.0  
> **定位**：轻量级 Multi-Agent Runtime + Redis 缓存 + DuckDB 数据分析 + 求职匹配平台  
> **变更**：移除 Spark 依赖，新增界面优化、Context Engineering、Report Synthesizer  
> **目标环境**：8GB RAM / 4 Core+，Docker 一键部署

---

## 一、版本定位

V3.0 在 V2.2 基础上做减法和深化：

| 变更 | 说明 |
|------|------|
| **移除 Spark** | 专注 DuckDB 单机分析，降低部署复杂度 |
| **新增 Context Engineering** | Token 预算控制 + 上下文压缩 |
| **新增 Report Synthesizer** | 模板化汇总 + 可选 LLM 润色 |
| **界面全面优化** | 响应式布局、暗色主题、动画过渡、组件拆分 |
| **Few-shot 检索** | 历史成功 SQL 作为 LLM 示例 |
| **动态规划** | 按复杂度自适应生成执行链路 |

**核心原则**：
- Multi-Agent 是核心能力
- Redis 是运行时增强
- DuckDB 是数据执行引擎
- 界面是用户体验载体

---

## 二、技术架构

```
                         User
                           │
                           ↓
                    Next.js (优化后 UI)
                           │
                     REST + SSE
                           │
                           ↓
                      FastAPI
                           │
              ┌────────────┴────────────┐
              ↓                         ↓
        Agent Runtime                MySQL
              │
       ┌──────┴──────┐
       ↓             ↓
    Redis         TaskState
       │             │
       └──────┬──────┘
              ↓
          Supervisor
              ↓
           Planner
              ↓
    ┌─────────┼─────────┐
    ↓         ↓         ↓
 Resume      Job       Data
 Agent      Agent      Agent
    │         │         │
    ↓         ↓         ↓
 Parser      Parser   DuckDB
                         │
                    ┌────┴────┐
                    ↓         ↓
              Few-shot    SQL Guard
              检索         校验
                    │         │
                    └────┬────┘
                         ↓
                    Match Agent
                         ↓
                     Validator
                         ↓
                 Report Synthesizer
                         ↓
                    Final Result
```

---

## 三、模块设计

### 3.1 Agent Runtime

#### 状态机

```
CREATED → ROUTING → RUNNING → VALIDATING → COMPLETED
                ↓         ↓
              FAILED → RETRYING → RUNNING
                ↓
           FAILED_FINAL
```

#### 动态路由（四级）

| 级别 | 判定 | 链路 |
|------|------|------|
| SIMPLE | 短问题 + 聚合词 | data → 完成 |
| NORMAL | 默认 | data → validator |
| COMPLEX | 对比/嵌套/多表 | resume∥job → match → validator → report |
| 匹配链路 | 含简历上下文 | resume∥job → match → validator → report |

#### WorkflowExecutor

- 波次拓扑执行，波内 `asyncio.gather` 并行
- 指数退避重试（0.5/1/2s，上限 4s）
- 每步超时 120s
- optional 步骤失败自动跳过 + 下游级联跳过
- fail-fast：必经步骤失败时并发兄弟步骤立即停止

### 3.2 Agent 设计

| Agent | 职责 | 输入 | 输出 |
|-------|------|------|------|
| **Supervisor** | 任务分类、复杂度判断、Agent 选择 | User Query | Task Type + Complexity |
| **Planner** | 任务拆解、依赖分析、执行顺序 | Complexity | PlanStep DAG |
| **ResumeAgent** | 简历解析（PDF/DOCX/TXT） | 文件路径 | Resume Profile |
| **JobAgent** | JD 结构化 | JD 文本/ID | Job Profile |
| **DataAgent** | NL2SQL + DuckDB 执行 | 数据集 + 问题 | SQL + 结果 + 图表 |
| **MatchAgent** | 五维打分 + 技能缺口 | Resume + Job Profile | Score + Dimensions + Gap |
| **ValidatorAgent** | 结果结构校验 | Agent 输出 | 校验通过/失败 |
| **ReportSynthesizer** | 汇总报告 | 所有 Agent 结果 | 最终报告 |

#### AgentMessage 协议

```python
class AgentMessage:
    message_id: str      # 唯一 ID
    task_id: str         # 所属任务
    sender: str          # 发送方 Agent
    receiver: str        # 接收方 Agent
    type: str            # 消息类型（resume_profile/job_profile/match_result）
    version: str         # 协议版本 "1.0"
    payload: dict        # 业务数据
    created_at: str      # 时间戳
```

### 3.3 匹配算法

三层匹配：

```
1. Exact Match     → 精确匹配（大小写归一 + 同义词表）
2. TF-IDF Match    → 词频加权匹配（稀有技能权重更高）
3. Coverage Match  → 覆盖率匹配（JD 要求被满足比例）

最终得分 = TF-IDF × 0.6 + Coverage × 0.4
         → 加权汇总五维（skill 0.5 / project 0.2 / experience 0.1 / education 0.1 / engineering 0.1）
```

同义词归一表（12 组）：
```
javascript ↔ js
kubernetes ↔ k8s
机器学习 ↔ ml
python ↔ py
typescript ↔ ts
go ↔ golang
vue ↔ vue.js
spring boot ↔ springboot
ci/cd ↔ 持续集成 ↔ ci
docker ↔ 容器
git ↔ 版本控制
postgresql ↔ postgres
```

### 3.4 NL2SQL 优化

#### Few-shot 检索

```
用户提问 → TF-IDF 向量化 → 检索相似历史 → 注入 prompt → LLM 生成 SQL
                                    ↓
                            SQL 执行成功 → 存入历史库
```

- 进程内历史库，LRU 上限 500 条
- 相似度阈值 0.1，取 top-3 示例
- 中文逐字 + 英文单词分词

#### 错误反馈重试

```
生成 SQL → Guard 校验 → 执行
    ↓ 失败
错误信息 + 原 SQL + schema → 喂回 LLM → 重新生成
    ↓ 最多重试 2 次
仍失败 → 报错
```

#### SQL Guard

- 只允许单条 SELECT
- 黑名单词过滤（INSERT/UPDATE/DELETE/DROP/ALTER 等）
- 自动加 LIMIT（默认 1000）
- 表名白名单校验

### 3.5 Context Engineering

#### Token 预算控制

```python
class TokenBudget:
    max_input_tokens: int = 4000    # 单次 LLM 输入上限
    max_output_tokens: int = 1000   # 单次 LLM 输出上限
    max_context_tokens: int = 8000  # 任务总上下文上限
```

#### 上下文压缩

```
完整上下文 → 提取关键信息 → 压缩摘要 → 注入 prompt
```

压缩策略：
- 简历：只保留 skills/projects/education/experience_years
- JD：只保留 must_have/nice_to_have/skills
- 数据：只保留 schema + 前 5 行样例

### 3.6 Report Synthesizer

#### 模板化汇总（默认）

```python
def synthesize_template(results: dict) -> str:
    """无 LLM 调用，纯模板拼接"""
    if "match" in results:
        return f"""
匹配分析报告
============
综合匹配度：{results['match']['score']} 分
技能匹配：{results['match']['dimensions']['skill']} 分
项目匹配：{results['match']['dimensions']['project']} 分
技能缺口：{', '.join(results['match']['skill_gap'][:5])}
        """
    # 数据分析报告...
```

#### LLM 润色（可选）

```python
if settings.report_llm_enabled:
    interpretation = await llm.generate(REPORT_PROMPT, template_result)
```

---

## 四、Redis 设计

### 4.1 Key 命名空间

| Key 模式 | 用途 | TTL |
|----------|------|-----|
| `cache:resume:{hash}` | 简历解析缓存 | 24h |
| `cache:job:{hash}` | JD 解析缓存 | 24h |
| `cache:nl2sql:{hash}` | NL2SQL 结果缓存 | 1h |
| `cache:schema:{dataset_id}` | 数据集 Schema 缓存 | 1h |
| `agent:lock:{hash}` | 任务幂等锁 | 300s |

### 4.2 单例连接

```python
# 进程内共享一个连接，避免每次操作新建 TCP
_client: Redis | None = None
_client_lock: asyncio.Lock

async def get_redis() -> Redis | None:
    """懒初始化 + 冷却期（5s）+ 失败降级"""
```

### 4.3 降级策略

```
Redis 挂了 → 返回 None → 直接查库/执行
冷却期 5s → 不频繁重试
```

---

## 五、DuckDB 设计

### 5.1 连接管理

```python
class DuckDBEngine:
    _MAX_CONNS = 10  # LRU 淘汰
    
    def _conn(self, dataset_id):
        # 已有连接直接返回
        # 新建前先淘汰最久未用的
        conn = duckdb.connect()
        conn.execute("SET memory_limit='1GB'")
        conn.execute("SET threads=4")
```

### 5.2 路由策略

```python
def choose_engine(profile):
    # 综合评分：行数 × 列数 / 1000
    score = profile.rows_estimate * max(1, len(profile.columns)) / 1000
    if score >= 100:  # SPARK_SCORE_THRESHOLD
        # 数据量过大，提示用户
        return "too_large"
    return "duckdb"
```

### 5.3 超时与中断

```python
async def execute(self, dataset_id, sql):
    timeout = 30  # 秒
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(self._execute_sync, ...), timeout
        )
    except asyncio.TimeoutError:
        conn.interrupt()  # 硬中断
        raise EngineError("查询超时")
```

---

## 六、界面优化设计

### 6.1 整体布局

```
┌─────────────────────────────────────────────────┐
│  吸顶导航栏（毛玻璃效果 + 锚点）                    │
├──────────────┬──────────────────────────────────┤
│              │                                  │
│  左侧面板     │        右侧内容区                  │
│  (30%)       │        (70%)                     │
│              │                                  │
│  ┌────────┐  │  ┌────────────────────────────┐  │
│  │数据上传 │  │  │  Agent 执行时间线            │  │
│  └────────┘  │  │  ┌──────┐ ┌──────┐         │  │
│  ┌────────┐  │  │  │resume│ │ job  │ ← 并行   │  │
│  │提问输入 │  │  │  └──┬───┘ └──┬───┘         │  │
│  └────────┘  │  │     └────┬───┘              │  │
│  ┌────────┐  │  │       ┌──▼───┐              │  │
│  │简历上传 │  │  │       │match │              │  │
│  └────────┘  │  │       └──┬───┘              │  │
│  ┌────────┐  │  │       ┌──▼────┐             │  │
│  │历史记录 │  │  │       │validator│            │  │
│  └────────┘  │  │       └───────┘             │  │
│              │  └────────────────────────────┘  │
│              │  ┌────────────────────────────┐  │
│              │  │  结果展示区                   │  │
│              │  │  图表 + 数据表 + 匹配分数      │  │
│              │  └────────────────────────────┘  │
└──────────────┴──────────────────────────────────┘
```

### 6.2 组件拆分

```
frontend/app/
├── page.tsx                    # 主页（组合布局，≤200 行）
├── components/
│   ├── layout/
│   │   ├── Navbar.tsx          # 吸顶导航
│   │   └── Sidebar.tsx         # 侧边栏
│   ├── data/
│   │   ├── UploadPanel.tsx     # 数据上传
│   │   ├── ChatPanel.tsx       # 提问输入
│   │   └── ResultPanel.tsx     # 结果展示
│   ├── match/
│   │   ├── ResumeUpload.tsx    # 简历上传
│   │   ├── JobSelector.tsx     # 岗位选择
│   │   └── MatchResult.tsx     # 匹配结果
│   ├── timeline/
│   │   ├── TimelineView.tsx    # 时间线容器
│   │   ├── StepCard.tsx        # 步骤卡片
│   │   └── EventItem.tsx       # 事件条目
│   ├── chart/
│   │   └── ChartBox.tsx        # ECharts 图表
│   └── history/
│       └── HistoryPanel.tsx    # 历史记录
```

### 6.3 视觉规范

#### 色彩系统

```css
:root {
  /* 主色 */
  --primary: #3b82f6;        /* 蓝色 */
  --primary-hover: #2563eb;
  
  /* 背景 */
  --bg-primary: #0f172a;     /* 深蓝黑 */
  --bg-secondary: #1e293b;
  --bg-card: #1e293b;
  
  /* 文字 */
  --text-primary: #f1f5f9;
  --text-secondary: #94a3b8;
  --text-muted: #64748b;
  
  /* 状态 */
  --success: #22c55e;
  --warning: #eab308;
  --error: #ef4444;
  --info: #3b82f6;
  
  /* 边框 */
  --border: #334155;
  --border-hover: #475569;
}
```

#### 字体规范

```css
/* 中文 */
font-family: 'PingFang SC', 'Microsoft YaHei', sans-serif;

/* 代码 */
font-family: 'Consolas', 'Monaco', monospace;
```

#### 间距规范

```css
/* 8px 网格 */
gap-1: 4px
gap-2: 8px
gap-3: 12px
gap-4: 16px
gap-6: 24px
gap-8: 32px
```

### 6.4 动画过渡

```css
/* 通用过渡 */
transition: all 0.2s ease-in-out;

/* 步骤卡片进入 */
@keyframes slideIn {
  from { opacity: 0; transform: translateY(10px); }
  to { opacity: 1; transform: translateY(0); }
}

/* 进度条 */
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}

/* 错误抖动 */
@keyframes shake {
  0%, 100% { transform: translateX(0); }
  25% { transform: translateX(-5px); }
  75% { transform: translateX(5px); }
}
```

### 6.5 响应式断点

```css
/* 移动端 */
@media (max-width: 768px) {
  .sidebar { display: none; }
  .content { width: 100%; }
}

/* 平板 */
@media (min-width: 769px) and (max-width: 1024px) {
  .sidebar { width: 240px; }
}

/* 桌面 */
@media (min-width: 1025px) {
  .sidebar { width: 320px; }
}
```

### 6.6 错误展示增强

```tsx
// 重试中：黄色警告
<div className="border-amber-500/40 bg-amber-500/10 text-amber-300">
  ⚠ 执行出错，正在重试…
</div>

// 终态失败：红色醒目
<div className="border-red-500/50 bg-red-500/15 text-red-200 shadow-lg shadow-red-500/10">
  ✕ 任务失败
</div>
```

### 6.7 ECharts 按需引入

```typescript
import * as echarts from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import { GridComponent, TooltipComponent, LegendComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([BarChart, LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer]);
// Bundle 减少 ~70%
```

### 6.8 SSE 重连策略

```typescript
// 指数退避，最多 3 次
const maxReconnects = 3;
es.onerror = () => {
  if (reconnectCount < maxReconnects) {
    reconnectCount++;
    const delay = Math.min(1000 * 2 ** (reconnectCount - 1), 8000);
    setTimeout(() => subscribe(taskId), delay);
  }
};
```

---

## 七、API 设计

### 7.1 认证

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/register` | 注册 |
| POST | `/api/auth/login` | 登录（返回 JWT） |
| GET | `/api/auth/me` | 当前用户 |

### 7.2 数据集

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/datasets` | 上传 CSV |
| GET | `/api/datasets` | 列表 |
| GET | `/api/datasets/{id}` | 详情 |

### 7.3 任务

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/tasks` | 创建分析任务 |
| GET | `/api/tasks` | 任务历史 |
| GET | `/api/tasks/{id}` | 任务详情 |
| GET | `/api/tasks/{id}/events` | SSE 事件流 |

### 7.4 简历匹配

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/resumes` | 上传简历 |
| GET | `/api/resumes` | 简历列表 |
| POST | `/api/matches` | 创建匹配任务 |

### 7.5 设置

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/settings` | 读取设置 |
| PUT | `/api/settings` | 更新设置 |
| GET | `/api/models` | 模型列表 |
| POST | `/api/models` | 新增模型 |
| PUT | `/api/models/{id}/activate` | 激活模型 |

### 7.6 健康检查

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 基础健康 |
| GET | `/api/health/live` | 存活探针 |
| GET | `/api/health/ready` | 就绪探针 |
| GET | `/api/health/mysql` | MySQL 状态 |
| GET | `/api/health/redis` | Redis 状态 |

---

## 八、数据库设计

### 8.1 核心表

```sql
-- 用户表
CREATE TABLE users (
    id VARCHAR(32) PRIMARY KEY,
    username VARCHAR(32) UNIQUE NOT NULL,
    password_hash VARCHAR(128) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 数据集表
CREATE TABLE datasets (
    id VARCHAR(32) PRIMARY KEY,
    user_id VARCHAR(32),
    name VARCHAR(128),
    path VARCHAR(256),
    table_name VARCHAR(64),
    schema_json JSON,
    rows_estimate INT,
    size_mb FLOAT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 任务表
CREATE TABLE tasks (
    id VARCHAR(32) PRIMARY KEY,
    user_id VARCHAR(32),
    dataset_id VARCHAR(32),
    query TEXT,
    status VARCHAR(16),
    engine VARCHAR(16),
    final_result JSON,
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

-- 任务步骤表
CREATE TABLE task_steps (
    id INT AUTO_INCREMENT PRIMARY KEY,
    task_id VARCHAR(32),
    step INT,
    agent_name VARCHAR(32),
    status VARCHAR(16),
    latency_ms INT,
    retry_count INT DEFAULT 0,
    detail JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 简历表
CREATE TABLE resumes (
    id VARCHAR(32) PRIMARY KEY,
    user_id VARCHAR(32),
    filename VARCHAR(128),
    path VARCHAR(256),
    profile JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 岗位表
CREATE TABLE jobs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_id VARCHAR(32),
    title VARCHAR(128),
    company VARCHAR(128),
    location VARCHAR(64),
    description TEXT,
    skills TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 匹配结果表
CREATE TABLE matches (
    id INT AUTO_INCREMENT PRIMARY KEY,
    task_id VARCHAR(32),
    resume_id VARCHAR(32),
    job_ids JSON,
    score INT,
    result JSON,
    user_id VARCHAR(32),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 模型配置表
CREATE TABLE model_configs (
    id VARCHAR(32) PRIMARY KEY,
    name VARCHAR(64),
    provider VARCHAR(32),
    base_url VARCHAR(256),
    api_key_enc TEXT,
    model VARCHAR(64),
    temperature FLOAT DEFAULT 0,
    is_active BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 功能开关表
CREATE TABLE app_settings (
    `key` VARCHAR(64) PRIMARY KEY,
    `value` TEXT,
    is_secret BOOLEAN DEFAULT FALSE,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);
```

### 8.2 索引

```sql
CREATE INDEX idx_tasks_user ON tasks(user_id);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_task_steps_task ON task_steps(task_id);
CREATE INDEX idx_resumes_user ON resumes(user_id);
CREATE INDEX idx_jobs_user ON jobs(user_id);
CREATE INDEX idx_matches_user ON matches(user_id);
```

---

## 九、部署设计

### 9.1 Docker Compose

```yaml
version: '3.8'

services:
  frontend:
    build: ./docker/frontend.Dockerfile
    ports:
      - "3100:3000"
    environment:
      - NEXT_PUBLIC_API_BASE=http://localhost:8100
    depends_on:
      - backend

  backend:
    build: ./docker/backend.Dockerfile
    ports:
      - "8100:8000"
    environment:
      - MYSQL_HOST=mysql
      - MYSQL_PORT=3306
      - MYSQL_USER=agentinsight
      - MYSQL_PASSWORD=${MYSQL_PASSWORD}
      - MYSQL_DATABASE=agentinsight
      - REDIS_URL=redis://redis:6379/0
      - LLM_API_KEY=${LLM_API_KEY}
    depends_on:
      - mysql
      - redis
    volumes:
      - ./data:/app/data

  mysql:
    image: mysql:8.0
    environment:
      - MYSQL_ROOT_PASSWORD=${MYSQL_ROOT_PASSWORD}
      - MYSQL_DATABASE=agentinsight
      - MYSQL_USER=agentinsight
      - MYSQL_PASSWORD=${MYSQL_PASSWORD}
    volumes:
      - mysql_data:/var/lib/mysql
      - ./scripts/init.sql:/docker-entrypoint-initdb.d/init.sql
    ports:
      - "3306:3306"

  redis:
    image: redis:7-alpine
    command: redis-server --maxmemory 256mb --maxmemory-policy allkeys-lru
    volumes:
      - redis_data:/data
    ports:
      - "6379:6379"

volumes:
  mysql_data:
  redis_data:
```

### 9.2 环境变量

```bash
# .env.example

# MySQL
MYSQL_ROOT_PASSWORD=your_root_password
MYSQL_PASSWORD=your_password

# LLM
LLM_API_KEY=your_api_key
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat

# App
APP_SECRET=your_app_secret
CORS_ORIGINS=http://localhost:3100
```

### 9.3 资源目标

| 组件 | 内存 | CPU |
|------|------|-----|
| Next.js | 256MB | 0.5 |
| FastAPI | 512MB | 1 |
| MySQL | 512MB | 1 |
| Redis | 256MB | 0.5 |
| DuckDB | 按需（上限 1GB） | 4 线程 |
| **总计** | ~2GB 常驻 | 4 Core |

---

## 十、测试设计

### 10.1 单元测试

```bash
cd backend && python -m pytest tests/unit -q
```

覆盖：
- 状态机迁移
- Agent Registry
- SQL Guard
- 匹配算法
- Few-shot 检索
- 复杂度评估
- 缓存策略
- 简历解析

### 10.2 评测体系

```bash
cd backend && python -m app.evaluation.runner
```

100 case：
- 60 NL2SQL（结构断言）
- 20 匹配（分数区间）
- 10 路由（Agent 选择）
- 10 异常（graceful 处理）

### 10.3 性能基准

```bash
python scripts/api_bench.py --n 10
python scripts/bench_duckdb.py
```

指标：
- L1 可用性延迟
- L2 读接口延迟
- L3 写与链路延迟
- L4 健壮性

---

## 十一、安全设计

### 11.1 认证授权

- JWT 令牌（7 天过期）
- Bearer Token 认证
- 数据按 user_id 隔离

### 11.2 数据安全

- API Key Fernet 加密存储
- 密码 PBKDF2 哈希（100k 迭代）
- 回显脱敏（`***尾4位`）

### 11.3 输入校验

- SQL Guard（只读 SELECT、黑名单词）
- 上传大小限制（10MB）
- 速率限制（登录 5/min、任务 30/min）

### 11.4 CORS

```python
# 白名单来源，空值拒绝启动
CORS_ORIGINS = ["http://localhost:3100"]
```

---

## 十二、开发路线

### 阶段 1：核心链路（1 周）

- [ ] Agent Runtime（状态机 + 执行器）
- [ ] DataAgent + DuckDB
- [ ] 基础 API + SSE
- [ ] 前端骨架

### 阶段 2：匹配链路（1 周）

- [ ] ResumeAgent + JobAgent
- [ ] MatchAgent（TF-IDF 算法）
- [ ] 匹配前端页面

### 阶段 3：优化增强（1 周）

- [ ] Redis 缓存 + 幂等锁
- [ ] Few-shot 检索
- [ ] 动态规划
- [ ] Context Engineering

### 阶段 4：界面优化（1 周）

- [ ] 组件拆分
- [ ] 响应式布局
- [ ] 动画过渡
- [ ] 错误展示增强

### 阶段 5：完善交付（3 天）

- [ ] Report Synthesizer
- [ ] 评测体系
- [ ] Docker 部署
- [ ] 文档完善

---

## 十三、验收清单

### 功能验收

- [ ] Agent Runtime 可创建、执行、终止任务
- [ ] Supervisor 能根据复杂度选择链路
- [ ] Planner 能生成带依赖的执行计划
- [ ] Resume/Job/Data/Match Agent 独立职责
- [ ] Agent 间通过 AgentMessage 交换结果
- [ ] Redis Cache/Task Lock/Idempotency 覆盖关键路径
- [ ] Redis 故障不导致业务中断
- [ ] DuckDB 路由逻辑可测试复现
- [ ] MySQL 可追溯 Task/Agent Run
- [ ] 前端展示 Agent Trace 和结果
- [ ] 100+ Evaluation Cases 形成回归集
- [ ] Docker Compose 可一键启动

### 性能验收

- [ ] 简单查询 < 500ms
- [ ] 匹配链路 < 5s
- [ ] Redis 命中 < 100ms
- [ ] 前端首屏 < 2s

### 安全验收

- [ ] 未认证访问返回 401
- [ ] 数据按用户隔离
- [ ] API Key 加密存储
- [ ] SQL 注入防护

---

## 附录 A：目录结构

```
AgentInsight/
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   ├── api/
│   │   ├── core/
│   │   ├── agent_runtime/
│   │   ├── agents/
│   │   ├── tools/
│   │   ├── context/
│   │   ├── cache/
│   │   ├── data_engine/
│   │   ├── persistence/
│   │   └── evaluation/
│   └── tests/
├── frontend/
│   ├── app/
│   ├── components/
│   └── lib/
├── data/
│   └── demo/
├── docker/
│   ├── backend.Dockerfile
│   └── frontend.Dockerfile
├── scripts/
├── docker-compose.yml
├── Makefile
├── README.md
├── ARCHITECTURE.md
└── .env.example
```

---

## 附录 B：技术栈清单

| 层 | 技术 |
|---|---|
| **前端** | Next.js 15, React 19, TypeScript, Tailwind CSS, ECharts |
| **后端** | Python 3.12, FastAPI, Pydantic |
| **Agent** | 自研 Runtime（Supervisor/Planner/Executor） |
| **存储** | MySQL 8.0, Redis 7 |
| **数据** | DuckDB, Pandas |
| **部署** | Docker, Docker Compose |

---

*文档版本：V3.0*  
*最后更新：2026-03-11*
