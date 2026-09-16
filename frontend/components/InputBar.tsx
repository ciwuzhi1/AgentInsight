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
    <div className="glass-strong p-3">
      <div className="flex items-end gap-2">
        <textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          disabled={disabled}
          rows={1}
          className="max-h-40 min-h-[2.5rem] flex-1 resize-none rounded-xl border border-transparent bg-transparent px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-secondary)] focus:border-[var(--primary)]/40 focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
        />
        <button
          type="button"
          onClick={submit}
          disabled={!canSend}
          className="shrink-0 rounded-xl bg-[var(--primary)] px-4 py-2 text-sm font-medium text-white transition hover:bg-[var(--primary-light)] disabled:cursor-not-allowed disabled:opacity-40"
        >
          发送
        </button>
      </div>
    </div>
  );
}
