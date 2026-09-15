"use client";

/* 聊天消息气泡：用户消息右对齐蓝底，助手消息左对齐毛玻璃 */

export type ChatRole = "user" | "assistant";

export type ChatMessage = {
  id: string;
  role: ChatRole;
  content: string;
};

export function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";

  return (
    <div
      className={`flex animate-fade-in-up ${isUser ? "justify-end" : "justify-start"}`}
    >
      <div
        className={[
          "max-w-[80%] rounded-2xl px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap break-words",
          isUser
            ? "bg-[var(--primary)] text-white shadow-lg shadow-[var(--primary)]/20"
            : "glass text-[var(--text-primary)]",
        ].join(" ")}
      >
        {message.content}
      </div>
    </div>
  );
}
