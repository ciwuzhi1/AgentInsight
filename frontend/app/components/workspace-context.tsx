"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type WorkspaceView = "dataset" | "analysis" | "match" | "crawler";

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

  const setView = useCallback((v: WorkspaceView) => {
    setViewState(v);
  }, []);

  const value = useMemo(() => ({ view, setView }), [view, setView]);
  return (
    <WorkspaceCtx.Provider value={value}>{children}</WorkspaceCtx.Provider>
  );
}

export function useWorkspace() {
  return useContext(WorkspaceCtx);
}
