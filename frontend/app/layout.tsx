import type { Metadata } from "next";
import "./globals.css";
import AppShell from "./components/AppShell";

export const metadata: Metadata = {
  title: "AgentInsight",
  description:
    "Agent 驱动的数据分析工作台：上传 CSV、自然语言提问、查看执行时间线与图表",
};

const themeInit = `(function(){try{var t=localStorage.getItem('agentinsight-theme');if(t==='amber'||t==='navy')document.documentElement.setAttribute('data-theme',t);}catch(e){}})();`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInit }} />
      </head>
      <body className="min-h-screen">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
