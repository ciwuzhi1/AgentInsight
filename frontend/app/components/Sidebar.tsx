"use client";

import { useState, type ReactNode } from "react";
import { useWorkspace, type WorkspaceView } from "./workspace-context";

/* 快捷提示词：与主页 JOB_PROMPT_PRESETS 对齐 */
const QUICK_PROMPTS = [
  "JD 中需求最多的技能 Top 10 是什么？",
  "统计各城市的岗位数量和平均薪资",
  "要求 Python 的岗位里哪个城市薪资最高？",
  "各薪资区间的岗位数量分布",
  "对比北京和上海的技能要求差异",
];

/* —— 线性 SVG 图标 —— */
function IconDataset() {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" className="h-[18px] w-[18px]" aria-hidden="true">
      <ellipse cx="10" cy="5" rx="6.5" ry="2.5" />
      <path d="M3.5 5v10c0 1.4 2.9 2.5 6.5 2.5s6.5-1.1 6.5-2.5V5" strokeLinecap="round" />
      <path d="M3.5 10c0 1.4 2.9 2.5 6.5 2.5s6.5-1.1 6.5-2.5" />
    </svg>
  );
}
function IconChart() {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" className="h-[18px] w-[18px]" aria-hidden="true">
      <path d="M3.5 16.5V9.5M8.5 16.5V4.5M13.5 16.5V7.5M18.5 16.5V11.5" strokeLinecap="round" />
    </svg>
  );
}
function IconMatch() {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" className="h-[18px] w-[18px]" aria-hidden="true">
      <rect x="3" y="3" width="6" height="6" rx="1.5" />
      <rect x="11" y="11" width="6" height="6" rx="1.5" />
      <path d="M9 6h2.5a1.5 1.5 0 011.5 1.5V9M11 14H8.5A1.5 1.5 0 017 12.5V11" strokeLinecap="round" />
    </svg>
  );
}
function IconCrawler() {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" className="h-[18px] w-[18px]" aria-hidden="true">
      <circle cx="10" cy="10" r="7" />
      <path d="M3.5 10h13M10 3.2c-1.8 2.2-2.8 4.4-2.8 6.8s1 4.6 2.8 6.8c1.8-2.2 2.8-4.4 2.8-6.8s-1-4.6-2.8-6.8z" />
    </svg>
  );
}
function ChevronLeft({ flip }: { flip?: boolean }) {
  return (
    <svg
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      className={`h-3.5 w-3.5 transition-transform duration-200 ${flip ? "rotate-180" : ""}`}
      aria-hidden="true"
    >
      <path d="M12.5 4.5L7 10l5.5 5.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
function IconClose() {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" className="h-3.5 w-3.5" aria-hidden="true">
      <path d="M5 5l10 10M15 5L5 15" strokeLinecap="round" />
    </svg>
  );
}
function BrandGlyph() {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" aria-hidden="true" fill="none">
      <rect x="1" y="1" width="22" height="22" rx="6" stroke="var(--primary)" strokeWidth="1.5" opacity="0.7" />
      <path
        d="M12 6.5c.3 2.7 1.8 4.2 4.5 4.5-2.7.3-4.2 1.8-4.5 4.5-.3-2.7-1.8-4.2-4.5-4.5 2.7-.3 4.2-1.8 4.5-4.5z"
        fill="var(--primary-light)"
      />
    </svg>
  );
}

type NavItem = { id: WorkspaceView; label: string; icon: ReactNode };

const NAV_ITEMS: NavItem[] = [
  { id: "dataset", label: "数据集", icon: <IconDataset /> },
  { id: "analysis", label: "数据分析", icon: <IconChart /> },
  { id: "match", label: "简历匹配", icon: <IconMatch /> },
  { id: "crawler", label: "岗位爬虫", icon: <IconCrawler /> },
];

export type SidebarProps = {
  onSelectPrompt?: (prompt: string) => void;
  collapsed?: boolean;
  onCollapsedChange?: (collapsed: boolean) => void;
  /** 移动端抽屉是否打开 */
  mobileOpen?: boolean;
  onMobileOpenChange?: (open: boolean) => void;
};

/**
 * MiMo 风格图标侧栏：折叠 56px / 展开 240px，毛玻璃。
 * md 以下为 fixed 抽屉 + 遮罩。
 */
