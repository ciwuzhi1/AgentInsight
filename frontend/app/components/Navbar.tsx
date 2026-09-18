"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { getToken, getUsername, ensureAuthToken, forceRefreshAuth } from "../auth-client";

/** AI 图标：圆角方块 + 四芒星 */
function AiIcon({ className = "h-7 w-7" }: { className?: string }) {
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
      <path
        d="M16 7.5c.4 3.6 2.4 5.6 6 6-3.6.4-5.6 2.4-6 6-.4-3.6-2.4-5.6-6-6 3.6-.4 5.6-2.4 6-6z"
        fill="url(#ai-icon-grad)"
      />
    </svg>
  );
}

/**
 * MiMo 风格顶栏：汉堡 + Logo + 主题/设置 + 可选用户菜单。
 * localStorage 只在 useEffect 里读，避免 SSR 水合不一致。
 */
export default function Navbar({
  onToggleSidebar,
  sidebarCollapsed,
  theme = "navy",
  onToggleTheme,
}: {
  onToggleSidebar?: () => void;
  sidebarCollapsed?: boolean;
  theme?: "navy" | "amber";
  onToggleTheme?: () => void;
} = {}) {
  const [username, setUsername] = useState<string | null>(null);
  const [authed, setAuthed] = useState(false);
  const [authReady, setAuthReady] = useState(false);
  const [avatarOpen, setAvatarOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    async function syncAuth() {
      await ensureAuthToken();
      if (cancelled) return;
      setAuthed(Boolean(getToken()));
      setUsername(getUsername());
      setAuthReady(true);
    }
    void syncAuth();

    function onAuthEvent() {
      setAuthed(Boolean(getToken()));
      setUsername(getUsername());
    }
    window.addEventListener("agentinsight:auth", onAuthEvent);

    const t = setInterval(async () => {
      if (!getToken()) await ensureAuthToken();
      if (cancelled) return;
      setAuthed(Boolean(getToken()));
      setUsername(getUsername());
      if (getToken()) setAuthReady(true);
    }, 5000);

    return () => {
      cancelled = true;
      clearInterval(t);
      window.removeEventListener("agentinsight:auth", onAuthEvent);
    };
  }, []);

  // 点击菜单外部关闭
  useEffect(() => {
    if (!avatarOpen) return;
    function handleClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setAvatarOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [avatarOpen]);

  function logout() {
    // 常驻单账号模式：「退出」= 强制重置会话并重新自动登录
    setAvatarOpen(false);
    void forceRefreshAuth().then((t) => {
      setAuthed(Boolean(t));
      setUsername(getUsername());
    });
  }

  const initial = username ? username.slice(0, 1).toUpperCase() : "?";
  const hamburgerLabel = sidebarCollapsed ? "展开侧栏" : "收起侧栏";

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
      <div className="flex h-[52px] items-center justify-between px-3 sm:px-4">
        {/* 左侧：汉堡 + Logo */}
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => onToggleSidebar?.()}
            aria-label={hamburgerLabel}
            className="flex h-9 w-9 items-center justify-center rounded-lg transition hover-surface"
            style={{ color: "var(--text-secondary)" }}
          >
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden="true">
              <path
                d="M3 5h12M3 9h12M3 13h12"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
            </svg>
          </button>

          <Link
            href="/"
            className="flex items-center gap-2 rounded-lg px-1.5 py-1"
            aria-label="AgentInsight 首页"
          >
            <AiIcon />
            <span
              className="text-base font-semibold tracking-tight"
              style={{ color: "var(--text-primary)" }}
            >
              Agent
              <span className="text-gradient-brand">Insight</span>
            </span>
          </Link>
        </div>

        {/* 右侧：主题 + 设置 + 可选用户 */}
        <nav className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => onToggleTheme?.()}
            className="flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-sm transition hover-surface"
            style={{ color: "var(--text-secondary)" }}
            aria-label={theme === "navy" ? "切换到暖黄主题" : "切换到深蓝主题"}
            title={theme === "navy" ? "暖黄" : "深蓝"}
          >
            {theme === "navy" ? (
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
                <circle cx="8" cy="8" r="3.2" stroke="currentColor" strokeWidth="1.4" />
                <path d="M8 1.5v1.6M8 12.9v1.6M1.5 8h1.6M12.9 8h1.6M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M12.6 3.4l-1.1 1.1M4.5 11.5l-1.1 1.1" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden>
                <path d="M13.5 9.2A5.5 5.5 0 016.8 2.5 5.6 5.6 0 1013.5 9.2z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
              </svg>
            )}
            <span className="hidden sm:inline">{theme === "navy" ? "深蓝" : "暖黄"}</span>
          </button>

          <Link
            href="/settings"
            aria-label="设置"
            className="flex h-8 items-center gap-1.5 rounded-lg px-2.5 text-sm transition hover-surface"
            style={{ color: "var(--text-secondary)" }}
          >
            <span aria-hidden="true">⚙</span>
            <span className="hidden sm:inline">设置</span>
          </Link>

          {/* 右侧登录模块：常驻，布局不变 */}
          <div className="relative" ref={menuRef}>
            <button
              type="button"
              onClick={() => setAvatarOpen((v) => !v)}
              className="flex h-8 items-center gap-1.5 rounded-lg px-1.5 pr-2.5 text-sm transition hover-surface"
              style={{ color: "var(--text-primary)" }}
              aria-haspopup="menu"
              aria-expanded={avatarOpen}
              aria-label="用户菜单"
            >
              <span
                className="flex h-6 w-6 items-center justify-center rounded-full text-[11px] font-semibold"
                style={{
                  background: "linear-gradient(135deg, var(--primary-light), var(--primary-dark))",
                  color: "var(--on-primary, #fff)",
                  opacity: authed ? 1 : 0.55,
                }}
              >
                {initial}
              </span>
              <span className="hidden max-w-[7rem] truncate sm:inline">
                {!authReady
                  ? "连接中…"
                  : authed
                    ? (username ?? "已登录")
                    : "自动登录…"}
              </span>
            </button>

            {avatarOpen && (
              <div
                className="glass absolute right-0 mt-1.5 w-44 overflow-hidden py-1"
                role="menu"
              >
                <p
                  className="truncate border-b px-3 py-2 text-xs"
                  style={{
                    borderColor: "var(--border-glass)",
                    color: "var(--text-secondary)",
                  }}
                >
                  {authed ? (username ?? "") : "尚未获得 token"}
                </p>
                <button
                  type="button"
                  onClick={async () => {
                    await forceRefreshAuth();
                    setAuthed(Boolean(getToken()));
                    setUsername(getUsername());
                    setAvatarOpen(false);
                  }}
                  className="block w-full px-3 py-2 text-left text-sm transition hover-surface"
                  style={{ color: "var(--text-secondary)" }}
                  role="menuitem"
                >
                  重新连接
                </button>
                <button
                  type="button"
                  onClick={logout}
                  className="block w-full px-3 py-2 text-left text-sm transition hover-surface"
                  style={{ color: "var(--text-secondary)" }}
                  role="menuitem"
                >
                  重置会话
                </button>
              </div>
            )}
          </div>
        </nav>
      </div>
    </header>
  );
}
