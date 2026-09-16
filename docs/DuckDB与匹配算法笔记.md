# DuckDB 与匹配算法笔记

> 整理自 AgentInsight 项目实践（2026-03-11）

---

## 一、DuckDB 是什么

**DuckDB** 是一个嵌入式分析型数据库（OLAP），专为高速分析查询设计。

### 核心特点

| 特点 | 说明 |
|------|------|
| **嵌入式** | 无需独立服务器进程，直接嵌入应用（类似 SQLite，但面向分析） |
| **列式存储** | 数据按列存储，聚合查询（SUM/COUNT/AVG）极快 |
| **向量化执行** | CPU SIMD 指令批量处理数据，充分利用现代硬件 |
| **零依赖** | 单个二进制文件，无需安装配置 |
| **SQL 兼容** | 支持标准 SQL + 分析扩展（窗口函数、PIVOT 等） |
| **多格式直读** | 可直接查询 CSV/Parquet/JSON，无需导入 |

### 与其他数据库对比

| | DuckDB | SQLite | PostgreSQL |
|---|---|---|---|
| 定位 | 分析查询（OLAP） | 事务处理（OLTP） | 通用 |
| 存储 | 列式 | 行式 | 行式 |
| 部署 | 嵌入式 | 嵌入式 | 独立服务器 |
| 聚合性能 | 极快 | 慢 | 中等 |
| 并发写 | 不适合 | 适合 | 适合 |

**一句话**：DuckDB 是"分析版的 SQLite"，专为快速跑 SQL 聚合而生。

---

## 二、DuckDB 结构

### 整体架构

```
┌─────────────────────────────────────────────┐
│                 应用层 (Python API)           │
│            duckdb.connect() / execute()      │
└─────────────────┬───────────────────────────┘
                  │
┌─────────────────▼───────────────────────────┐
│              SQL 解析器 (Parser)              │
│         SQL 文本 → 抽象语法树 (AST)           │
└─────────────────┬───────────────────────────┘
                  │
┌─────────────────▼───────────────────────────┐
│           优化器 (Optimizer)                  │
│    谓词下推 / 列裁剪 / 常量折叠 / Join 重排    │
└─────────────────┬───────────────────────────┘
                  │
┌─────────────────▼───────────────────────────┐
│         向量化执行引擎 (Vectorized Engine)     │
│      算子树: Scan → Filter → Aggregate → ...  │
│      批量处理: 一次处理 2048 行向量             │
└─────────────────┬───────────────────────────┘
                  │
┌─────────────────▼───────────────────────────┐
│            存储层 (Storage)                   │
│   列式存储 / 压缩 / 内存管理 / 缓冲池          │
└─────────────────────────────────────────────┘
```

### 关键组件

| 组件 | 职责 |
|------|------|
| **Parser** | SQL 文本 → 语法树，支持标准 SQL + 扩展 |
| **Optimizer** | 自动优化查询计划（谓词下推、列裁剪等） |
| **Vectorized Engine** | 向量化执行，批量处理 2048 行/批 |
| **Storage** | 列式存储，支持内存/磁盘/直接读文件 |
| **Transaction** | MVCC 事务，支持并发读写 |

### 列式存储示意

```
行式存储（SQLite/MySQL）:
┌──────┬──────┬──────┐
│ name │ age  │ city │  ← 一行完整存储
│ 张三 │ 25   │ 北京 │
│ 李四 │ 30   │ 上海 │
└──────┴──────┴──────┘

列式存储（DuckDB）:
┌──────┬──────┬──────┐
│ name │ age  │ city │  ← 每列独立存储
├──────┼──────┼──────┤
│ 张三 │ 25   │ 北京 │
│ 李四 │ 30   │ 上海 │
└──────┴──────┴──────┘
  ↓ 只读 age 列时，跳过 name/city，IO 减少 2/3
```

---

## 三、DuckDB 嵌入式部署

### 与传统数据库的区别

