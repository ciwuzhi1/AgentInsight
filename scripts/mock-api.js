/* 轻量 Mock 后端：8100，无需真实 LLM/MySQL。结果由预置答案直接返回。 */
const http = require("http");
const { randomUUID } = require("crypto");

const PORT = 8100;
const tasks = new Map();

const SAMPLE_FINAL = {
  engine: "duckdb",
  sql: "SELECT city, COUNT(*) AS cnt, ROUND(AVG(salary_k),1) AS avg_salary FROM dataset GROUP BY city ORDER BY cnt DESC",
  explanation: "按城市聚合岗位数量与平均薪资（Mock 直答）",
  columns: ["city", "cnt", "avg_salary"],
  rows: [
    ["Beijing", 5, 22.6],
    ["Shanghai", 3, 22.7],
    ["Shenzhen", 1, 35.0],
    ["Guangzhou", 1, 24.0],
  ],
  chart: {
    type: "bar",
    x_field: "city",
    y_fields: ["cnt", "avg_salary"],
  },
  conclusion: "北京岗位最多（5），深圳平均薪资最高（35k）。",
  elapsed_ms: 320,
  row_count: 4,
  truncated: false,
  task_id: "mock-analysis",
  query: "各城市岗位数量和平均薪资",
};

const MATCH_FINAL = {
  engine: "multi_agent",
  score: 86,
  elapsed_ms: 880,
  task_id: "mock-match",
  query: "resume-match",
  skill_gap: ["Kubernetes", "系统设计"],
  dimensions: [
    { name: "技能匹配", score: 90 },
    { name: "经验匹配", score: 82 },
    { name: "学历匹配", score: 88 },
    { name: "城市偏好", score: 80 },
    { name: "综合潜力", score: 90 },
  ],
  interpretation: "简历与 Python 数据岗高度匹配，建议补强 K8s 与系统设计（Mock）。",
  interpretation_source: "mock",
  resume: { resume_id: "mock-resume", filename: "mock_resume.pdf" },
  jobs: [
    { id: 1, title: "Python 开发", company: "Acme", location: "北京" },
    { id: 2, title: "数据分析师", company: "Globex", location: "上海" },
  ],
};

function json(res, code, body) {
  const data = JSON.stringify(body);
  res.writeHead(code, {
    "Content-Type": "application/json; charset=utf-8",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "*",
    "Access-Control-Allow-Methods": "*",
  });
  res.end(data);
}

function readBody(req) {
  return new Promise((resolve) => {
    let raw = "";
    req.on("data", (c) => (raw += c));
    req.on("end", () => {
      try {
        resolve(raw ? JSON.parse(raw) : {});
      } catch {
        resolve({});
      }
    });
  });
}

