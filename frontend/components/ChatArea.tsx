"use client";

/* 对话主区域：消息列表滚动 + 空状态欢迎语 + 加载脉冲 + 自动滚到底部 */

import { useEffect, useRef } from "react";
import { MessageBubble, type ChatMessage } from "./MessageBubble";

function LoadingDots() {
  return (
    <div className="flex justify-start">
      <div className="glass animate-pulse flex items-center gap-1.5 rounded-2xl px-4 py-3">
        <span className="h-2 w-2 animate-bounce rounded-full bg-[var(--primary-light)] [animation-delay:0ms]" />
        <span className="h-2 w-2 animate-bounce rounded-full bg-[var(--primary-light)] [animation-delay:150ms]" />
        <span className="h-2 w-2 animate-bounce rounded-full bg-[var(--primary-light)] [animation-delay:300ms]" />
      </div>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
      <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-[var(--primary)]/15 text-2xl">
        💬
      </div>
      <h3 className="text-base font-semibold text-[var(--text-primary)]">
        开始一场对话
      </h3>
      <p className="max-w-sm text-sm leading-relaxed text-[var(--text-secondary)]">
        上传数据集后，用自然语言提问。Agent 会拆解任务、生成 SQL，并在下方实时展示执行过程与结果。
      </p>
    </div>
  );
}

export function ChatArea({
  messages,
  loading = false,
}: {
  messages: ChatMessage[];
  loading?: boolean;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const isEmpty = messages.length === 0 && !loading;

  // 新消息或加载态变化时自动滚到底部
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length, loading]);

  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-2 py-4">
      {isEmpty ? (
        <EmptyState />
      ) : (
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-3">
          {messages.map((m) => (
            <MessageBubble key={m.id} message={m} />
          ))}
          {loading && <LoadingDots />}
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  );
}