| | DuckDB | PostgreSQL/MySQL |
|---|---|---|
| 部署 | `pip install duckdb` 即可 | 安装服务器 + 配置 + 启动服务 |
| 连接 | `duckdb.connect()` 进程内 | TCP 网络连接 |
| 配置 | 代码里 `SET` 语句 | 配置文件 + 重启 |
| 数据位置 | 内存/本地文件 | 服务器磁盘 |
| 多进程 | 每个进程独立实例 | 多客户端共享一个服务器 |

### 代码示例

```python
# 无需启动任何服务，直接使用
import duckdb

# 创建连接（进程内）
conn = duckdb.connect()

# 直接读 CSV，无需导入
conn.execute("SELECT * FROM read_csv_auto('data.csv')")

# 配置资源限制
conn.execute("SET memory_limit='1GB'")
conn.execute("SET threads=4")

# 执行查询
rows = conn.execute("SELECT region, SUM(sales) FROM t GROUP BY region").fetchall()

# 关闭即释放
conn.close()
```

### 在 AgentInsight 项目中的应用

```python
class DuckDBEngine:
    _conns: dict[str, DuckDBPyConnection]  # dataset_id → 连接
    _MAX_CONNS = 10                         # LRU 淘汰
    
    def _conn(self, dataset_id):
        conn = duckdb.connect()  # 直接创建，无需启动服务
        conn.execute("SET memory_limit='1GB'")
        conn.execute("SET threads=4")
        self._conns[dataset_id] = conn  # 存在 Python 进程内存里
        return conn
```

**关键点**：`duckdb.connect()` 返回的是一个**普通 Python 对象**，不是网络连接。

---

## 四、DuckDB 方便分析什么

DuckDB 擅长**单机、中等规模数据的交互式分析**，典型场景：

### 1. 聚合统计
```sql
-- 按地区统计销售额
SELECT region, SUM(sales) as total FROM t GROUP BY region

-- 各品类平均值
SELECT category, AVG(price) FROM t GROUP BY category
```

### 2. Top-N 排序
```sql
-- 销量前 10 的商品
SELECT product, SUM(qty) as total FROM t GROUP BY product ORDER BY total DESC LIMIT 10
```

### 3. 时间序列分析
```sql
-- 按月统计趋势
SELECT month(order_date) as m, SUM(sales) FROM t GROUP BY m ORDER BY m
```

### 4. 分布分析
```sql
-- 薪资区间分布
SELECT 
  CASE 
    WHEN salary < 10 THEN '0-10k'
    WHEN salary < 20 THEN '10-20k'
    ELSE '20k+'
  END as range,
  COUNT(*) 
FROM jobs GROUP BY range
```

### 5. 多表关联
```sql
-- 简历与岗位匹配
SELECT r.name, j.title, j.company 
FROM resumes r JOIN jobs j ON r.skill = j.required_skill
```

### 在 AgentInsight 项目中的具体用途

| 场景 | DuckDB 分析内容 |
|------|----------------|
| 销售数据 | 按地区/商品/月份聚合销售额、销量 |
| 岗位数据 | 技能需求 Top N、城市分布、薪资统计 |
| 简历匹配 | 五维打分（技能/项目/经验/学历/工程化） |

**一句话**：凡是「给一份 CSV，问一个统计问题」的场景，DuckDB 都能在毫秒级返回结果。

---

## 五、匹配算法：TF-IDF + 余弦相似度

### 要解决什么问题

**旧算法**：简单集合交集
```python
score = len(resume_skills ∩ jd_skills) / len(jd_skills)
```

问题：所有技能权重相同。匹配到 "Python"（到处都有）和匹配到 "COBOL"（很稀有）得分一样。

**新算法**：稀有技能权重更高。

---

### TF-IDF 是什么

**TF-IDF = Term Frequency × Inverse Document Frequency**

#### TF（词频）— 这个技能在这个 JD 里多重要

```
JD 要求: [Python, Java, Python, Go]
                ↑出现2次

TF(Python) = 2/4 = 0.5  （占 JD 技能的 50%）
TF(Java)   = 1/4 = 0.25
TF(Go)     = 1/4 = 0.25
```

#### IDF（逆文档频率）— 这个技能在所有 JD 里多稀有

假设有 100 个 JD：
- "Python" 出现在 80 个 JD → 常见 → IDF 低
- "COBOL" 出现在 2 个 JD → 稀有 → IDF 高

