"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ensureAuthToken, forceRefreshAuth, installAuthFetch } from "../auth-client";

installAuthFetch();

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8100";

/* ---------- 类型 ---------- */

type ModelConfig = {
  id: string;
  name: string;
  provider: string;
  base_url: string;
  api_key?: string;
  api_key_masked?: string;
  model: string;
  temperature: number;
  is_active: boolean;
};

type SettingMeta = {
  key: string;
  label: string;
  kind: "select" | "text" | "number" | "secret";
  options?: string[];
};

const SETTING_META: SettingMeta[] = [
  { key: "match_llm_enabled", label: "匹配 LLM 解读", kind: "select", options: ["true", "false"] },
  { key: "llm_fallback_mock", label: "LLM 兜底策略", kind: "select", options: ["auto", "never"] },
  { key: "parser_backend", label: "PDF 解析后端", kind: "select", options: ["mineru_api", "pymupdf"] },
  { key: "sql_timeout", label: "SQL 超时（秒）", kind: "number" },
  { key: "mineru_api_token", label: "MinerU Token", kind: "secret" },
  { key: "tavily_api_key", label: "Tavily API Key", kind: "secret" },
];

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

async function readError(res: Response): Promise<Error> {
  const detail = await res.json().catch(() => ({}));
  const d = (detail as { detail?: string }).detail;
  return new Error(d ?? `HTTP ${res.status}`);
}

/* ---------- 小组件 ---------- */

function ErrorBar({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="mt-3 rounded-lg border px-4 py-3 text-sm"
      style={{
        borderColor: "var(--status-error-border)",
        background: "var(--status-error-bg)",
        color: "var(--status-error)",
      }}
    >
      出错了：{message}
    </div>
  );
}

function OkBar({ message }: { message: string }) {
  return (
    <div
      role="status"
      className="mt-3 rounded-lg border px-4 py-3 text-sm"
      style={{
        borderColor: "var(--status-ok-border)",
        background: "var(--status-ok-bg)",
        color: "var(--status-ok)",
      }}
    >
      {message}
    </div>
  );
}

function Badge({
  children,
  tone = "slate",
}: {
  children: React.ReactNode;
  tone?: "slate" | "green" | "red" | "amber" | "sky" | "violet";
}) {
  const tones: Record<string, React.CSSProperties> = {
    slate: {
      borderColor: "var(--border-default)",
      background: "var(--bg-inset)",
      color: "var(--text-secondary)",
    },
    green: {
      borderColor: "var(--status-ok-border)",
      background: "var(--status-ok-bg)",
      color: "var(--status-ok)",
    },
    red: {
      borderColor: "var(--status-error-border)",
      background: "var(--status-error-bg)",
      color: "var(--status-error)",
    },
    amber: {
      borderColor: "var(--status-running-border)",
      background: "var(--status-running-bg)",
      color: "var(--status-running)",
    },
    sky: {
      borderColor: "var(--accent-sky-border)",
      background: "var(--accent-sky-bg)",
      color: "var(--accent-sky)",
    },
    violet: {
      borderColor: "var(--accent-violet)",
      background: "color-mix(in srgb, var(--accent-violet) 10%, transparent)",
      color: "var(--accent-violet)",
    },
  };
  return (
    <span
      className="inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium"
      style={tones[tone]}
    >
      {children}
    </span>
  );
}

/* 设置页板块外层卡片：与主页视觉统一（小图标 + 标题 + 一句说明） */
function SettingsCard({
  icon,
  title,
  desc,
  children,
}: {
  icon: string;
  title: string;
  desc: string;
  children: React.ReactNode;
}) {
  return (
    <section className="glass p-5 shadow-lg shadow-black/20 sm:p-6">
      <div
        className="mb-5 flex items-start gap-3 border-b pb-4"
        style={{ borderColor: "var(--border-glass)" }}
      >
        <span
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-xl"
          style={{ background: "var(--primary)", opacity: 0.12 }}
        >
          {icon}
        </span>
        <div>
          <h2 className="text-lg font-semibold text-[var(--text-primary)]">{title}</h2>
          <p className="mt-0.5 text-sm text-[var(--text-secondary)]">{desc}</p>
        </div>
      </div>
      {children}
    </section>
  );
}

/* ---------- 设置中心（自主页整体迁移，功能逻辑不变） ---------- */

