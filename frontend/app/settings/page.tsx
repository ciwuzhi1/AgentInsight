"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getToken, installAuthFetch } from "../auth-client";

installAuthFetch();

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8100";

/* ---------- 类型 ---------- */

type ModelConfig = {
  id: string;
  name: string;
  provider: string;
  base_url: string;
  api_key: string;
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
    <div className="mt-3 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">
      出错了：{message}
    </div>
  );
}

function OkBar({ message }: { message: string }) {
  return (
    <div className="mt-3 rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-300">
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
  const tones: Record<string, string> = {
    slate: "border-slate-600 bg-slate-800 text-slate-300",
    green: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
    red: "border-red-500/40 bg-red-500/10 text-red-300",
    amber: "border-amber-500/40 bg-amber-500/10 text-amber-300",
    sky: "border-sky-500/40 bg-sky-500/10 text-sky-300",
    violet: "border-violet-500/40 bg-violet-500/10 text-violet-300",
  };
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${tones[tone]}`}
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
    <section className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5 shadow-lg shadow-black/20 sm:p-6">
      <div className="mb-5 flex items-start gap-3 border-b border-slate-800/70 pb-4">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-sky-500/10 text-xl">
          {icon}
        </span>
        <div>
          <h2 className="text-lg font-semibold text-slate-100">{title}</h2>
          <p className="mt-0.5 text-sm text-slate-400">{desc}</p>
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

  useEffect(() => {
    // 未登录直接去登录页（fetch 补丁兜底 401 跳转）
    if (!getToken()) window.location.href = "/login";
  }, []);
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

  async function loadAll() {
    setBusy(true);
    setError(null);
    setOkMsg(null);
    try {
      const [mRes, sRes] = await Promise.all([
        fetch(`${API_BASE}/api/models`),
        fetch(`${API_BASE}/api/settings`),
      ]);
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
      resetForm();
      setOkMsg("模型配置已添加");
      await loadAll();
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
      const res = await fetch(`${API_BASE}/api/models/${id}/activate`, {
        method: "PUT",
      });
      if (!res.ok) throw await readError(res);
      setOkMsg("已切换激活模型");
      await loadAll();
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
      await loadAll();
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
      await loadAll();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(false);
    }
  }

  const inputCls =
    "rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:border-sky-500 focus:outline-none";

  return (
    <div>
      {/* 吸顶返回导航条 */}
      <nav className="sticky top-0 z-40 -mx-4 mb-8 border-b border-slate-800/80 bg-slate-950/70 px-4 py-3 backdrop-blur">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Link
            href="/"
            className="inline-flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-sm text-slate-400 transition hover:bg-slate-800/60 hover:text-sky-300"
          >
            ← 返回分析台
          </Link>
          <span className="text-sm font-semibold text-slate-300">
            ⚙ 设置中心
          </span>
        </div>
      </nav>

      <header className="pb-8">
        <h1 className="text-3xl font-bold tracking-tight text-slate-100">
          设置<span className="bg-gradient-to-r from-sky-400 to-cyan-300 bg-clip-text text-transparent">中心</span>
        </h1>
        <p className="mt-2 text-sm text-slate-400">
          模型配置与功能开关，保存后立即生效（后端有 10 秒缓存）。后端：
          <span className="font-mono text-slate-500">{API_BASE}</span>
        </p>
      </header>

      <div className="space-y-8">
        {(busy || error || okMsg) && (
          <div>
            {busy && models.length === 0 && !error && (
              <p className="animate-pulse py-6 text-center text-sm text-slate-500">
                正在加载设置…
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
            <p className="rounded-lg border border-dashed border-slate-800 py-6 text-center text-sm text-slate-500">
              暂无模型配置（当前使用后端 .env 的默认模型）
            </p>
          ) : (
            <ul className="space-y-3">
              {models.map((m) => (
                <li
                  key={m.id}
                  className="rounded-lg border border-slate-800 bg-slate-800/40 px-4 py-3"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium text-slate-200">
                      {m.name}
                    </span>
                    <Badge tone="sky">{m.provider}</Badge>
                    <Badge tone="violet">{m.model}</Badge>
                    {m.is_active ? (
                      <Badge tone="green">激活中</Badge>
                    ) : (
                      <Badge>未激活</Badge>
                    )}
                    <Badge tone="slate">key: {m.api_key}</Badge>
                  </div>
                  <p className="mt-1 font-mono text-xs text-slate-500">
                    {m.base_url} · temperature {m.temperature}
                  </p>
                  {testResult[m.id] && (
                    <p
                      className={`mt-1 text-xs ${
                        testResult[m.id].startsWith("连通")
                          ? "text-emerald-400"
                          : testResult[m.id] === "测试中…"
                            ? "text-slate-400"
                            : "text-red-400"
                      }`}
                    >
                      测试结果：{testResult[m.id]}
                    </p>
                  )}
                  <div className="mt-2 flex flex-wrap gap-2">
                    <button
                      onClick={() => activateModel(m.id)}
                      disabled={busy || m.is_active}
                      className="rounded-lg border border-emerald-500/50 bg-emerald-500/10 px-3 py-1.5 text-xs font-medium text-emerald-300 transition hover:bg-emerald-500/20 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      激活
                    </button>
                    <button
                      onClick={() => testModel(m.id)}
                      disabled={testingId === m.id}
                      className="rounded-lg border border-sky-500/50 bg-sky-500/10 px-3 py-1.5 text-xs font-medium text-sky-300 transition hover:bg-sky-500/20 disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      {testingId === m.id ? "测试中…" : "测试连通"}
                    </button>
                    <button
                      onClick={() => deleteModel(m.id)}
                      disabled={busy}
                      className="rounded-lg border border-red-500/50 bg-red-500/10 px-3 py-1.5 text-xs font-medium text-red-300 transition hover:bg-red-500/20 disabled:cursor-not-allowed disabled:opacity-40"
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
              className={inputCls}
            />
            <select
              value={form.provider}
              onChange={(e) => setForm({ ...form, provider: e.target.value })}
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
              className={inputCls}
            />
            <input
              type="password"
              value={form.api_key}
              onChange={(e) => setForm({ ...form, api_key: e.target.value })}
              placeholder="API Key"
              className={inputCls}
            />
            <input
              value={form.model}
              onChange={(e) => setForm({ ...form, model: e.target.value })}
              placeholder="模型名，如：deepseek-chat"
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
              className={inputCls}
            />
          </div>
          <button
            onClick={addModel}
            disabled={busy}
            className="mt-4 rounded-lg bg-sky-600 px-5 py-2 text-sm font-medium text-white transition hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-50"
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
                <span className="w-40 shrink-0 text-slate-300">{meta.label}</span>
                <span className="font-mono text-xs text-slate-600">
                  {meta.key}
                </span>
                {meta.kind === "select" ? (
                  <select
                    value={draft[meta.key] ?? settings[meta.key] ?? ""}
                    onChange={(e) =>
                      setDraft((prev) => ({ ...prev, [meta.key]: e.target.value }))
                    }
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
                    className={`${inputCls} w-56`}
                  />
                )}
                <button
                  onClick={() => saveSetting(meta)}
                  disabled={busy}
                  className="rounded-lg border border-sky-500/50 bg-sky-500/10 px-3 py-1.5 text-xs font-medium text-sky-300 transition hover:bg-sky-500/20 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  保存
                </button>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-slate-500">
            密钥类输入框留空表示不修改；保存后立即生效（后端有 10 秒缓存）。
          </p>
        </SettingsCard>
      </div>

      <footer className="pb-6 pt-10 text-center text-xs text-slate-600">
        AgentInsight MVP · Next.js 15 + React 19 + Tailwind v4 + ECharts 5
      </footer>
    </div>
  );
}