```
IDF(skill) = log(总JD数 / (包含该技能的JD数 + 1)) + 1

IDF(Python) = log(100 / (80+1)) + 1 ≈ 1.22  （常见，权重低）
IDF(COBOL)  = log(100 / (2+1)) + 1  ≈ 4.22  （稀有，权重高）
```

#### TF-IDF 权重

```
权重 = TF × IDF

Python: 0.5 × 1.22 = 0.61  （常见技能，权重低）
COBOL:  1.0 × 4.22 = 4.22  （稀有技能，权重高）
```

---

### 余弦相似度是什么

把简历和 JD 的技能表示成**向量**，计算两个向量的夹角。

```
简历向量: [Python=0.61, Java=0.30, Go=0.25]
JD向量:   [Python=0.61, Java=0.30, Rust=1.50]

余弦相似度 = (A·B) / (|A| × |B|)
          = 点积 / (模长A × 模长B)
```

**几何意义**：
```
相似度 = 1.0  → 方向完全相同（完美匹配）
相似度 = 0.5  → 45° 夹角（部分匹配）
相似度 = 0.0  → 90° 夹角（完全不相关）
```

---

### 组合得分

```python
最终得分 = TF-IDF余弦相似度 × 0.6 + 技能覆盖率 × 0.4
```

| 维度 | 作用 | 权重 |
|------|------|------|
| TF-IDF 余弦相似度 | 匹配**质量**（稀有技能更重要） | 60% |
| 技能覆盖率 | 匹配**数量**（JD 要求被满足多少） | 40% |

---

### 代码实现流程

```python
def combined_skill_score(resume_skills, all_jobs_skills):
    # 1. 计算 TF-IDF 权重
    jd_weights, resume_weights = compute_tfidf_weights(...)
    
    # 2. 余弦相似度
    tfidf_score = cosine_similarity(jd_weights, resume_weights)
    
    # 3. 技能覆盖率
    coverage = len(resume ∩ jd_union) / len(jd_union)
    
    # 4. 组合
    return tfidf_score * 0.6 + coverage * 0.4
```

---

### 举例对比

**场景**：3 个 JD，简历有 ["Python"]

| 技能 | 出现 JD 数 | IDF | 旧算法 | 新算法 |
|------|-----------|-----|--------|--------|
| Python | 3/3 | 低 | 33% | 低分（常见） |
| COBOL | 1/3 | 高 | 33% | 高分（稀有） |

**结论**：匹配到稀有技能的简历得分更高。

---

### 面试可讲点

1. **为什么用 TF-IDF？** — 区分技能的"信息量"，稀有技能匹配更有价值
2. **为什么用余弦相似度？** — 对向量长度不敏感，只看方向（适合稀疏技能集）
3. **为什么还要覆盖率？** — TF-IDF 可能高估稀有技能，覆盖率保证"数量"维度
4. **与 Word2Vec/BERT 的区别？** — TF-IDF 是词频统计，不需要训练；语义模型需要预训练

---

## 六、NL2SQL 是什么

**NL2SQL** = **Natural Language to SQL**（自然语言转 SQL）

用户用中文提问 → LLM 生成 SQL → 数据库执行 → 返回结果。

```
用户: "按地区统计总销售额"
  ↓ LLM
SQL: SELECT region, SUM(sales) as total_sales FROM ds_xxx GROUP BY region ORDER BY total_sales DESC LIMIT 1000
  ↓ DuckDB 执行
结果: [{region: "华东", total_sales: 123456}, ...]
```

### AgentInsight 中的实现链路

1. `data_agent` 拼 prompt（表名 + schema + 用户问题）
2. LLM 生成 SQL（有 Redis 缓存，相同问题不重复调）
3. `sql_tool.guard` 安全校验（只读 SELECT、黑名单词、LIMIT 规范化）
4. DuckDB 执行（30s 超时）
5. 执行失败时把错误喂回 LLM 重试修正（最多 2 次）

---

## 七、关键公式速查