export default function Sidebar({
  onSelectPrompt,
  collapsed: controlledCollapsed,
  onCollapsedChange,
  mobileOpen = false,
  onMobileOpenChange,
}: SidebarProps) {
  const [internalCollapsed, setInternalCollapsed] = useState(false);
  const collapsed = controlledCollapsed ?? internalCollapsed;
  const { view, setView } = useWorkspace();

  function toggleCollapse() {
    const next = !collapsed;
    if (controlledCollapsed === undefined) setInternalCollapsed(next);
    onCollapsedChange?.(next);
  }

  function closeMobile() {
    onMobileOpenChange?.(false);
  }

  function selectPrompt(q: string) {
    onSelectPrompt?.(q);
    setView("analysis");
    window.dispatchEvent(
      new CustomEvent("agentinsight:quick-prompt", { detail: q })
    );
    closeMobile();
  }

  /** 内容主体；mobile 恒为展开态，关闭走遮罩/关闭按钮 */
  function renderBody(isMobile: boolean) {
    const isCollapsed = isMobile ? false : collapsed;

    return (
      <>
        {/* 顶栏：标题 + 折叠/关闭 */}
        <div
          className="flex shrink-0 items-center justify-between border-b px-3 py-3"
          style={{ borderColor: "var(--border-glass)" }}
        >
          {!isCollapsed && (
            <span
              className="sidebar-body text-xs font-medium tracking-wide"
              style={{ color: "var(--text-secondary)" }}
            >
              导航
            </span>
          )}
          {isMobile ? (
            <button
              type="button"
              onClick={closeMobile}
              className="btn-glow ml-auto flex h-8 w-8 items-center justify-center rounded-lg border"
              style={{
                borderColor: "var(--border-glass)",
                color: "var(--text-secondary)",
                background: "var(--bg-card)",
              }}
              aria-label="关闭导航"
              title="关闭"
            >
              <IconClose />
            </button>
          ) : (
            <button
              type="button"
              onClick={toggleCollapse}
              className="btn-glow flex h-8 w-8 items-center justify-center rounded-lg border"
              style={{
                borderColor: "var(--border-glass)",
                color: "var(--text-secondary)",
                background: "var(--bg-card)",
                marginLeft: isCollapsed ? "auto" : undefined,
              }}
              aria-label={isCollapsed ? "展开侧栏" : "收起侧栏"}
              aria-expanded={!isCollapsed}
              title={isCollapsed ? "展开" : "收起"}
            >
              <ChevronLeft flip={!isCollapsed} />
            </button>
          )}
        </div>

        {/* 可滚动主体 */}
        <div className="flex-1 overflow-y-auto px-1.5 py-2">
          <ul className="space-y-0.5">
            {NAV_ITEMS.map((item) => {
              const active = view === item.id;
              return (
                <li key={item.id}>
                  <button
                    type="button"
                    onClick={() => {
                      setView(item.id);
                      closeMobile();
                    }}
                    className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-xs transition hover-surface"
                    style={{
                      color: active ? "var(--text-primary)" : "var(--text-secondary)",
                      background: active
                        ? "color-mix(in srgb, var(--primary) 14%, transparent)"
                        : undefined,
                    }}
                    title={isCollapsed ? item.label : undefined}
                    aria-label={item.label}
                    aria-current={active ? "page" : undefined}
                  >
                    <span
                      className="flex h-5 w-5 shrink-0 items-center justify-center"
                      style={{
                        color: active ? "var(--primary-light)" : "var(--text-secondary)",
                      }}
                      aria-hidden="true"
                    >
                      {item.icon}
                    </span>
                    {!isCollapsed && (
                      <span className="sidebar-body truncate">{item.label}</span>
                    )}
                  </button>
                </li>
              );
            })}
          </ul>

          {/* 快捷提问：仅分析页 + 展开态 */}
          {!isCollapsed && view === "analysis" && (
            <div className="sidebar-body mt-3">
              <p
                className="mb-1 px-1 text-[11px] font-medium tracking-wide"
                style={{ color: "var(--text-secondary)" }}
              >
                快捷提问
              </p>
              <ul className="space-y-1">
                {QUICK_PROMPTS.map((q) => (
                  <li key={q}>
                    <button
                      type="button"
                      onClick={() => selectPrompt(q)}
                      className="w-full truncate rounded-md border px-2 py-1 text-left text-[11px] transition hover:brightness-110"
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

        {/* 底部品牌 */}
        <div
          className="flex shrink-0 items-center justify-center gap-2 border-t px-3 py-3"
          style={{ borderColor: "var(--border-glass)" }}
        >
          <BrandGlyph />
          {!isCollapsed && (
            <span
              className="sidebar-body text-[10px] tracking-wide"
              style={{ color: "var(--text-secondary)", opacity: 0.7 }}
            >
              AgentInsight
            </span>
          )}
        </div>
      </>
    );
  }

  return (
    <>
      {/* 桌面常驻侧栏 */}
      <aside
        className="sidebar-shell glass sticky hidden h-[calc(100vh-var(--topbar-h)-1rem)] shrink-0 flex-col overflow-hidden md:flex"
        data-collapsed={collapsed ? "true" : "false"}
        style={{ borderRadius: 12, top: "calc(var(--topbar-h) + 0.5rem)" }}
        aria-label="主导航侧栏"
      >
        {renderBody(false)}
      </aside>

      {/* 移动端遮罩 */}
      {mobileOpen && (
        <div
          className="shell-overlay md:hidden"
          onClick={closeMobile}
          aria-hidden="true"
        />
      )}

      {/* 移动端抽屉 */}
      {mobileOpen && (
        <aside
          className="sidebar-shell glass fixed left-0 top-0 z-50 flex h-screen flex-col overflow-hidden md:hidden"
          data-collapsed="false"
          style={{
            borderTopRightRadius: 12,
            borderBottomRightRadius: 12,
          }}
          aria-label="主导航侧栏"
          aria-modal="true"
          role="dialog"
        >
          {renderBody(true)}
        </aside>
      )}
    </>
  );
}
