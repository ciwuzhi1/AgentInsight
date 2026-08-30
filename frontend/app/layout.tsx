import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AgentInsight",
  description: "Agent 驱动的数据分析工作台：上传 CSV、自然语言提问、查看执行时间线与图表",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN" className="scroll-smooth">
      <body>
        <div className="mx-auto max-w-6xl px-4 py-6">{children}</div>
      </body>
    </html>
  );
}