| 公式 | 含义 |
|------|------|
| `TF(t) = 该词出现次数 / 文档总词数` | 词在文档中的频率 |
| `IDF(t) = log(N / (df(t) + 1)) + 1` | 词的稀有度 |
| `TF-IDF = TF × IDF` | 词的综合权重 |
| `cos(A,B) = (A·B) / (|A|×|B|)` | 向量相似度 |
| `覆盖率 = |A∩B| / |B|` | 集合覆盖比例 |

---

---

# 技术栈详解

## 八、Redis 详解

### 是什么

**Redis**（Remote Dictionary Server）是一个开源的**内存键值数据库**，数据存储在内存中，读写速度极快（微秒级）。

### 核心特点

| 特点 | 说明 |
|------|------|
| **内存存储** | 数据在内存中，读写 ~0.1ms |
| **键值对** | 支持 String/Hash/List/Set/ZSet 等数据结构 |
| **持久化** | 可选 RDB 快照 / AOF 日志落盘 |
| **过期时间** | 支持 TTL，自动清理 |
| **原子操作** | SET NX、INCR 等天然原子 |
| **发布订阅** | 支持 Pub/Sub 消息通知 |

### Redis 数据结构

| 结构 | 命令 | 用途 |
|------|------|------|
| **String** | `SET/GET` | 缓存、计数器 |
| **Hash** | `HSET/HGET` | 对象存储 |
| **List** | `LPUSH/LRANGE` | 队列、最新N条 |
| **Set** | `SADD/SMEMBERS` | 去重、标签 |
| **ZSet** | `ZADD/ZRANGE` | 排行榜、限流 |

### 在 AgentInsight 中的 4 大用途

#### 1. 缓存（Cache-Aside 模式）

```python
# 简历解析缓存：相同文件不重复解析
async def parse_resume(path):
    key = f"cache:resume:{hash_file(path)}"
    
    # 1. 先查缓存
    cached = await redis.get(key)
    if cached:
        return json.loads(cached)  # HIT，直接返回
    
    # 2. 缓存未命中，执行解析
    profile = await llm_parse(path)
    
    # 3. 写入缓存（TTL 24小时）
    await redis.set(key, json.dumps(profile), ex=86400)
    return profile
```

**实测效果**：第二次相同简历，延迟从 21s 降到 8s（2.4x 提速），LLM 调用 0 次。

#### 2. 幂等锁（防止重复提交）

```python
# 用户快速点击两次"提交"，只执行一次
async def create_task(body):
    lock_key = f"agent:lock:{hash(body)}"
    
    # SET NX = 不存在才设置（原子操作）
    ok = await redis.set(lock_key, task_id, nx=True, ex=300)
    
    if not ok:
        # 锁已存在，说明任务正在执行
        existing = await redis.get(lock_key)
        return 409, {"detail": "任务正在执行中", "task_id": existing}
    
    # 获得锁，执行任务
    try:
        await run_task(task_id)
    finally:
        await redis.delete(lock_key)  # 释放锁
```

#### 3. 分布式限流

```python
# 滑动窗口限流：每分钟最多 5 次登录
async def rate_limit(user_id):
    key = f"ratelimit:login:{user_id}"
    now = time.time() * 1000
    window = 60 * 1000  # 1分钟
    
    # ZSet 实现滑动窗口
    pipe = redis.pipeline()
    pipe.zremrangebyscore(key, 0, now - window)  # 清理过期
    pipe.zcard(key)  # 统计窗口内次数
    _, count = await pipe.execute()
    
    if count >= 5:
        return False  # 限流
    
    pipe.zadd(key, {str(now): now})
    pipe.expire(key, 61)
    await pipe.execute()
    return True
```

#### 4. NL2SQL 结果缓存

```python
# 相同问题不重复调 LLM
cache_key = f"cache:nl2sql:{hash(dataset_id, schema, query)}"

cached = await redis.get(cache_key)
if cached:
    sql = cached["sql"]  # 直接用缓存的 SQL
else:
    sql = await llm.generate(prompt)  # 调 LLM
    await redis.set(cache_key, {"sql": sql}, ex=3600)
```

### 降级策略

