/* 认证前端助手：token 存 localStorage，全局 fetch 注入 Authorization，401 自动跳登录。 */
"use client";


import { useEffect, useState } from "react";

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

/** 幂等安装：所有 fetch 自动带 Authorization；401 且不在登录页 → 跳 /login。 */
export function installAuthFetch() {
  if (typeof window === "undefined" || installed) return;
  installed = true;
  const origFetch = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const token = getToken();
    const headers = new Headers(init?.headers ?? undefined);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const res = await origFetch(input, { ...init, headers });
    if (res.status === 401 && !window.location.pathname.startsWith("/login")) {
      clearAuth();
      window.location.href = "/login";
    }
    return res;
  };
}

/** SSE 地址：EventSource 不能带 header，token 走查询参数（后端 get_current_user_flex 支持）。 */
export function sseUrl(path: string): string {
  const base = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8100";
  const token = getToken() ?? "";
  return `${base}${path}?token=${encodeURIComponent(token)}`;
}

/** 导航栏用户徽标：显示用户名 + 退出按钮（未登录不渲染）。
 *  localStorage 只能在 useEffect 里读——渲染期读会导致 SSR/客户端水合不一致（Next 左下角报错）。 */
export function UserChip() {
  const [name, setName] = useState<string | null>(null);
  useEffect(() => {
    setName(getUsername());
  }, []);
  if (!name) return null;
  return (
    <span className="ml-1 flex items-center gap-2 text-xs">
      <span className="rounded-lg bg-slate-800/60 px-2.5 py-1.5 text-slate-300">{name}</span>
      <button
        onClick={() => {
          if (typeof window !== "undefined") {
            localStorage.removeItem("ai_token");
            localStorage.removeItem("ai_username");
            window.location.href = "/login";
          }
        }}
        className="rounded-lg border border-slate-700 px-2.5 py-1.5 text-slate-400 transition hover:border-rose-500/60 hover:text-rose-300"
      >
        退出
      </button>
    </span>
  );
}
