"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { setToken } from "@/auth/token";
import { API_BASE } from "@/api/client";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    const res = await fetch(`${API_BASE}/auth/token`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ username, password }),
    });
    if (!res.ok) { setError("Invalid username or password"); return; }
    const data = await res.json();
    setToken(data.access_token);
    router.replace("/");
  }

  return (
    <main className="mx-auto max-w-sm p-8">
      <h1 className="mb-6 text-2xl font-semibold">bellwether</h1>
      <form onSubmit={onSubmit} className="space-y-3">
        <input aria-label="username" className="w-full rounded border p-2" placeholder="username"
               value={username} onChange={(e) => setUsername(e.target.value)} />
        <input aria-label="password" type="password" className="w-full rounded border p-2" placeholder="password"
               value={password} onChange={(e) => setPassword(e.target.value)} />
        {error && <p className="text-sm text-red-600">{error}</p>}
        <button type="submit" className="w-full rounded bg-black p-2 text-white">Sign in</button>
      </form>
    </main>
  );
}