```python
async def get_redis():
    """Redis 单例：失败后进入冷却期，不拖慢主链路"""
    global _client, _last_fail_ts
    
    # 冷却期内直接返回 None
    if time.monotonic() - _last_fail_ts < 5.0:
        return None
    
    try:
        client = aioredis.from_url(settings.REDIS_URL)
        await client.ping()
        return client
    except Exception:
        _last_fail_ts = time.monotonic()
        return None  # 降级为 None

# 调用方：Redis 不可用时走降级逻辑
async def get_cached_resume(key):
    client = await get_redis()
    if client is None:
        return None  # 降级：当作缓存未命中
    return await client.get(key)
```

### Redis vs MySQL

| | Redis | MySQL |
|---|---|---|
| 存储位置 | 内存 | 磁盘 |
| 速度 | ~0.1ms | ~10ms |
| 数据结构 | 键值对 | 表/行/列 |
| 持久化 | 可选 | 必须 |
| 适用场景 | 缓存、锁、计数 | 持久存储、事务 |
| 数据丢失 | 重启可能丢 | 不会丢 |

---

## 九、Tailwind CSS 3.x（原子化 CSS）

### 是什么

用预定义的原子类组合样式，不用写自定义 CSS。

### 传统 CSS vs Tailwind

```css
/* 传统写法 */
.error-bar {
  margin-top: 12px;
  padding: 12px 16px;
  border: 1px solid rgba(239, 68, 68, 0.5);
  background-color: rgba(239, 68, 68, 0.1);
  color: #fca5a5;
  border-radius: 8px;
  font-size: 14px;
}
```

```html
<!-- Tailwind 写法 -->
<div className="mt-3 px-4 py-3 border border-red-500/50 bg-red-500/10 text-red-200 rounded-lg text-sm">
```

### 常用类速查

| 类名 | 含义 |
|------|------|
| `mt-3` | margin-top: 12px |
| `px-4` | padding 左右: 16px |
| `text-sm` | font-size: 14px |
| `rounded-lg` | border-radius: 8px |
| `bg-red-500/10` | 背景色红色 10% 透明度 |
| `flex items-center` | Flexbox 垂直居中 |
| `grid grid-cols-3` | 3 列网格 |

### 优势

- 不用切换文件（HTML 里直接写类名）
- 类名即文档（看类名就知道样式）
- 生产环境自动去除未使用的类（bundle 更小）

---

## 十、ECharts 5.x（按需引入）

### 是什么

百度开源的图表库，支持柱状图、折线图、饼图等。

### 按需引入（优化 bundle）

```typescript
// ❌ 全量引入（~1MB）
import * as echarts from "echarts";

// ✅ 按需引入（~300KB）
import * as echarts from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";

echarts.use([BarChart, LineChart, GridComponent, TooltipComponent, CanvasRenderer]);
```

### 使用示例

```typescript
const chart = echarts.init(domElement);
chart.setOption({
  xAxis: { type: "category", data: ["华东", "华南", "华北"] },
  yAxis: { type: "value" },
  series: [{ type: "bar", data: [120, 200, 150] }]
});
```

---

## 十一、PyJWT 2.8（JWT 令牌）

### 是什么

JSON Web Token，无状态的认证令牌。

### 结构

`Header.Payload.Signature`

```
eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoiMTIzIiwiZXhwIjoxNzAwMDAwMDAwfQ.abc123...
│                     │                              │
│                     │                              └─ 签名（防篡改）
│                     └─ 载荷（用户ID、过期时间）
└─ 头部（算法类型）
```

### 代码示例

```python
import jwt

# 生成令牌
token = jwt.encode(
    {"user_id": "123", "exp": time.time() + 7*24*3600},  # 7天过期
    secret_key,
    algorithm="HS256"
)

# 验证令牌
payload = jwt.decode(token, secret_key, algorithms=["HS256"])
user_id = payload["user_id"]
```

### 使用流程

```
登录 → 服务器返回 JWT → 前端存 localStorage
    ↓
每次请求带 Authorization: Bearer <token>
    ↓
服务器验证签名 + 过期时间 → 返回数据
```

---

## 十二、cryptography 42.x（Fernet 加密）

### 是什么

对称加密库，用于加密存储敏感数据（如 LLM API Key）。

### Fernet 特点

加密后不可逆、自带时间戳防重放。

