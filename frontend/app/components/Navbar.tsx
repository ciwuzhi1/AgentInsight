"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getToken, getUsername, clearAuth } from "../auth-client";

/** AI 图标：圆角方块 + 闪烁星形，搭配主色渐变 */
function AiIcon({ className = "h-8 w-8" }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      className={className}
      aria-hidden="true"
      fill="none"
    >
      <defs>
        <linearGradient id="ai-icon-grad" x1="0" y1="0" x2="32" y2="32">
          <stop offset="0%" stopColor="var(--primary-light)" />
          <stop offset="100%" stopColor="var(--primary)" />
        </linearGradient>
      </defs>
      <rect
        x="1"
        y="1"
        width="30"
        height="30"
        rx="9"
        fill="url(#ai-icon-grad)"
        opacity="0.18"
      />
      <rect
        x="1"
        y="1"
        width="30"
        height="30"
        rx="9"
        stroke="url(#ai-icon-grad)"
        strokeWidth="1.5"
        opacity="0.85"
      />
      {/* 四芒星：代表 AI 能力 */}
      <path
        d="M16 7.5c.4 3.6 2.4 5.6 6 6-3.6.4-5.6 2.4-6 6-.4-3.6-2.4-5.6-6-6 3.6-.4 5.6-2.4 6-6z"
        fill="url(#ai-icon-grad)"
      />
      <circle cx="23.5" cy="9.5" r="1.4" fill="var(--primary-light)" opacity="0.9" />
      <circle cx="9" cy="22.5" r="1.1" fill="var(--primary)" opacity="0.75" />
    </svg>
  );
}

/**
 * 顶部固定导航：毛玻璃 + Logo + 设置入口 + 登录/用户头像。
 * localStorage 只在 useEffect 里读，避免 SSR 水合不一致。
 */
export default function Navbar() {
  const [username, setUsername] = useState<string | null>(null);
  const [authed, setAuthed] = useState(false);
  const [avatarOpen, setAvatarOpen] = useState(false);

  useEffect(() => {
    setAuthed(Boolean(getToken()));
    setUsername(getUsername());
  }, []);

  function logout() {
    clearAuth();
    setAuthed(false);
    setUsername(null);
    setAvatarOpen(false);
    window.location.href = "/login";
  }

  /** 头像首字母：中英文用户名都取第一个字符 */
  const initial = username ? username.slice(0, 1).toUpperCase() : "?";

  return (
    <header
      className="fixed inset-x-0 top-0 z-50 border-b"
      style={{
        background: "var(--bg-glass)",
        backdropFilter: "blur(12px)",
        WebkitBackdropFilter: "blur(12px)",
        borderColor: "var(--border-glass)",
      }}
    >
      <div className="mx-auto flex h-14 max-w-7xl items-center justify-between px-4 sm:px-6">
        {/* Logo */}
        <Link
          href="/"
          className="btn-glow flex items-center gap-2.5 rounded-xl px-1 py-1"
          aria-label="AgentInsight 首页"
        >
          <AiIcon />
          <span
            className="text-lg font-bold tracking-tight"
            style={{ color: "var(--text-primary)" }}
          >
            Agent
            <span
              className="bg-gradient-to-r from-sky-400 to-cyan-300 bg-clip-text text-transparent"
            >
              Insight
            </span>
          </span>
        </Link>

        {/* 右侧操作区 */}
        <nav className="flex items-center gap-2 sm:gap-3">
          <Link
            href="/settings"
            className="btn-glow flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-sm transition"
            style={{
              borderColor: "var(--border-glass)",
              color: "var(--text-secondary)",
              background: "var(--bg-card)",
            }}
          >
            <span aria-hidden="true">⚙</span>
            <span className="hidden sm:inline">设置</span>
          </Link>

          {authed ? (
            <div className="relative">
              <button
                type="button"
                onClick={() => setAvatarOpen((v) => !v)}
                className="btn-glow flex items-center gap-2 rounded-full border py-1 pl-1 pr-3 text-sm"
                style={{
                  borderColor: "var(--border-glass)",
                  background: "var(--bg-card)",
                  color: "var(--text-primary)",
                }}
                aria-haspopup="menu"
                aria-expanded={avatarOpen}
              >
                <span
                  className="flex h-7 w-7 items-center justify-center rounded-full text-xs font-semibold text-white"
                  style={{
                    background: "linear-gradient(135deg, var(--primary-light), var(--primary-dark))",
                  }}
                >
                  {initial}
                </span>
                <span className="hidden max-w-[8rem] truncate sm:inline">
                  {username ?? "用户"}
                </span>
              </button>

              {avatarOpen && (
                <div
                  className="glass absolute right-0 mt-2 w-40 overflow-hidden py-1"
                  role="menu"
                >
                  <p
                    className="truncate border-b px-3 py-2 text-xs"
                    style={{
                      borderColor: "var(--border-glass)",
                      color: "var(--text-secondary)",
                    }}
                  >
                    {username ?? ""}
                  </p>
                  <button
                    type="button"
                    onClick={logout}
                    className="block w-full px-3 py-2 text-left text-sm transition hover:bg-white/5"
                    style={{ color: "var(--text-secondary)" }}
                    role="menuitem"
                  >
                    退出登录
                  </button>
                </div>
              )}
            </div>
          ) : (
            <Link
              href="/login"
              className="btn-glow rounded-lg px-4 py-1.5 text-sm font-medium text-white"
              style={{
                background: "linear-gradient(135deg, var(--primary), var(--primary-dark))",
              }}
            >
              登录
            </Link>
          )}
        </nav>
      </div>
    </header>
  );
}