export default function SettingsPage() {
  const [models, setModels] = useState<ModelConfig[]>([]);
  const [settings, setSettings] = useState<Record<string, string>>({});
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  const [error, setError] = useState<string | null>(null);
  const [okMsg, setOkMsg] = useState<string | null>(null);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, string>>({});
  const [form, setForm] = useState({
    name: "",
    provider: "openai_compatible",
    base_url: "",
    api_key: "",
    model: "",
    temperature: "0.3",
  });

  async function loadAll(opts?: { preserveOk?: boolean }) {
    setBusy(true);
    setError(null);
    if (!opts?.preserveOk) setOkMsg(null);
    try {
      // 先确保 Bearer（登录页已移除，自动注册/登录）
      await ensureAuthToken();
      const [mRes, sRes] = await Promise.all([
        fetch(`${API_BASE}/api/models`),
        fetch(`${API_BASE}/api/settings`),
      ]);
      if (mRes.status === 401 || sRes.status === 401) {
        await forceRefreshAuth();
        const [m2, s2] = await Promise.all([
          fetch(`${API_BASE}/api/models`),
          fetch(`${API_BASE}/api/settings`),
        ]);
        if (m2.status === 401) {
          throw new Error("登录失效：自动重连失败，请刷新页面");
        }
        const m = (await m2.json()) as unknown;
        const s = (await s2.json()) as Record<string, string>;
        const list = Array.isArray(m)
          ? m
          : ((m as { items?: ModelConfig[] }).items ?? []);
        setModels(list as ModelConfig[]);
        setSettings(s ?? {});
        setDraft({});
        return;
      }
      if (!mRes.ok) throw new Error(`加载模型列表失败（HTTP ${mRes.status}）`);
      if (!sRes.ok) throw new Error(`加载设置失败（HTTP ${sRes.status}）`);
      const m = (await mRes.json()) as unknown;
      const s = (await sRes.json()) as Record<string, string>;
      const list = Array.isArray(m)
        ? m
        : ((m as { items?: ModelConfig[] }).items ?? []);
      setModels(list as ModelConfig[]);
      setSettings(s ?? {});
      setDraft({});
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  // 进入页面即加载（原主页折叠面板是展开时加载）
  useEffect(() => {
    loadAll();
  }, []);

  function resetForm() {
    setForm({
      name: "",
      provider: "openai_compatible",
      base_url: "",
      api_key: "",
      model: "",
      temperature: "0.3",
    });
  }

  async function addModel() {
    if (
      !form.name.trim() ||
      !form.base_url.trim() ||
      !form.api_key.trim() ||
      !form.model.trim()
    ) {
      setError("请填写完整的模型配置（名称 / Base URL / API Key / 模型名）");
      return;
    }
    setBusy(true);
    setError(null);
    setOkMsg(null);
    try {
      await ensureAuthToken();
      const res = await fetch(`${API_BASE}/api/models`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: form.name.trim(),
          provider: form.provider,
          base_url: form.base_url.trim(),
          api_key: form.api_key.trim(),
          model: form.model.trim(),
          temperature: Number(form.temperature) || 0,
        }),
      });
      if (!res.ok) throw await readError(res);
      const addedName = form.name.trim();
      resetForm();
      setOkMsg(`模型「${addedName}」已添加，列表已刷新`);
      await loadAll({ preserveOk: true });
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  async function activateModel(id: string) {
    setBusy(true);
    setError(null);
    setOkMsg(null);
    try {
      await ensureAuthToken();
      const res = await fetch(`${API_BASE}/api/models/${id}/activate`, {
        method: "PUT",
      });
      if (!res.ok) throw await readError(res);
      setOkMsg("已切换激活模型");
      await loadAll({ preserveOk: true });
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  async function testModel(id: string) {
    setTestingId(id);
    setError(null);
    setTestResult((prev) => ({ ...prev, [id]: "测试中…" }));
    try {
      await ensureAuthToken();
      const res = await fetch(`${API_BASE}/api/models/${id}/test`, {
        method: "POST",
      });
      if (!res.ok) throw await readError(res);
      const data = (await res.json()) as {
        ok: boolean;
        latency_ms?: number;
        error?: string;
      };
      setTestResult((prev) => ({
        ...prev,
        [id]: data.ok
          ? `连通正常 · ${data.latency_ms ?? "?"} ms`
          : `失败：${data.error ?? "未知错误"}`,
      }));
    } catch (e) {
      setTestResult((prev) => ({ ...prev, [id]: `失败：${errText(e)}` }));
    } finally {
      setTestingId(null);
    }
  }

  async function deleteModel(id: string) {
    if (!window.confirm("确定删除该模型配置吗？")) return;
    setBusy(true);
    setError(null);
    setOkMsg(null);
    try {
      const res = await fetch(`${API_BASE}/api/models/${id}`, {
        method: "DELETE",
      });
      if (!res.ok) throw await readError(res);
      setOkMsg("模型配置已删除");
      await loadAll({ preserveOk: true });
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  async function saveSetting(meta: SettingMeta) {
    setError(null);
    setOkMsg(null);
    const typed = draft[meta.key];
    if (meta.kind === "secret" && !typed) {
      setError(`请先输入「${meta.label}」的新值（已配置的密钥不会回显明文）`);
      return;
    }
    const value = typed ?? settings[meta.key] ?? "";
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/api/settings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: meta.key, value }),
      });
      if (!res.ok) throw await readError(res);
      setOkMsg(`已保存「${meta.label}」`);
      setDraft((prev) => ({ ...prev, [meta.key]: "" }));
      await loadAll({ preserveOk: true });
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  const inputCls =
    "rounded-lg border border-[var(--border-strong)] bg-[var(--bg-inset)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:border-[var(--primary)] focus:outline-none";

  return (
    <div>
      {/* 吸顶返回导航条 */}
      <nav
        className="sticky top-0 z-40 -mx-4 mb-8 border-b px-4 py-3 backdrop-blur"
        style={{
          borderColor: "var(--border-glass)",
          background: "var(--bg-glass-strong)",
        }}
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Link
            href="/"
            className="inline-flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-sm text-[var(--text-secondary)] transition hover:bg-[var(--bg-inset)] hover:text-[var(--primary-light)]"
          >
            ← 返回分析台
          </Link>
          <span className="text-sm font-semibold text-[var(--text-primary)]">
            ⚙ 设置中心
          </span>
        </div>
      </nav>

      <header className="pb-8">
        <h1 className="text-3xl font-bold tracking-tight text-[var(--text-primary)]">
          设置<span className="text-gradient-brand">中心</span>
        </h1>
        <p className="mt-2 text-sm text-[var(--text-secondary)]">
          模型配置与功能开关，保存后立即生效（后端有 10 秒缓存）。后端：
          <span className="font-mono text-[var(--text-muted)]">{API_BASE}</span>
        </p>
      </header>

      <div className="space-y-8">
        {(busy || error || okMsg) && (
          <div>
            {busy && !error && !okMsg && (
              <p className="animate-pulse py-6 text-center text-sm text-[var(--text-muted)]">
                {models.length === 0 ? "正在加载设置…" : "正在处理…"}
              </p>
            )}
            {error && <ErrorBar message={error} />}
            {okMsg && !error && <OkBar message={okMsg} />}
          </div>
        )}

        {/* 模型列表 */}
        <SettingsCard
          icon="🧠"
          title="模型配置"
          desc="已接入的模型列表，可激活、测试连通或删除"
        >
          {models.length === 0 ? (
            <p
              className="rounded-lg border border-dashed py-6 text-center text-sm text-[var(--text-muted)]"
              style={{ borderColor: "var(--border-default)" }}
            >
              暂无模型配置（当前使用后端 .env 的默认模型）
            </p>
          ) : (
            <ul className="space-y-3">
              {models.map((m) => (
                <li
                  key={m.id}
                  className="rounded-lg border border-[var(--border-default)] bg-[var(--bg-inset)] px-4 py-3"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium text-[var(--text-primary)]">
                      {m.name}
                    </span>
                    <Badge tone="sky">{m.provider}</Badge>
                    <Badge tone="violet">{m.model}</Badge>
                    {m.is_active ? (
                      <Badge tone="green">激活中</Badge>
                    ) : (
                      <Badge>未激活</Badge>
                    )}
                    <Badge tone="slate">key: {m.api_key_masked || m.api_key || "—"}</Badge>
                  </div>
                  <p className="mt-1 font-mono text-xs text-[var(--text-muted)]">
                    {m.base_url} · temperature {m.temperature}
                  </p>
                  {testResult[m.id] && (
                    <p
                      className="mt-1 text-xs"
                      style={{
                        color: testResult[m.id].startsWith("连通")
                          ? "var(--status-ok)"
                          : testResult[m.id] === "测试中…"
                            ? "var(--text-secondary)"
                            : "var(--status-error)",
                      }}
                    >
                      测试结果：{testResult[m.id]}
                    </p>
                  )}
                  <div className="mt-2 flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => activateModel(m.id)}
                      disabled={busy || m.is_active}
                      className="btn-ghost rounded-lg border px-3 py-1.5 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-40"
                      style={{
                        borderColor: "var(--status-ok-border)",
                        background: "var(--status-ok-bg)",
                        color: "var(--status-ok)",
                      }}
                    >
                      激活
                    </button>
                    <button
                      type="button"
                      onClick={() => testModel(m.id)}
                      disabled={testingId !== null || busy}
                      className="btn-ghost rounded-lg border px-3 py-1.5 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-40"
                      style={{
                        borderColor: "var(--accent-sky-border)",
                        background: "var(--accent-sky-bg)",
                        color: "var(--accent-sky)",
                      }}
                    >
                      {testingId === m.id ? "测试中…" : "测试连通"}
                    </button>
                    <button
                      type="button"
                      onClick={() => deleteModel(m.id)}
                      disabled={busy}
                      className="btn-ghost rounded-lg border px-3 py-1.5 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-40"
                      style={{
                        borderColor: "var(--status-error-border)",
                        background: "var(--status-error-bg)",
                        color: "var(--status-error)",
                      }}
                    >
                      删除
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </SettingsCard>

        {/* 新增模型表单 */}
        <SettingsCard
          icon="➕"
          title="新增模型"
          desc="OpenAI 兼容接口，填好后保存即可在上方列表看到"
        >
          <div className="grid gap-3 md:grid-cols-2">
            <input
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              placeholder="配置名称，如：DeepSeek 主力"
              aria-label="名称"
              className={inputCls}
            />
            <select
              value={form.provider}
              onChange={(e) => setForm({ ...form, provider: e.target.value })}
              aria-label="Provider"
              className={inputCls}
            >
              <option value="openai_compatible">openai_compatible</option>
              <option value="openai">openai</option>
              <option value="deepseek">deepseek</option>
              <option value="qwen">qwen</option>
              <option value="moonshot">moonshot</option>
            </select>
            <input
              value={form.base_url}
              onChange={(e) => setForm({ ...form, base_url: e.target.value })}
              placeholder="Base URL，如：https://api.deepseek.com/v1"
              aria-label="Base URL"
              className={inputCls}
            />
            <input
              type="password"
              value={form.api_key}
              onChange={(e) => setForm({ ...form, api_key: e.target.value })}
              placeholder="API Key"
              aria-label="API Key"
              className={inputCls}
            />
            <input
              value={form.model}
              onChange={(e) => setForm({ ...form, model: e.target.value })}
              placeholder="模型名，如：deepseek-chat"
              aria-label="模型名"
              className={inputCls}
            />
            <input
              type="number"
              step="0.1"
              min="0"
              max="2"
              value={form.temperature}
              onChange={(e) => setForm({ ...form, temperature: e.target.value })}
              placeholder="temperature"
              aria-label="temperature"
              className={inputCls}
            />
          </div>
          <button
            type="button"
            onClick={addModel}
            disabled={busy}
            className="btn-primary mt-4 px-5 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-50"
          >
            添加模型
          </button>
        </SettingsCard>

        {/* 开关区 */}
        <SettingsCard
          icon="🎚️"
          title="开关与密钥"
          desc="功能开关与密钥管理，密钥留空表示不修改"
        >
          <ul className="space-y-3">
            {SETTING_META.map((meta) => (
              <li
                key={meta.key}
                className="flex flex-wrap items-center gap-3 text-sm"
              >
                <span className="w-40 shrink-0 text-[var(--text-primary)]">{meta.label}</span>
                <span className="font-mono text-xs text-[var(--text-faint)]">
                  {meta.key}
                </span>
                {meta.kind === "select" ? (
                  <select
                    value={draft[meta.key] ?? settings[meta.key] ?? ""}
                    onChange={(e) =>
                      setDraft((prev) => ({ ...prev, [meta.key]: e.target.value }))
                    }
                    aria-label={meta.label}
                    className={inputCls}
                  >
                    {meta.options?.map((o) => (
                      <option key={o} value={o}>
                        {o}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    type={meta.kind === "secret" ? "password" : meta.kind === "number" ? "number" : "text"}
                    value={draft[meta.key] ?? ""}
                    onChange={(e) =>
                      setDraft((prev) => ({ ...prev, [meta.key]: e.target.value }))
                    }
                    placeholder={
                      meta.kind === "secret"
                        ? settings[meta.key]
                          ? "已配置（输入新值覆盖）"
                          : "未配置"
                        : settings[meta.key] || ""
                    }
                    aria-label={meta.label}
                    className={`${inputCls} w-56`}
                  />
                )}
                <button
                  type="button"
                  onClick={() => saveSetting(meta)}
                  disabled={busy}
                  className="btn-ghost rounded-lg border px-3 py-1.5 text-xs font-medium transition disabled:cursor-not-allowed disabled:opacity-40"
                  style={{
                    borderColor: "var(--accent-sky-border)",
                    background: "var(--accent-sky-bg)",
                    color: "var(--accent-sky)",
                  }}
                >
                  保存
                </button>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-[var(--text-muted)]">
            密钥类输入框留空表示不修改；保存后立即生效（后端有 10 秒缓存）。
          </p>
        </SettingsCard>
      </div>

      <footer className="pb-6 pt-10 text-center text-xs text-[var(--text-faint)]">
        AgentInsight MVP · Next.js 15 + React 19 + Tailwind v4 + ECharts 5
      </footer>
    </div>
  );
}