### 代码示例

```python
from cryptography.fernet import Fernet

# 生成密钥（APP_SECRET）
key = Fernet.generate_key()
f = Fernet(key)

# 加密
encrypted = f.encrypt(b"sk-abc123...")  # API Key
# 输出: gAAAAABl...（base64）

# 解密
decrypted = f.decrypt(encrypted)
# 输出: b"sk-abc123..."
```

### 在本项目中

```python
# 存储时加密
api_key_enc = encrypt_secret("sk-abc123")

# 读取时解密
api_key = decrypt_secret(api_key_enc)

# 展示时脱敏
display = f"***{api_key[-4:]}"  # ***c123
```

---

## 十三、EventSource（SSE 实时通信）

### 是什么

Server-Sent Events，服务器向客户端单向推送事件。

### 与 WebSocket 对比

| | SSE | WebSocket |
|---|---|---|
| 方向 | 服务器 → 客户端 | 双向 |
| 协议 | HTTP | 独立协议 |
| 自动重连 | 内置 | 需手动实现 |
| 复杂度 | 简单 | 较高 |

### 后端实现

```python
@app.get("/api/tasks/{task_id}/events")
async def task_events(task_id: str):
    async def generate():
        async for event in bus.subscribe(task_id):
            yield f"data: {json.dumps(event)}\n\n"
        yield "event: done\ndata: {}\n\n"
    
    return StreamingResponse(generate(), media_type="text/event-stream")
```

### 前端接收

```typescript
const es = new EventSource(`/api/tasks/${taskId}/events`);

es.onmessage = (ev) => {
  const data = JSON.parse(ev.data);
  if (data.type === "final") {
    // 最终结果
    es.close();
  } else {
    // 中间事件（agent_start/sql/retry...）
    updateTimeline(data);
  }
};

es.onerror = () => {
  // 自动重连（指数退避，最多3次）
};
```

### 事件类型

```
plan        → 执行计划
agent_start → Agent 开始
sql         → 生成的 SQL
agent_end   → Agent 结束（含耗时）
cache       → 缓存命中/未命中
retry       → 重试
error       → 错误（terminal=true 表示终态）
final       → 最终结果
```

---

## 技术栈总结

| 技术 | 一句话 |
|------|--------|
| **Redis** | 内存缓存，加速重复查询 |
| **Tailwind** | 原子类拼样式，不用写 CSS |
| **ECharts** | 图表库，按需引入减 bundle |
| **PyJWT** | 无状态登录令牌 |
| **Fernet** | 加密存 API Key |
| **SSE** | 服务器实时推送执行进度 |

---

---

# V3.0 新增功能详解

## 十四、Few-shot 检索增强

### 是什么

从历史成功 SQL 中检索相似问题，作为 LLM 的示例（few-shot），提高 SQL 生成准确率。

### 核心流程

```
用户提问 → TF-IDF 向量化 → 检索相似历史 → 注入 prompt → LLM 生成 SQL
                                    ↓
                            SQL 执行成功 → 存入历史库
```

### 中文 Bigram 分词

**问题**：中文没有空格分隔，单字匹配效果差。

**解决**：在单字基础上生成双字词（bigram）。

```python
# 输入: "按地区统计销售额"
# 单字: ["按", "地", "区", "统", "计", "销", "售", "额"]
# Bigram: ["按地", "地区", "区统", "统计", "计销", "销售", "售额"]

def _tokenize(text):
    tokens = re.findall(r'[a-zA-Z]+|[一-鿿]', text.lower())
    bigrams = []
    run = []
    for tok in tokens:
        if '一' <= tok <= '鿿':
            run.append(tok)  # 连续中文字符
        else:
            if len(run) >= 2:
                # join 为字符串保证可哈希（关键修复）
                bigrams.extend(''.join(run[i:i+2]) for i in range(len(run) - 1))
            run = []
    return tokens + bigrams
```

**效果**：「销售额」和「销售总额」能通过 bigram「销售」「售额」匹配上。

### 持久化

```python
# 导出：SQL 成功后异步写入文件
export_history()  # → data/fewshot_history.json

# 导入：进程启动时加载
import_history()  # 文件不存在/损坏返回 0
```

