"use client";

/* 底部输入栏：毛玻璃背景 + 发送按钮；Enter 发送，Shift+Enter 换行 */

import { useState, type KeyboardEvent } from "react";

export function InputBar({
  onSend,
  disabled = false,
  placeholder = "输入你的问题…",
  defaultValue = "",
}: {
  onSend: (text: string) => void;
  disabled?: boolean;
  placeholder?: string;
  /** 初始文本（配合 key 变化实现外部填入） */
  defaultValue?: string;
}) {
  const [value, setValue] = useState(defaultValue);
  const canSend = !disabled && value.trim().length > 0;

  function submit() {
    if (!canSend) return;
    onSend(value.trim());
    setValue("");
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div className="glass-strong p-2">
      <div className="flex items-end gap-1.5">
        <textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          disabled={disabled}
          rows={1}
          className="max-h-32 min-h-[2.25rem] flex-1 resize-none rounded-lg border border-transparent bg-transparent px-2 py-1.5 text-xs text-[var(--text-primary)] placeholder:text-[var(--text-secondary)] focus:border-[var(--primary)]/40 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
        />
        <button
          type="button"
          onClick={submit}
          disabled={!canSend}
          style={{ color: "var(--on-primary, #fff)" }}
          className="shrink-0 rounded-lg bg-[var(--primary)] px-3 py-1.5 text-xs font-medium transition hover:bg-[var(--primary-light)] disabled:cursor-not-allowed disabled:opacity-40"
        >
          发送
        </button>
      </div>
    </div>
  );
}
