/* 认证助手：无登录页，默认账号自动 register/login；业务请求前等待 token 就绪。 */
"use client";

const TOKEN_KEY = "ai_token";
const USER_KEY = "ai_username";
// 默认演示账密仅从环境变量读取，避免硬编码进源码
const DEFAULT_USER = process.env.NEXT_PUBLIC_DEMO_USER ?? "";
const DEFAULT_PASS = process.env.NEXT_PUBLIC_DEMO_PASS ?? "";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8100";

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

export function getUsername(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(USER_KEY);
}

export function setAuth(token: string, username: string) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, username);
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("agentinsight:auth"));
  }
}

export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("agentinsight:auth"));
  }
}

let installed = false;
let ensurePromise: Promise<string | null> | null = null;
/** 首次模块加载即预取，供 installAuthFetch 排队等待 */
let bootstrapPromise: Promise<string | null> | null = null;
/** 登录限流（5 次/分钟/IP）退避，避免轮询打爆 429 */
let lastFailAt = 0;
let failBackoffMs = 0;

async function rawLogin(username: string, password: string) {
  try {
    const res = await fetch(`${API_BASE}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    if (res.status === 429) {
      lastFailAt = Date.now();
      failBackoffMs = Math.min((failBackoffMs || 3000) * 2, 30000);
      return null;
    }
    if (!res.ok) return null;
    const body = (await res.json().catch(() => ({}))) as {
      token?: string;
      username?: string;
    };
    return body.token ? body : null;
  } catch {
    return null;
  }
}

async function doEnsure(): Promise<string | null> {
  try {
    // 退避窗口内直接跳过，避免限流雪崩
    if (failBackoffMs > 0 && Date.now() - lastFailAt < failBackoffMs) {
      return getToken();
    }
    if (!DEFAULT_USER || !DEFAULT_PASS) {
      throw new Error(
        "未配置演示账号（NEXT_PUBLIC_DEMO_USER / NEXT_PUBLIC_DEMO_PASS），无法自动登录"
      );
    }
    let body = await rawLogin(DEFAULT_USER, DEFAULT_PASS);
    if (!body) {
      try {
        const reg = await fetch(`${API_BASE}/api/auth/register`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            username: DEFAULT_USER,
            password: DEFAULT_PASS,
          }),
        });
        if (reg.status !== 429) {
          body = await rawLogin(DEFAULT_USER, DEFAULT_PASS);
        }
      } catch {
        /* ignore */
      }
    }
    if (body?.token) {
      failBackoffMs = 0;
      setAuth(body.token, body.username ?? DEFAULT_USER);
      return body.token;
    }
    lastFailAt = Date.now();
    if (failBackoffMs === 0) failBackoffMs = 3000;
    console.warn("[auth] 自动登录失败：无法获得 token（可能触发限流，稍后重试）");
    return getToken();
  } catch (e) {
    lastFailAt = Date.now();
    failBackoffMs = Math.min((failBackoffMs || 3000) * 2, 30000);
    console.warn("[auth] 自动登录异常", e);
    return getToken();
  }
}

/**
 * 确保本地有可用 token。
 * force=true 时忽略本地缓存（处理假活/过期 token）。
 */
export function ensureAuthToken(opts?: { force?: boolean }): Promise<string | null> {
  if (typeof window === "undefined") return Promise.resolve(null);
  if (!opts?.force) {
    const existing = getToken();
    if (existing) return Promise.resolve(existing);
  }
  if (ensurePromise) return ensurePromise;

  ensurePromise = (async () => {
    try {
      return await doEnsure();
    } finally {
      ensurePromise = null;
    }
  })();

  return ensurePromise;
}

/** 401 时强制刷新 token（clear 后重新 login） */
export function forceRefreshAuth(): Promise<string | null> {
  clearAuth();
  return ensureAuthToken({ force: true });
}

/** 清除本地登录态并触发 UI 同步（常驻模式下 UI 会再 ensure） */
export function resetAuthLocal() {
  clearAuth();
}

function isAuthUrl(input: string): boolean {
  return /\/api\/auth\/(login|register)/.test(input);
}

function isApiUrl(input: string): boolean {
  return input.includes("/api/");
}

/** 幂等安装：业务 API 请求前等待 token；401 时 force 刷新并重试一次（非 FormData） */
export function installAuthFetch() {
  if (typeof window === "undefined" || installed) return;
  installed = true;

  if (!bootstrapPromise) {
    bootstrapPromise = ensureAuthToken();
  }

  const origFetch = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : (input as Request).url;

    if (isApiUrl(url) && !isAuthUrl(url)) {
      await bootstrapPromise;
      if (!getToken()) await ensureAuthToken();
    }

    const token = getToken();
    const headers = new Headers(init?.headers ?? undefined);
    if (token && !headers.has("Authorization")) {
      headers.set("Authorization", `Bearer ${token}`);
    }

    const doRequest = () =>
      origFetch(input, {
        ...init,
        headers,
        body: init?.body,
      });

    let res = await doRequest();
    if (
      res.status === 401 &&
      isApiUrl(url) &&
      !isAuthUrl(url) &&
      !(typeof FormData !== "undefined" && init?.body instanceof FormData)
    ) {
      await forceRefreshAuth();
      const t2 = getToken();
      const headers2 = new Headers(init?.headers ?? undefined);
      if (t2) headers2.set("Authorization", `Bearer ${t2}`);
      res = await origFetch(input, { ...init, headers: headers2 });
    }
    return res;
  };
}

/** SSE 地址：EventSource 不能带 header，token 走查询参数 */
export function sseUrl(path: string): string {
  const base = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8100";
  const token = getToken();
  if (!token) return `${base}${path}`;
  return `${base}${path}?token=${encodeURIComponent(token)}`;
}