- LRU 上限 500 条
- JSON 格式，UTF-8 编码
- 去重：相同问题覆盖旧 SQL

### data_agent 集成

```python
# 启动时加载历史（仅一次）
if not _fewshot_history_loaded:
    await asyncio.to_thread(import_history)

# SQL 成功后存入历史 + 异步导出
add_to_history(query, sql, explanation)
asyncio.create_task(asyncio.to_thread(export_history))
```

---

## 十五、Context Engineering

### 是什么

控制 LLM 调用的 token 消耗：预算限制 + 上下文压缩 + 智能构建。

### 三大组件

```
┌─────────────────────────────────────────┐
│           Context Engineering            │
├──────────┬──────────┬───────────────────┤
│  Budget  │ Compressor│     Builder       │
│ 预算控制  │  上下文压缩 │   Prompt 构建     │
└──────────┴──────────┴───────────────────┘
```

### Token 预算控制（budget.py）

```python
@dataclass
class TokenBudget:
    max_input_tokens: int = 4000    # 单次 LLM 输入上限
    max_output_tokens: int = 1000   # 单次 LLM 输出上限
    max_context_tokens: int = 8000  # 任务总上下文上限

def estimate_tokens(text: str) -> int:
    """粗略估算：中文 1 字 ≈ 1 token，英文 4 字符 ≈ 1 token"""
    chinese = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    other = len(text) - chinese
    return chinese + other // 4
```

### 上下文压缩（compressor.py）

```python
def compress_resume(profile: dict) -> dict:
    """只保留关键字段，减少 token"""
    return {
        "skills": profile.get("skills", [])[:20],      # 最多 20 个技能
        "projects": [p[:50] for p in profile.get("projects", [])[:5]],  # 最多 5 个项目
        "education": profile.get("education", ""),
        "experience_years": profile.get("experience_years", 0),
    }

def compress_job(job: dict) -> dict:
    """JD 压缩：只保留核心要求"""
    return {
        "title": job.get("title", ""),
        "must_have": job.get("must_have", [])[:10],
        "nice_to_have": job.get("nice_to_have", [])[:5],
        "skills": job.get("skills", [])[:15],
    }
```

### Prompt 构建（builder.py）

```python
def build_match_context(resume: dict, jobs: list[dict]) -> str:
    """构建匹配任务的 LLM prompt"""
    resume_compact = compress_resume(resume)
    jobs_compact = [compress_job(j) for j in jobs[:5]]  # 最多 5 个 JD
    
    context = f"简历画像:\n{resume_compact}\n\n岗位要求:\n"
    for i, job in enumerate(jobs_compact, 1):
        context += f"{i}. {job['title']}\n"
        context += f"   必备: {', '.join(job['must_have'][:5])}\n"
    
    return budget.truncate_input(context)  # 超预算自动截断
```

### Agent 集成

| Agent | 集成点 | 效果 |
|-------|--------|------|
| **ResumeAgent** | 存入 state.results 前压缩 | 减少下游 token |
| **JobAgent** | 存入 state.results 前压缩 | 减少下游 token |
| **MatchAgent** | LLM 调用时用 build_match_context | 控制 prompt 大小 |

---

## 十六、动态规划（MEDIUM 级别）

### 四级复杂度

| 级别 | 判定条件 | 执行链路 |
|------|----------|----------|
| **SIMPLE** | 短问题 + 单聚合词 + 无筛选 | data → 完成 |
| **MEDIUM** | 多聚合词 或 含筛选词 | data → validator → report |
| **NORMAL** | 默认 | data → validator → report |
| **COMPLEX** | 对比/嵌套/多表/环比 | resume∥job → match → validator → report |

### MEDIUM 判定逻辑

