"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import Navbar from "./Navbar";
import Sidebar from "./Sidebar";
import { WorkspaceProvider } from "./workspace-context";

const THEME_KEY = "agentinsight-theme";
export type ThemeName = "navy" | "amber";

function readTheme(): ThemeName {
  if (typeof document === "undefined") return "navy";
  const t = document.documentElement.getAttribute("data-theme");
  return t === "amber" ? "amber" : "navy";
}

export default function AppShell({ children }: { children: ReactNode }) {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [theme, setTheme] = useState<ThemeName>("navy");
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setTheme(readTheme());
    setSidebarCollapsed(localStorage.getItem("agentinsight-sidebar") === "1");
    setReady(true);
  }, []);

  useEffect(() => {
    if (!ready) return;
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem(THEME_KEY, theme);
  }, [theme, ready]);

  useEffect(() => {
    if (!ready) return;
    localStorage.setItem("agentinsight-sidebar", sidebarCollapsed ? "1" : "0");
  }, [sidebarCollapsed, ready]);

  const toggleSidebar = useCallback(() => {
    if (window.matchMedia("(max-width: 767px)").matches) {
      setMobileOpen((v) => !v);
    } else {
      setSidebarCollapsed((v) => !v);
    }
  }, []);

  const toggleTheme = useCallback(() => {
    setTheme((t) => (t === "navy" ? "amber" : "navy"));
  }, []);

  return (
    <WorkspaceProvider>
      <div className="min-h-screen">
        <Navbar
          onToggleSidebar={toggleSidebar}
          sidebarCollapsed={sidebarCollapsed}
          theme={theme}
          onToggleTheme={toggleTheme}
        />
        <div
          className="mx-auto flex"
          style={{
            paddingTop: "var(--topbar-h)",
            gap: "var(--shell-gap)",
            maxWidth: 1440,
            paddingLeft: "var(--shell-gap)",
            paddingRight: "var(--shell-gap)",
          }}
        >
          <Sidebar
            collapsed={sidebarCollapsed}
            onCollapsedChange={setSidebarCollapsed}
            mobileOpen={mobileOpen}
            onMobileOpenChange={setMobileOpen}
          />
          <main className="min-w-0 flex-1 pb-4 pt-2">{children}</main>
        </div>
      </div>
    </WorkspaceProvider>
  );
}
