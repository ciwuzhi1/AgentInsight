"use client";

import Link from "next/link";
import { useState } from "react";

/* 快捷提示词：与主页 JOB_PROMPT_PRESETS 对齐，点击可交给父组件填入输入框 */
const QUICK_PROMPTS = [
  "JD 中需求最多的技能 Top 10 是什么？",
  "统计各城市的岗位数量和平均薪资",
  "要求 Python 的岗位里哪个城市薪资最高？",
  "各薪资区间的岗位数量分布",
  "对比北京和上海的技能要求差异",
];

type NavItem = {
  href: string;
  label: string;
  icon: string;
};

const NAV_ITEMS: NavItem[] = [
  { href: "/#analysis", label: "数据分析", icon: "📊" },
  { href: "/#match", label: "简历匹配", icon: "🧩" },
  { href: "/#analysis", label: "历史记录", icon: "🗂" },
];

export type SidebarProps = {
  /** 点击快捷提示时回调（主页可把文案填进输入框） */
  onSelectPrompt?: (prompt: string) => void;
  /** 受控折叠状态；不传则内部自管 */
  collapsed?: boolean;
  onCollapsedChange?: (collapsed: boolean) => void;
};

/**
 * 左侧可折叠侧栏：毛玻璃 + 导航锚点 + 快捷提示词。
 * 折叠后仅保留图标列（宽 16），展开约 64。
 */
export default function Sidebar({
  onSelectPrompt,
  collapsed: controlledCollapsed,
  onCollapsedChange,
}: SidebarProps) {
  const [internalCollapsed, setInternalCollapsed] = useState(false);
  const collapsed = controlledCollapsed ?? internalCollapsed;

  function toggle() {
    const next = !collapsed;
    if (controlledCollapsed === undefined) setInternalCollapsed(next);
    onCollapsedChange?.(next);
  }

  return (
    <aside
      className="sidebar-shell glass sticky top-16 hidden h-[calc(100vh-5rem)] shrink-0 flex-col overflow-hidden md:flex"
      style={{
        width: collapsed ? 64 : 248,
        borderRadius: 12,
      }}
      aria-label="主导航侧栏"
    >
      {/* 折叠开关 */}
      <div className="flex items-center justify-between border-b px-3 py-3" style={{ borderColor: "var(--border-glass)" }}>
        {!collapsed && (
          <span className="sidebar-body text-xs font-medium tracking-wide" style={{ color: "var(--text-secondary)" }}>
            导航
          </span>
        )}
        <button
          type="button"
          onClick={toggle}
          className="btn-glow flex h-8 w-8 items-center justify-center rounded-lg border text-sm"
          style={{
            borderColor: "var(--border-glass)",
            color: "var(--text-secondary)",
            background: "var(--bg-card)",
            marginLeft: collapsed ? "auto" : undefined,
          }}
          aria-label={collapsed ? "展开侧栏" : "收起侧栏"}
          title={collapsed ? "展开" : "收起"}
        >
          {collapsed ? "»" : "«"}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-2 py-3">
        {/* 导航项 */}
        <ul className="space-y-1">
          {NAV_ITEMS.map((item) => (
            <li key={item.label}>
              <Link
                href={item.href}
                className="btn-glow flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-sm transition"
                style={{ color: "var(--text-secondary)" }}
                title={collapsed ? item.label : undefined}
              >
                <span className="flex h-6 w-6 shrink-0 items-center justify-center text-base" aria-hidden="true">
                  {item.icon}
                </span>
                {!collapsed && (
                  <span className="sidebar-body truncate" style={{ color: "var(--text-primary)" }}>
                    {item.label}
                  </span>
                )}
              </Link>
            </li>
          ))}
        </ul>

        {/* 快捷提示词 */}
        {!collapsed && (
          <div className="sidebar-body mt-6">
            <p className="mb-2 px-1 text-xs font-medium tracking-wide" style={{ color: "var(--text-secondary)" }}>
              快捷提问
            </p>
            <ul className="space-y-1.5">
              {QUICK_PROMPTS.map((q) => (
                <li key={q}>
                  <button
                    type="button"
                    onClick={() => onSelectPrompt?.(q)}
                    className="w-full rounded-lg border px-2.5 py-2 text-left text-xs leading-relaxed transition hover:brightness-110"
                    style={{
                      borderColor: "var(--border-glass)",
                      background: "var(--bg-card)",
                      color: "var(--text-secondary)",
                    }}
                    title={q}
                  >
                    {q}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* 底部品牌角标 */}
      <div
        className="border-t px-3 py-3 text-center"
        style={{ borderColor: "var(--border-glass)" }}
      >
        <p className="text-[10px]" style={{ color: "var(--text-secondary)", opacity: 0.7 }}>
          {collapsed ? "AI" : "AgentInsight"}
        </p>
      </div>
    </aside>
  );
}