```python
# 筛选词特征
_FILTER_PATTERNS = ["筛选", "过滤", "其中", "只看", "只要", "仅", "条件", "限定"]

def assess_complexity(query: str) -> str:
    q = query.lower()
    
    # 1. COMPLEX 优先（对比/嵌套等）
    if any(p in q for p in _COMPLEX_PATTERNS):
        return COMPLEX
    
    # 2. 统计聚合词数量
    agg_count = sum(1 for p in _SIMPLE_PATTERNS if p in q)
    has_filter = any(p in q for p in _FILTER_PATTERNS)
    
    # 3. SIMPLE: 短问题 + 单聚合 + 无筛选
    if len(query) < 20 and agg_count == 1 and not has_filter:
        return SIMPLE
    
    # 4. MEDIUM: 多聚合 或 有筛选
    if agg_count >= 2 or has_filter:
        return MEDIUM
    
    return NORMAL
```

### 示例

| 问题 | 级别 | 原因 |
|------|------|------|
| "统计总销售额" | SIMPLE | 短 + 单聚合「总」 |
| "按地区统计销售额和销量" | MEDIUM | 两个聚合词 |
| "只看北京的销售额" | MEDIUM | 含筛选词「只看」 |
| "对比北京和上海的差异" | COMPLEX | 含「对比」 |

---

## 十七、Validator 增强

### 三层校验

```
┌─────────────────────────────────────┐
│          ValidatorAgent              │
├─────────────────────────────────────┤
│  1. 报告格式校验（report_synthesizer）│
│  2. 匹配结构校验（match_agent）       │
│  3. 数据结果校验（data_agent）        │
└─────────────────────────────────────┘
```

### 报告格式校验

```python
def _validate_report(result: dict) -> list[str]:
    """校验 report_synthesizer 输出"""
    errors = []
    
    # source 必须是 template 或 llm
    if result.get("source") not in ("template", "llm"):
        errors.append(f"source 非法: {result.get('source')}")
    
    # report 必须是 dict
    report = result.get("report")
    if not isinstance(report, dict):
        errors.append("report 必须为 dict")
        return errors
    
    # title 和 summary 必须非空
    if not report.get("title"):
        errors.append("title 不能为空")
    if not report.get("summary"):
        errors.append("summary 不能为空")
    
    return errors
```

### Score-Dimensions 一致性检查

**原理**：总分应该是各维度加权和（四舍五入）。

```python
# 权重定义（与 match_agent 一致）
_MATCH_WEIGHTS = {
    "skill": 0.5, "project": 0.2,
    "experience": 0.1, "education": 0.1, "engineering": 0.1
}

def _validate_score_dimensions_consistency(score: int, dimensions: dict) -> list[str]:
    """校验 score ≈ round(Σ dimensions[k] × weight)"""
    errors = []
    
    # 计算期望分数
    expected = round(sum(
        dimensions[k] * w for k, w in _MATCH_WEIGHTS.items()
    ))
    
    # 允许 ±1 的舍入误差
    if abs(score - expected) > 1:
        errors.append(
            f"score({score}) 与 dimensions 加权和({expected}) 不一致"
        )
    
    return errors
```

**示例**：

| dimensions | 期望 score | 实际 score | 结果 |
|------------|-----------|-----------|------|
| skill=80, project=60, exp=50, edu=80, eng=40 | 69 | 69 | ✓ |
| skill=80, project=60, exp=50, edu=80, eng=40 | 69 | 50 | ✗ 不一致 |

### 三分支路由

```python
async def run(self, state, emit):
    results = state.results
    
    # 优先级：report > match > data
    if "report_synthesizer" in results:
        errors = _validate_report(results["report_synthesizer"])
        # 同时校验 match（如果存在）
        if "match_agent" in results:
            errors += _validate_match(results["match_agent"])
    elif "match_agent" in results:
        errors = _validate_match(results["match_agent"])
    else:
        errors = _validate_data(results.get("data_agent"))
    
    return AgentResult(status="ok" if not errors else "error", ...)
```

---

## V3.0 功能总结

| 功能 | 核心价值 | 关键指标 |
|------|----------|----------|
| **Few-shot 检索** | 提高 SQL 生成准确率 | 相似问题命中率 |
| **Context Engineering** | 控制 token 成本 | 压缩率 30-50% |
| **动态规划** | 简单问题快速响应 | SIMPLE 延迟 -50% |
| **Validator 增强** | 保证输出质量 | 一致性检查通过率 |

---

*笔记更新时间：2026-03-11*
*来源项目：AgentInsight V3.0*
