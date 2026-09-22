"use client";

import { useId, useState, type ReactNode } from "react";

function Chevron({ open }: { open: boolean }) {
  return (
    <span
      className="chevron inline-flex h-5 w-5 shrink-0 items-center justify-center text-[var(--text-secondary)]"
      data-open={open}
      aria-hidden
    >
      <svg viewBox="0 0 16 16" className="h-3.5 w-3.5">
        <path
          d="M6 3.5L10.5 8 6 12.5"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </span>
  );
}

export type CollapsibleProps = {
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  header: ReactNode;
  /** 放在标题行右侧、折叠按钮之外，避免嵌套 button */
  headerActions?: ReactNode;
  children: ReactNode;
  className?: string;
  headerClassName?: string;
  contentClassName?: string;
};

export function Collapsible({
  open: controlledOpen,
  defaultOpen = true,
  onOpenChange,
  header,
  headerActions,
  children,
  className = "",
  headerClassName = "",
  contentClassName = "",
}: CollapsibleProps) {
  const id = useId();
  const [internalOpen, setInternalOpen] = useState(defaultOpen);
  const open = controlledOpen ?? internalOpen;

  function toggle() {
    const next = !open;
    if (controlledOpen === undefined) setInternalOpen(next);
    onOpenChange?.(next);
  }

  return (
    <div className={className}>
      <div className={`flex items-center gap-2 ${headerClassName}`}>
        <button
          type="button"
          onClick={toggle}
          className="collapse-header flex min-w-0 flex-1 items-center gap-2 rounded-lg px-1 py-1 text-left"
          aria-expanded={open}
          aria-controls={id}
        >
          <Chevron open={open} />
          <span className="min-w-0 flex-1">{header}</span>
        </button>
        {headerActions}
      </div>
      <div
        id={id}
        className="collapse-grid"
        data-open={open}
        style={{
          display: "grid",
          gridTemplateRows: open ? "1fr" : "0fr",
          transition: "grid-template-rows 0.22s ease",
        }}
      >
        <div
          className={contentClassName}
          style={{ overflow: "hidden", minHeight: 0 }}
        >
          <div className="pt-1">{children}</div>
        </div>
      </div>
    </div>
  );
}


