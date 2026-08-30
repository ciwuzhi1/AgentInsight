"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { setAuth } from "../auth-client";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8100";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);
    setBusy(true);
    try {
      const res = await fetch(`${API_BASE}/api/auth/${mode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: username.trim(), password }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(String(body.detail ?? `请求失败（${res.status}）`));
        return;
      }
      // 注册接口返回 {user_id, username}，需再登录拿 token
      if (mode === "register") {
        const lr = await fetch(`${API_BASE}/api/auth/login`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ username: username.trim(), password }),
        });
        if (!lr.ok) {
          setError("注册成功，但自动登录失败，请手动登录");
          setMode("login");
          return;
        }
        setAuth((await lr.json()).token, username.trim());
      } else {
        setAuth(body.token, body.username ?? username.trim());
      }
      router.push("/");
    } catch (e) {
      setError(`网络错误：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <div className="w-full max-w-sm rounded-2xl border border-slate-800 bg-slate-900/60 p-8">
        <h1 className="text-2xl font-bold text-slate-100">
          Agent<span className="bg-gradient-to-r from-sky-400 to-cyan-300 bg-clip-text text-transparent">Insight</span>
        </h1>
        <p className="mt-1 mb-6 text-sm text-slate-400">{mode === "login" ? "登录以继续" : "创建新账号"}</p>

        <label className="block text-xs text-slate-400">用户名</label>
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          className="mt-1 mb-4 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 outline-none focus:border-sky-500"
          placeholder="3~32 个字符"
        />
        <label className="block text-xs text-slate-400">密码</label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
          className="mt-1 mb-4 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100 outline-none focus:border-sky-500"
          placeholder="至少 6 位"
        />

        {error && <div className="mb-4 rounded-lg border border-rose-800 bg-rose-950/40 px-3 py-2 text-sm text-rose-300">{error}</div>}

        <button
          onClick={submit}
          disabled={busy || !username.trim() || !password}
          className="w-full rounded-lg bg-sky-600 px-4 py-2 font-medium text-white transition hover:bg-sky-500 disabled:opacity-50"
        >
          {busy ? "请稍候…" : mode === "login" ? "登录" : "注册并登录"}
        </button>

        <p className="mt-4 text-center text-sm text-slate-400">
          {mode === "login" ? (
            <>
              没有账号？{" "}
              <button className="text-sky-400 hover:underline" onClick={() => { setMode("register"); setError(null); }}>
                注册
              </button>
            </>
          ) : (
            <>
              已有账号？{" "}
              <button className="text-sky-400 hover:underline" onClick={() => { setMode("login"); setError(null); }}>
                登录
              </button>
            </>
          )}
          {" · "}
          <Link href="/" className="text-slate-500 hover:text-slate-300">
            先逛逛
          </Link>
        </p>
      </div>
    </main>
  );
}
