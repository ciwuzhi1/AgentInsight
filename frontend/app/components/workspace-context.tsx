"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type WorkspaceView = "dataset" | "analysis" | "match" | "crawler";

const VALID_VIEWS: readonly WorkspaceView[] = [
  "dataset",
  "analysis",
  "match",
  "crawler",
];

function parseViewFromSearch(search: string): WorkspaceView {
  const raw = new URLSearchParams(search).get("view");
  return VALID_VIEWS.includes(raw as WorkspaceView)
    ? (raw as WorkspaceView)
    : "dataset";
}

function buildViewPath(view: WorkspaceView): string {
  const url = new URL(window.location.href);
  if (view === "dataset") {
    url.searchParams.delete("view");
  } else {
    url.searchParams.set("view", view);
  }
  return url.pathname + url.search + url.hash;
}

type WorkspaceState = {
  view: WorkspaceView;
  setView: (v: WorkspaceView) => void;
};

const WorkspaceCtx = createContext<WorkspaceState>({
  view: "dataset",
  setView: () => {},
});

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [view, setViewState] = useState<WorkspaceView>("dataset");

  // Initialize view from URL query on mount (SSR-safe default = dataset).
  // Also respond to popstate so back/forward restores the query view.
  useEffect(() => {
    setViewState(parseViewFromSearch(window.location.search));

    function onPopState() {
      setViewState(parseViewFromSearch(window.location.search));
    }
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  // 空状态等场景：CustomEvent 导航到指定视图
  useEffect(() => {
    function onNavigate(e: Event) {
      const v = (e as CustomEvent<{ view?: WorkspaceView }>).detail?.view;
      if (!v || !VALID_VIEWS.includes(v)) return;
      setViewState(v);
      window.history.replaceState(window.history.state, "", buildViewPath(v));
    }
    window.addEventListener("agentinsight:navigate", onNavigate);
    return () => window.removeEventListener("agentinsight:navigate", onNavigate);
  }, []);

  const setView = useCallback((v: WorkspaceView) => {
    const next: WorkspaceView = VALID_VIEWS.includes(v) ? v : "dataset";
    setViewState(next);
    window.history.replaceState(window.history.state, "", buildViewPath(next));
  }, []);

  const value = useMemo(() => ({ view, setView }), [view, setView]);
  return (
    <WorkspaceCtx.Provider value={value}>{children}</WorkspaceCtx.Provider>
  );
}

export function useWorkspace() {
  return useContext(WorkspaceCtx);
}
