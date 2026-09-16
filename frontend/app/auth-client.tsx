/* 认证助手：可选 token 注入 Authorization；已移除登录页与 401 强制跳转。 */
"use client";

const TOKEN_KEY = "ai_token";
const USER_KEY = "ai_username";

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
}

export function clearAuth() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

let installed = false;

/** 幂等安装：有 token 时自动带 Authorization；无登录页，401 不跳转。 */
export function installAuthFetch() {
  if (typeof window === "undefined" || installed) return;
  installed = true;
  const origFetch = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const token = getToken();
    const headers = new Headers(init?.headers ?? undefined);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    return origFetch(input, { ...init, headers });
  };
}

/** SSE 地址：EventSource 不能带 header，token 走查询参数（后端 get_current_user_flex 支持）。 */
export function sseUrl(path: string): string {
  const base = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8100";
  const token = getToken();
  if (!token) return `${base}${path}`;
  return `${base}${path}?token=${encodeURIComponent(token)}`;
}

