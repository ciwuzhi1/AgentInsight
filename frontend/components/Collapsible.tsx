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

export function CollapsibleCard({
  id,
  icon,
  no,
  title,
  desc,
  defaultOpen = true,
  children,
}: {
  id: string;
  icon: string;
  no: string;
  title: string;
  desc: string;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  return (
    <section id={id} className="glass scroll-mt-20 p-5 sm:p-6">
      <Collapsible
        defaultOpen={defaultOpen}
        className="-mx-1"
        headerClassName="items-start"
        header={
          <span className="flex items-start gap-3 py-1">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-[var(--primary)]/10 text-xl">
              {icon}
            </span>
            <span className="min-w-0">
              <span className="flex flex-wrap items-center gap-2 text-lg font-semibold text-[var(--text-primary)]">
                <span className="font-mono text-xs font-bold tracking-widest text-[var(--primary-light)]">
                  {no}
                </span>
                {title}
              </span>
              <span className="mt-0.5 block text-sm text-[var(--text-secondary)]">
                {desc}
              </span>
            </span>
          </span>
        }
      >
        <div className="border-t border-[var(--border-glass)] pt-5">
          {children}
        </div>
      </Collapsible>
    </section>
  );
}