function emitTask(taskId, kind) {
  const t = tasks.get(taskId);
  if (!t) return;
  const steps =
    kind === "match"
      ? [
          ["planner", "ok"],
          ["resume_agent", "ok"],
          ["job_agent", "ok"],
          ["match_agent", "ok"],
        ]
      : [
          ["data_agent", "ok"],
          ["validator", "ok"],
          ["report", "ok"],
        ];
  let i = 0;
  const timer = setInterval(() => {
    if (i >= steps.length) {
      clearInterval(timer);
      t.final = kind === "match" ? MATCH_FINAL : SAMPLE_FINAL;
      t.status = "completed";
      return;
    }
    const [agent, status] = steps[i++];
    t.events.push({ type: "agent_start", agent, step: agent });
    t.events.push({
      type: "agent_end",
      agent,
      step: agent,
      status,
      latency_ms: 40 + i * 10,
      detail: {},
    });
  }, 80);
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${PORT}`);
  const path = url.pathname;
  const method = req.method;

  if (method === "OPTIONS") {
    res.writeHead(204, {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Headers": "*",
      "Access-Control-Allow-Methods": "*",
    });
    return res.end();
  }

  if (path === "/api/health" || path === "/api/health/live") {
    return json(res, 200, { status: "ok", mock: true });
  }

  if (path === "/api/datasets" && method === "POST") {
    return json(res, 200, {
      dataset_id: randomUUID(),
      rows_estimate: 10,
      size_mb: 0.02,
      engine_hint: "duckdb",
      schema: [
        { name: "job_id", type: "INTEGER" },
        { name: "title", type: "VARCHAR" },
        { name: "company", type: "VARCHAR" },
        { name: "city", type: "VARCHAR" },
        { name: "salary_k", type: "DOUBLE" },
        { name: "skills", type: "VARCHAR" },
        { name: "posted_date", type: "DATE" },
      ],
    });
  }

  if (path === "/api/tasks" && method === "POST") {
    const body = await readBody(req);
    const id = randomUUID();
    tasks.set(id, {
      id,
      query: body.query || "",
      events: [],
      final: null,
      status: "running",
      created_at: new Date().toISOString(),
    });
    emitTask(id, "analysis");
    return json(res, 200, { task_id: id });
  }

  if (path === "/api/tasks" && method === "GET") {
    const items = [...tasks.values()]
      .slice(-20)
      .reverse()
      .map((t) => ({
        id: t.id,
        query: t.query,
        status: t.status,
        engine: t.final?.engine,
        score: t.final?.score,
        created_at: t.created_at,
      }));
    return json(res, 200, { items });
  }

  if (path.startsWith("/api/tasks/") && path.endsWith("/events")) {
    const id = path.split("/")[3];
    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "Access-Control-Allow-Origin": "*",
    });
    const t = tasks.get(id);
    if (!t) {
      res.write(`data: ${JSON.stringify({ type: "error", message: "not found", terminal: true })}\n\n`);
      return res.end();
    }
    let idx = 0;
    const iv = setInterval(() => {
      if (idx < t.events.length) {
        res.write(`data: ${JSON.stringify(t.events[idx++])}\n\n`);
      } else if (t.final) {
        res.write(`data: ${JSON.stringify({ type: "final", result: t.final })}\n\n`);
        clearInterval(iv);
        res.end();
      }
    }, 50);
    req.on("close", () => clearInterval(iv));
    return;
  }

  if (path.match(/^\/api\/tasks\/[^/]+\/trace$/)) {
    const id = path.split("/")[3];
    const t = tasks.get(id);
    if (!t) return json(res, 404, { detail: "not found" });
    return json(res, 200, {
      steps: t.events
        .filter((e) => e.type === "agent_end")
        .map((e) => ({
          agent_name: e.agent,
          status: e.status,
          latency_ms: e.latency_ms,
          detail: e.detail,
        })),
      final_result: t.final,
    });
  }

  if (path === "/api/matches" && method === "POST") {
    const id = randomUUID();
    tasks.set(id, {
      id,
      query: "resume-match",
      events: [],
      final: null,
      status: "running",
      created_at: new Date().toISOString(),
    });
    emitTask(id, "match");
    return json(res, 200, { task_id: id });
  }

  if (path === "/api/resumes" && method === "POST") {
    return json(res, 200, {
      resume_id: randomUUID(),
      filename: "mock_resume.pdf",
    });
  }

  if (path === "/api/crawler/jobs") {
    return json(res, 200, {
      items: [
        { id: 1, title: "Python 开发", company: "Acme", location: "北京", skills: ["Python", "SQL"] },
        { id: 2, title: "数据分析师", company: "Globex", location: "上海", skills: ["SQL"] },
        { id: 3, title: "后端工程师", company: "Initech", location: "北京", skills: ["Go", "Redis"] },
      ],
    });
  }

  if (path === "/api/crawler/run" && method === "POST") {
    return json(res, 200, {
      inserted: 3,
      skipped: 1,
      items: [
        { title: "Python 开发", company: "Acme", location: "北京", skills: ["Python", "SQL"] },
        { title: "数据分析师", company: "Globex", location: "上海", skills: ["SQL", "Excel"] },
        { title: "后端工程师", company: "Initech", location: "北京", skills: ["Go"] },
      ],
    });
  }

  if (path === "/api/crawler/export" && method === "POST") {
    return json(res, 200, { path: "data/large/jd_crawled.csv", rows: 3 });
  }

  if (path.match(/^\/api\/tasks\/[^/]+\/export$/)) {
    res.writeHead(200, {
      "Content-Type": "text/csv; charset=utf-8",
      "Access-Control-Allow-Origin": "*",
    });
    return res.end("city,cnt,avg_salary\nBeijing,5,22.6\nShanghai,3,22.7\n");
  }

  if (path === "/api/settings" && method === "GET") {
    return json(res, 200, {
      match_llm_enabled: "false",
      llm_fallback_mock: "auto",
      parser_backend: "pymupdf",
      sql_timeout: "30",
    });
  }

  if (path === "/api/settings" && method === "PUT") {
    return json(res, 200, { ok: true });
  }

  if (path === "/api/models" && method === "GET") {
    return json(res, 200, { items: [] });
  }

  if (path === "/api/models" && method === "POST") {
    return json(res, 200, { id: randomUUID(), ...((await readBody(req)) || {}) });
  }

  if (path.match(/^\/api\/models\/[^/]+\/activate$/) && method === "PUT") {
    return json(res, 200, { ok: true });
  }

  if (path.match(/^\/api\/models\/[^/]+\/test$/) && method === "POST") {
    return json(res, 200, { ok: true, latency_ms: 12, message: "mock ok" });
  }

  if (path.match(/^\/api\/models\/[^/]+$/) && method === "DELETE") {
    return json(res, 200, { ok: true });
  }

  if (path === "/api/auth/login" && method === "POST") {
    return json(res, 200, { token: "mock-token", username: "tester" });
  }

  json(res, 404, { detail: `no mock for ${method} ${path}` });
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`Mock API on http://127.0.0.1:${PORT}`);
});
