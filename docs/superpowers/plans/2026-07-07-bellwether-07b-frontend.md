# bellwether Plan 7b — Frontend (Next.js Dashboard) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Next.js (App Router) + TypeScript dashboard in `frontend/` over the bellwether REST API — login + seven pages, a typed client generated from the backend's OpenAPI, centralized auth, live SSE alerts.

**Architecture:** Client-rendered SPA in a new `frontend/` dir (Python backend untouched). A typed `openapi-fetch` client (types generated from a static `openapi.json` dumped offline from the app) is the single API boundary; a client middleware attaches the JWT Bearer and handles 401→login. SWR for reads, native `EventSource` for the live-alert feed. Tailwind styling. Vitest + React Testing Library for the non-visual logic; pages verified live.

**Tech Stack:** Node 22 / npm 10 (verified), Next.js 15 (App Router), React 19, TypeScript 5, Tailwind CSS 3, `openapi-fetch` + `openapi-typescript`, SWR, Vitest + @testing-library/react + jsdom. Design spec: `docs/superpowers/specs/2026-07-07-bellwether-07b-frontend-design.md`.

## Global Constraints

- Everything lives under **`frontend/`**; do not touch `src/bellwether/` except the one CORS `.env` note (Task 1).
- **The generated typed client (`openapi-fetch` against `src/api/schema.ts`) is the ONLY way to call the API** — no ad-hoc `fetch` to backend routes. `schema.ts` is generated from a static `frontend/openapi.json` (dumped offline via `create_app().openapi()`), so codegen needs no running server.
- **One token, one attach point, one 401 path:** JWT in `localStorage["bw_token"]`; the client middleware adds `Authorization: Bearer` and, on any 401, clears the token + redirects to `/login`. SSE uses `?token=`.
- **Async discovery is surfaced, never blocked on** — `/watchlist` shows `discovery_status` and revalidates.
- **Client components only** (`"use client"`) — it's an SPA; `localStorage`/`EventSource` are browser-only.
- **Lean deps** — only those in the Tech Stack line. Pin versions in `package.json`.
- **`npm install` + `npm run build` + `npm test` need network / a real toolchain** — run those steps **unsandboxed** (network for the npm registry). A subagent that can't reach npm must report BLOCKED, not fake it.
- **Testing:** Vitest + RTL for logic (token store, client middleware, `useAlertStream`, login form, `ConditionBuilder`); pages verified live against the running backend + workers. `npm test` and `npm run build` (tsc) are the gates.
- **CORS:** the backend must allow `http://localhost:3000` — add it to the backend `.env`'s `CORS_ORIGINS` (Task 1 step); already supported since 7a.

## File Structure

```
frontend/
├── package.json  tsconfig.json  next.config.mjs  postcss.config.mjs  tailwind.config.ts
├── vitest.config.ts  vitest.setup.ts  .gitignore  .env.local  openapi.json (generated, git-ignored)
└── src/
    ├── api/schema.ts (generated)   api/client.ts
    ├── auth/token.ts   auth/guard.tsx
    ├── hooks/useAlertStream.ts   hooks/useApi.ts
    ├── components/AppShell.tsx  Badge.tsx  ConditionBuilder.tsx  (+ small shared bits)
    └── app/
        ├── globals.css  layout.tsx
        ├── login/page.tsx
        ├── page.tsx            (Live Feed)
        ├── watchlist/page.tsx
        ├── review/page.tsx
        ├── discovery/page.tsx
        ├── alerts/page.tsx
        └── impact/page.tsx
```

---

### Task 1: Scaffold the Next.js + TS + Tailwind project

**Files:** Create the `frontend/` project skeleton (configs + a placeholder page) + the CORS `.env` line.

**Interfaces:**
- Produces: a buildable Next.js app (`npm run build` passes), Tailwind wired, Vitest configured, `npm run gen:api` script defined (used in Task 2), `.gitignore`.

- [ ] **Step 1: Create `frontend/package.json`**

```json
{
  "name": "bellwether-frontend",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "next lint",
    "test": "vitest run",
    "gen:api": "cd .. && LITELLM_LOCAL_MODEL_COST_MAP=True .venv/bin/python -c \"import json; from bellwether.api.app import create_app; open('frontend/openapi.json','w').write(json.dumps(create_app().openapi()))\" && cd frontend && openapi-typescript openapi.json -o src/api/schema.ts"
  },
  "dependencies": {
    "next": "15.1.6",
    "react": "19.0.0",
    "react-dom": "19.0.0",
    "openapi-fetch": "0.13.4",
    "swr": "2.3.0"
  },
  "devDependencies": {
    "typescript": "5.7.3",
    "@types/react": "19.0.7",
    "@types/react-dom": "19.0.3",
    "@types/node": "22.10.7",
    "tailwindcss": "3.4.17",
    "postcss": "8.5.1",
    "autoprefixer": "10.4.20",
    "openapi-typescript": "7.5.2",
    "vitest": "2.1.8",
    "@vitejs/plugin-react": "4.3.4",
    "@testing-library/react": "16.1.0",
    "@testing-library/jest-dom": "6.6.3",
    "@testing-library/user-event": "14.5.2",
    "jsdom": "25.0.1"
  }
}
```

- [ ] **Step 2: Create the config files**

`frontend/tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2020", "lib": ["dom", "dom.iterable", "esnext"], "allowJs": true,
    "skipLibCheck": true, "strict": true, "noEmit": true, "esModuleInterop": true,
    "module": "esnext", "moduleResolution": "bundler", "resolveJsonModule": true,
    "isolatedModules": true, "jsx": "preserve", "incremental": true,
    "plugins": [{ "name": "next" }],
    "paths": { "@/*": ["./src/*"] }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules"]
}
```
`frontend/next.config.mjs`: `export default {};`
`frontend/postcss.config.mjs`: `export default { plugins: { tailwindcss: {}, autoprefixer: {} } };`
`frontend/tailwind.config.ts`:
```ts
import type { Config } from "tailwindcss";
export default { content: ["./src/**/*.{ts,tsx}"], theme: { extend: {} }, plugins: [] } satisfies Config;
```
`frontend/.env.local`: `NEXT_PUBLIC_API_BASE=http://localhost:8000`
`frontend/.gitignore`:
```
node_modules
.next
openapi.json
*.tsbuildinfo
next-env.d.ts
```

- [ ] **Step 3: Vitest config + setup**

`frontend/vitest.config.ts`:
```ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
export default defineConfig({
  plugins: [react()],
  test: { environment: "jsdom", globals: true, setupFiles: ["./vitest.setup.ts"] },
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
});
```
`frontend/vitest.setup.ts`: `import "@testing-library/jest-dom/vitest";`

- [ ] **Step 4: Minimal app scaffold**

`frontend/src/app/globals.css`:
```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```
`frontend/src/app/layout.tsx`:
```tsx
import "./globals.css";
export const metadata = { title: "bellwether" };
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (<html lang="en"><body className="bg-gray-50 text-gray-900">{children}</body></html>);
}
```
`frontend/src/app/page.tsx` (placeholder — replaced in Task 5):
```tsx
export default function Home() {
  return <main className="p-8">bellwether</main>;
}
```

- [ ] **Step 5: Install + build (UNSANDBOXED — needs the npm registry)**

Run (from `frontend/`): `npm install` then `npm run build`.
Expected: install succeeds; `npm run build` compiles with no type/build errors (it will build `/` as a static/prerendered page). If `npm install` cannot reach the registry, report BLOCKED.

- [ ] **Step 6: Add the CORS origin to the backend `.env`**

Add (or append) to the repo-root `.env`: `CORS_ORIGINS=["http://localhost:3000"]` (JSON list — pydantic-settings parses it). Confirm `LITELLM_LOCAL_MODEL_COST_MAP=True .venv/bin/python -c "from bellwether.config import get_settings; print(get_settings().cors_origins)"` prints `['http://localhost:3000']`. (Do NOT commit real secrets from `.env`; `.env` is git-ignored — this step is local config, not committed.)

- [ ] **Step 7: Commit**

```bash
cd "$(git rev-parse --show-toplevel)"
git add frontend/package.json frontend/tsconfig.json frontend/next.config.mjs frontend/postcss.config.mjs frontend/tailwind.config.ts frontend/vitest.config.ts frontend/vitest.setup.ts frontend/.gitignore frontend/.env.local frontend/src/app
git commit -m "feat(frontend): scaffold Next.js + TS + Tailwind + Vitest project"
```
(Do NOT commit `node_modules`, `.next`, or `openapi.json` — they're git-ignored.)

---

### Task 2: Typed API client + token store

**Files:** Create `frontend/src/api/client.ts`, `frontend/src/auth/token.ts`; generate `frontend/src/api/schema.ts`. Test: `frontend/src/api/client.test.ts`, `frontend/src/auth/token.test.ts`.

**Interfaces:**
- Produces: `getToken()/setToken(t)/clearToken()` (localStorage `bw_token`); `client` (an `openapi-fetch` client typed by `schema.ts`, with a middleware adding `Authorization: Bearer` and redirecting to `/login` on 401); `API_BASE` constant.

- [ ] **Step 1: Generate the typed schema**

Run (from `frontend/`): `npm run gen:api`. Expected: writes `frontend/openapi.json` (offline, from `create_app().openapi()`) then `src/api/schema.ts` with a `paths` interface covering the 18 routes. Confirm `src/api/schema.ts` exists and exports `paths`.

- [ ] **Step 2: Write `auth/token.ts` + its failing test**

`frontend/src/auth/token.ts`:
```ts
const KEY = "bw_token";
export function getToken(): string | null {
  return typeof window === "undefined" ? null : window.localStorage.getItem(KEY);
}
export function setToken(token: string): void {
  window.localStorage.setItem(KEY, token);
}
export function clearToken(): void {
  window.localStorage.removeItem(KEY);
}
```
`frontend/src/auth/token.test.ts`:
```ts
import { describe, it, expect, beforeEach } from "vitest";
import { getToken, setToken, clearToken } from "./token";
describe("token store", () => {
  beforeEach(() => window.localStorage.clear());
  it("round-trips", () => {
    expect(getToken()).toBeNull();
    setToken("abc");
    expect(getToken()).toBe("abc");
    clearToken();
    expect(getToken()).toBeNull();
  });
});
```

- [ ] **Step 3: Write `api/client.ts` + its failing test**

`frontend/src/api/client.ts`:
```ts
"use client";
import createClient, { type Middleware } from "openapi-fetch";
import type { paths } from "./schema";
import { getToken, clearToken } from "@/auth/token";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

const authMiddleware: Middleware = {
  async onRequest({ request }) {
    const token = getToken();
    if (token) request.headers.set("Authorization", `Bearer ${token}`);
    return request;
  },
  async onResponse({ response }) {
    if (response.status === 401) {
      clearToken();
      if (typeof window !== "undefined") window.location.assign("/login");
    }
    return response;
  },
};

export const client = createClient<paths>({ baseUrl: API_BASE });
client.use(authMiddleware);
```
`frontend/src/api/client.test.ts` — test the middleware in isolation (don't hit the network): re-implement/import the middleware behaviour by exercising `onRequest`/`onResponse`. Simplest: export the middleware for testing.
Add to `client.ts`: `export const _authMiddleware = authMiddleware;`
```ts
import { describe, it, expect, beforeEach, vi } from "vitest";
import { _authMiddleware } from "./client";
import { setToken } from "@/auth/token";

describe("auth middleware", () => {
  beforeEach(() => window.localStorage.clear());
  it("attaches Bearer when a token exists", async () => {
    setToken("tok123");
    const request = new Request("http://x/figures");
    const out = await _authMiddleware.onRequest!({ request } as any);
    expect((out as Request).headers.get("Authorization")).toBe("Bearer tok123");
  });
  it("clears token + redirects on 401", async () => {
    setToken("tok123");
    const assign = vi.fn();
    Object.defineProperty(window, "location", { value: { assign }, writable: true });
    await _authMiddleware.onResponse!({ response: new Response("", { status: 401 }) } as any);
    expect(window.localStorage.getItem("bw_token")).toBeNull();
    expect(assign).toHaveBeenCalledWith("/login");
  });
});
```

- [ ] **Step 4: Run tests + build**

Run (from `frontend/`): `npm test` — token + middleware tests pass. `npm run build` — compiles (schema types resolve). If the exact `Middleware` onRequest/onResponse arg shape differs in openapi-fetch 0.13, adjust the test's call shape to match (this is the one library-API calibration point — confirm against the installed `openapi-fetch` types).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/api/client.test.ts frontend/src/auth/token.ts frontend/src/auth/token.test.ts frontend/src/api/schema.ts
git commit -m "feat(frontend): typed openapi-fetch client + JWT token store + auth middleware"
```
(`schema.ts` is generated but committed so the app builds without regenerating; `openapi.json` stays ignored.)

---

### Task 3: Login page + auth guard

**Files:** Create `frontend/src/auth/guard.tsx`, `frontend/src/app/login/page.tsx`. Test: `frontend/src/app/login/page.test.tsx`.

**Interfaces:**
- Consumes: `setToken`/`getToken` (Task 2), `API_BASE`.
- Produces: `<AuthGuard>` (redirects to `/login` when no token) used by the shell (Task 4); the `/login` page (form → `POST /auth/token` form-encoded → `setToken` → redirect `/`).

- [ ] **Step 1: Write `auth/guard.tsx`**

```tsx
"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getToken } from "./token";

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [ok, setOk] = useState(false);
  useEffect(() => {
    if (!getToken()) router.replace("/login");
    else setOk(true);
  }, [router]);
  return ok ? <>{children}</> : null;
}
```

- [ ] **Step 2: Write `app/login/page.tsx`**

```tsx
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
```

- [ ] **Step 3: Write the login test**

`frontend/src/app/login/page.test.tsx`:
```tsx
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LoginPage from "./page";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace }) }));

describe("LoginPage", () => {
  beforeEach(() => { window.localStorage.clear(); replace.mockClear(); vi.restoreAllMocks(); });

  it("stores the token and redirects on success", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ access_token: "T" }), { status: 200 })));
    render(<LoginPage />);
    await userEvent.type(screen.getByLabelText("username"), "tester");
    await userEvent.type(screen.getByLabelText("password"), "pw");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
    expect(window.localStorage.getItem("bw_token")).toBe("T");
    expect(replace).toHaveBeenCalledWith("/");
  });

  it("shows an error on bad creds", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("", { status: 401 })));
    render(<LoginPage />);
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
    expect(await screen.findByText(/invalid username or password/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 4: Run tests + build; commit**

Run (from `frontend/`): `npm test` (login tests pass) + `npm run build`.
```bash
git add frontend/src/auth/guard.tsx frontend/src/app/login
git commit -m "feat(frontend): login page + auth guard"
```

---

### Task 4: App shell (nav + guard)

**Files:** Create `frontend/src/components/AppShell.tsx`; modify `frontend/src/app/layout.tsx` to wrap non-login routes. (Login must stay outside the guard.)

**Interfaces:**
- Consumes: `AuthGuard` (Task 3), `clearToken`.
- Produces: `<AppShell>` — top nav (Feed/Watchlist/Review/Discovery/Alerts/Impact + Logout) wrapping guarded children. Because `/login` must not be guarded, use a route-group layout: put the shell in a `(dash)` route group layout and move the pages under it, OR have `AppShell` render nothing special for `/login`. SIMPLEST: keep `layout.tsx` minimal (html/body only) and wrap each page's content in `<AppShell>` — but that repeats. PREFERRED: use a Next.js **route group** `src/app/(dash)/layout.tsx` that renders `<AuthGuard><AppShell>{children}</AppShell></AuthGuard>`, and move the guarded pages (`page.tsx` feed, watchlist, review, discovery, alerts, impact) under `src/app/(dash)/`, leaving `login/` at the top level.

- [ ] **Step 1: Write `components/AppShell.tsx`**

```tsx
"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { clearToken } from "@/auth/token";

const NAV = [
  { href: "/", label: "Feed" }, { href: "/watchlist", label: "Watchlist" },
  { href: "/review", label: "Review" }, { href: "/discovery", label: "Discovery" },
  { href: "/alerts", label: "Alerts" }, { href: "/impact", label: "Impact" },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  return (
    <div className="min-h-screen">
      <nav className="flex items-center gap-4 border-b bg-white px-6 py-3">
        <span className="font-semibold">bellwether</span>
        {NAV.map((n) => (
          <Link key={n.href} href={n.href}
                className={pathname === n.href ? "font-medium text-black" : "text-gray-500 hover:text-black"}>
            {n.label}
          </Link>
        ))}
        <button className="ml-auto text-sm text-gray-500 hover:text-black"
                onClick={() => { clearToken(); router.replace("/login"); }}>Logout</button>
      </nav>
      <main className="p-6">{children}</main>
    </div>
  );
}
```

- [ ] **Step 2: Create the `(dash)` route-group layout + move pages**

Create `frontend/src/app/(dash)/layout.tsx`:
```tsx
import { AuthGuard } from "@/auth/guard";
import { AppShell } from "@/components/AppShell";
export default function DashLayout({ children }: { children: React.ReactNode }) {
  return (<AuthGuard><AppShell>{children}</AppShell></AuthGuard>);
}
```
Move `src/app/page.tsx` → `src/app/(dash)/page.tsx` (the placeholder for now; Task 5 replaces it). Later tasks create their pages under `src/app/(dash)/…`. `login/` stays at `src/app/login/`.

- [ ] **Step 3: Build; commit**

Run (from `frontend/`): `npm run build` — routes compile; `/` is guarded, `/login` is not. (Manual: `npm run dev`, visit `/` with no token → redirected to `/login`.)
```bash
git add frontend/src/components/AppShell.tsx "frontend/src/app/(dash)"
git rm frontend/src/app/page.tsx 2>/dev/null || true
git commit -m "feat(frontend): app shell + guarded (dash) route group"
```

---

### Task 5: Live Feed page (`useAlertStream` + signals)

**Files:** Create `frontend/src/hooks/useAlertStream.ts`, `frontend/src/hooks/useApi.ts`, `frontend/src/app/(dash)/page.tsx`. Test: `frontend/src/hooks/useAlertStream.test.ts`.

**Interfaces:**
- Consumes: `client`/`API_BASE` (Task 2), `getToken`.
- Produces: `useApi<T>(key, fetcher)` (thin SWR wrapper); `useAlertStream() -> { alerts, connected }` (EventSource, prepend, cap 100); the Live Feed page (live alerts panel + `GET /signals` table).

- [ ] **Step 1: Write `hooks/useAlertStream.ts` + its failing test**

```ts
"use client";
import { useEffect, useRef, useState } from "react";
import { API_BASE } from "@/api/client";
import { getToken } from "@/auth/token";

export type AlertPayload = { figure?: string; direction?: string; magnitude?: string;
  confidence?: number; text?: string; url?: string };

export function useAlertStream(): { alerts: AlertPayload[]; connected: boolean } {
  const [alerts, setAlerts] = useState<AlertPayload[]>([]);
  const [connected, setConnected] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  useEffect(() => {
    const token = getToken();
    if (!token) return;
    const es = new EventSource(`${API_BASE}/stream?token=${encodeURIComponent(token)}`);
    esRef.current = es;
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.addEventListener("alert", (e) => {
      try { setAlerts((prev) => [JSON.parse((e as MessageEvent).data), ...prev].slice(0, 100)); }
      catch { /* ignore malformed */ }
    });
    return () => es.close();
  }, []);
  return { alerts, connected };
}
```
`frontend/src/hooks/useAlertStream.test.ts` — mock `EventSource`:
```ts
import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useAlertStream } from "./useAlertStream";
import { setToken } from "@/auth/token";

class MockES {
  listeners: Record<string, (e: any) => void> = {};
  onopen: any; onerror: any;
  constructor(public url: string) { MockES.last = this; }
  addEventListener(t: string, cb: (e: any) => void) { this.listeners[t] = cb; }
  close() {}
  static last: MockES;
}

describe("useAlertStream", () => {
  beforeEach(() => { window.localStorage.clear(); vi.stubGlobal("EventSource", MockES as any); });
  it("prepends parsed alert events", () => {
    setToken("T");
    const { result } = renderHook(() => useAlertStream());
    act(() => { MockES.last.listeners["alert"]({ data: JSON.stringify({ figure: "Fed", direction: "up" }) }); });
    expect(result.current.alerts[0].figure).toBe("Fed");
    expect(MockES.last.url).toContain("token=T");
  });
});
```

- [ ] **Step 2: Write `hooks/useApi.ts`**

```ts
"use client";
import useSWR from "swr";
import { client } from "@/api/client";

// Generic SWR read bound to the typed client. `path` is a schema path; returns data|undefined.
export function useApiGet<T>(key: string | null, fetcher: () => Promise<T>) {
  return useSWR<T>(key, key ? fetcher : null);
}
export { client };
```

- [ ] **Step 3: Write the Live Feed page `app/(dash)/page.tsx`**

```tsx
"use client";
import { useState } from "react";
import useSWR from "swr";
import { client } from "@/api/client";
import { useAlertStream } from "@/hooks/useAlertStream";

export default function FeedPage() {
  const { alerts, connected } = useAlertStream();
  const [direction, setDirection] = useState("");
  const { data: signals, isLoading, error } = useSWR(["/signals", direction], async () => {
    const { data } = await client.GET("/signals", { params: { query: direction ? { direction } : {} } });
    return data ?? [];
  });

  return (
    <div className="grid gap-6 md:grid-cols-2">
      <section>
        <h2 className="mb-2 flex items-center gap-2 font-semibold">Live alerts
          <span className={`h-2 w-2 rounded-full ${connected ? "bg-green-500" : "bg-gray-300"}`} /></h2>
        {alerts.length === 0 && <p className="text-sm text-gray-500">No alerts yet — they appear as signals match your rules.</p>}
        <ul className="space-y-2">
          {alerts.map((a, i) => (
            <li key={i} className="rounded border bg-white p-3 text-sm">
              <span className="font-medium">{a.figure}</span> — {a.direction}/{a.magnitude} ({a.confidence?.toFixed(2)})
              <div className="text-gray-600">{a.text}</div>
            </li>
          ))}
        </ul>
      </section>
      <section>
        <h2 className="mb-2 font-semibold">Recent signals</h2>
        <select className="mb-2 rounded border p-1 text-sm" value={direction} onChange={(e) => setDirection(e.target.value)}>
          <option value="">all directions</option><option value="up">up</option>
          <option value="down">down</option><option value="neutral">neutral</option>
        </select>
        {isLoading && <p className="text-sm text-gray-500">Loading…</p>}
        {error && <p className="text-sm text-red-600">Failed to load signals.</p>}
        <ul className="space-y-2">
          {(signals ?? []).map((s: any) => (
            <li key={s.id} className="rounded border bg-white p-3 text-sm">
              {s.direction}/{s.magnitude} · conf {s.confidence?.toFixed?.(2)} · {(s.entities ?? []).join(", ")}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
```

- [ ] **Step 4: Run tests + build; commit**

Run (from `frontend/`): `npm test` (useAlertStream passes) + `npm run build`. If `client.GET("/signals", …)` typing needs a different `params.query` shape, adjust to match `schema.ts` (the query params come from the generated types).
```bash
git add frontend/src/hooks "frontend/src/app/(dash)/page.tsx"
git commit -m "feat(frontend): live feed page (SSE alert stream + signals)"
```

---

### Task 6: Watchlist page

**Files:** Create `frontend/src/components/Badge.tsx`, `frontend/src/app/(dash)/watchlist/page.tsx`.

**Interfaces:**
- Consumes: `client`. Produces: the Watchlist page.

- [ ] **Step 1: `components/Badge.tsx`**

```tsx
export function Badge({ children, tone = "gray" }: { children: React.ReactNode; tone?: "gray" | "green" | "amber" | "red" }) {
  const cls = { gray: "bg-gray-100 text-gray-700", green: "bg-green-100 text-green-700",
    amber: "bg-amber-100 text-amber-700", red: "bg-red-100 text-red-700" }[tone];
  return <span className={`rounded px-2 py-0.5 text-xs ${cls}`}>{children}</span>;
}
```

- [ ] **Step 2: `app/(dash)/watchlist/page.tsx`**

```tsx
"use client";
import { useState } from "react";
import useSWR from "swr";
import { client } from "@/api/client";
import { Badge } from "@/components/Badge";

function SourceList({ figureId }: { figureId: number }) {
  const { data } = useSWR(["/figures/sources", figureId], async () => {
    const { data } = await client.GET("/figures/{figure_id}/sources", { params: { path: { figure_id: figureId } } });
    return data ?? [];
  });
  return (
    <ul className="ml-4 mt-1 space-y-1 text-sm">
      {(data ?? []).map((s: any) => (
        <li key={s.id} className="flex items-center gap-2">
          <span className="text-gray-700">{s.connector_type}</span>
          <Badge tone={s.status === "active" ? "green" : s.status === "pending_review" ? "amber" : "gray"}>{s.status}</Badge>
          {s.discovery_confidence != null && <span className="text-gray-400">conf {s.discovery_confidence.toFixed(2)}</span>}
        </li>
      ))}
      {(data ?? []).length === 0 && <li className="text-gray-400">no sources yet</li>}
    </ul>
  );
}

export default function WatchlistPage() {
  const { data: figures, mutate } = useSWR("/figures", async () => (await client.GET("/figures")).data ?? []);
  const [name, setName] = useState("");

  async function addFigure(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    await client.POST("/figures", { body: { name, type: "individual", discover: true } as any });
    setName(""); mutate();
  }
  async function rediscover(id: number) { await client.POST("/figures/{figure_id}/discover", { params: { path: { figure_id: id } } }); mutate(); }
  async function remove(id: number) { await client.DELETE("/figures/{figure_id}", { params: { path: { figure_id: id } } }); mutate(); }

  return (
    <div>
      <form onSubmit={addFigure} className="mb-4 flex gap-2">
        <input className="rounded border p-2" placeholder="Add a figure by name…" value={name} onChange={(e) => setName(e.target.value)} />
        <button className="rounded bg-black px-4 text-white">Add</button>
      </form>
      <ul className="space-y-3">
        {(figures ?? []).map((f: any) => (
          <li key={f.id} className="rounded border bg-white p-3">
            <div className="flex items-center gap-2">
              <span className="font-medium">{f.name}</span>
              <Badge tone={f.discovery_status === "done" ? "green" : f.discovery_status === "failed" ? "red" : "amber"}>{f.discovery_status}</Badge>
              <button className="ml-auto text-sm text-gray-500 hover:text-black" onClick={() => rediscover(f.id)}>re-discover</button>
              <button className="text-sm text-red-500 hover:text-red-700" onClick={() => remove(f.id)}>delete</button>
            </div>
            <SourceList figureId={f.id} />
          </li>
        ))}
        {(figures ?? []).length === 0 && <li className="text-gray-500">No figures yet — add one above.</li>}
      </ul>
    </div>
  );
}
```

- [ ] **Step 3: Build; commit**

Run (from `frontend/`): `npm run build`. Adjust `params`/`body` shapes to the generated types if tsc complains (the `as any` on the POST body tolerates the discover flag if the generated type lags). Manual: add a figure, watch `discovery_status` + sources.
```bash
git add "frontend/src/app/(dash)/watchlist" frontend/src/components/Badge.tsx
git commit -m "feat(frontend): watchlist page (figures + sources + discovery status)"
```

---

### Task 7: Review-and-correct page

**Files:** Create `frontend/src/app/(dash)/review/page.tsx`.

**Interfaces:** Consumes `client`. Produces the review-and-correct page (`GET /review/queue?module=extract`, `POST /review/{statement_id}`).

- [ ] **Step 1: `app/(dash)/review/page.tsx`**

```tsx
"use client";
import { useState } from "react";
import useSWR from "swr";
import { client } from "@/api/client";

export default function ReviewPage() {
  const { data: queue, mutate } = useSWR("/review/queue", async () =>
    (await client.GET("/review/queue", { params: { query: { module: "extract" } } })).data ?? []);
  const [editing, setEditing] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function submit(id: number, body: any) {
    setErr(null);
    const res = await client.POST("/review/{statement_id}", { params: { path: { statement_id: id } }, body });
    if (res.error) { setErr(typeof res.error === "object" ? JSON.stringify(res.error) : String(res.error)); return; }
    setEditing(null); mutate();
  }

  return (
    <div className="space-y-4">
      <h2 className="font-semibold">Review &amp; correct — extraction golden labels</h2>
      {err && <p className="text-sm text-red-600">{err}</p>}
      {(queue ?? []).length === 0 && <p className="text-gray-500">Review queue empty.</p>}
      {(queue ?? []).map((item: any) => (
        <div key={item.statement_id} className="rounded border bg-white p-4">
          <p className="mb-1 text-sm text-gray-500">{item.figure_name}</p>
          <p className="mb-2">{item.text}</p>
          {item.current_extraction && (
            <p className="mb-2 text-sm text-gray-700">
              model: <b>{item.current_extraction.direction}</b>/{item.current_extraction.magnitude} · conf {item.current_extraction.confidence?.toFixed?.(2)}
              · {(item.current_extraction.entities ?? []).join(", ")}
            </p>
          )}
          {editing === item.statement_id
            ? <CorrectForm item={item} onSubmit={(ext) => submit(item.statement_id, { is_relevant: true, extraction: ext })} onCancel={() => setEditing(null)} />
            : (<div className="flex gap-2">
                <button className="rounded bg-green-600 px-3 py-1 text-sm text-white" onClick={() => submit(item.statement_id, { is_relevant: true })}>Confirm</button>
                <button className="rounded border px-3 py-1 text-sm" onClick={() => setEditing(item.statement_id)}>Correct</button>
                <button className="rounded bg-red-600 px-3 py-1 text-sm text-white" onClick={() => submit(item.statement_id, { is_relevant: false })}>Reject</button>
              </div>)}
        </div>
      ))}
    </div>
  );
}

function CorrectForm({ item, onSubmit, onCancel }: { item: any; onSubmit: (ext: any) => void; onCancel: () => void }) {
  const c = item.current_extraction ?? {};
  const [direction, setDirection] = useState(c.direction ?? "up");
  const [magnitude, setMagnitude] = useState(c.magnitude ?? "moderate");
  const [entities, setEntities] = useState((c.entities ?? []).join(", "));
  const [quote, setQuote] = useState(c.evidence_quote ?? "");
  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <select className="rounded border p-1 text-sm" value={direction} onChange={(e) => setDirection(e.target.value)}>
          <option>up</option><option>down</option><option>neutral</option></select>
        <select className="rounded border p-1 text-sm" value={magnitude} onChange={(e) => setMagnitude(e.target.value)}>
          <option>none</option><option>small</option><option>moderate</option><option>large</option></select>
      </div>
      <input className="w-full rounded border p-1 text-sm" value={entities} onChange={(e) => setEntities(e.target.value)} placeholder="entities, comma-separated" />
      <input className="w-full rounded border p-1 text-sm" value={quote} onChange={(e) => setQuote(e.target.value)} placeholder="evidence quote (must be a verbatim substring)" />
      <div className="flex gap-2">
        <button className="rounded bg-black px-3 py-1 text-sm text-white"
                onClick={() => onSubmit({ direction, magnitude, entities: entities.split(",").map((s) => s.trim()).filter(Boolean), evidence_quote: quote })}>Save</button>
        <button className="rounded border px-3 py-1 text-sm" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Build; commit**

Run (from `frontend/`): `npm run build`. Manual: a non-verbatim evidence quote surfaces the 422 error inline.
```bash
git add "frontend/src/app/(dash)/review"
git commit -m "feat(frontend): review-and-correct page (golden labels: confirm/correct/reject)"
```

---

### Task 8: Discovery review page

**Files:** Create `frontend/src/app/(dash)/discovery/page.tsx`.

**Interfaces:** Consumes `client`. Produces the discovery-review page (`GET /discovery/queue`, `POST /discovery/{source_id}`).

- [ ] **Step 1: `app/(dash)/discovery/page.tsx`**

```tsx
"use client";
import useSWR from "swr";
import { client } from "@/api/client";
import { Badge } from "@/components/Badge";

export default function DiscoveryPage() {
  const { data: queue, mutate } = useSWR("/discovery/queue", async () => (await client.GET("/discovery/queue")).data ?? []);
  async function decide(sourceId: number, decision: "confirm" | "reject") {
    await client.POST("/discovery/{source_id}", { params: { path: { source_id: sourceId } }, body: { decision } });
    mutate();
  }
  return (
    <div className="space-y-3">
      <h2 className="font-semibold">Discovery review — proposed sources</h2>
      {(queue ?? []).length === 0 && <p className="text-gray-500">Nothing awaiting review.</p>}
      {(queue ?? []).map((item: any) => (
        <div key={item.source_id} className="rounded border bg-white p-3">
          <div className="flex items-center gap-2">
            <span className="font-medium">{item.figure_name}</span>
            <span className="text-gray-600">{item.connector_type}</span>
            <span className="text-gray-400 text-sm">{JSON.stringify(item.config)}</span>
            {item.discovery_confidence != null && <Badge tone="amber">conf {item.discovery_confidence.toFixed(2)}</Badge>}
          </div>
          <p className="mt-1 text-xs text-gray-500">why: {JSON.stringify(item.discovery_meta)}</p>
          <div className="mt-2 flex gap-2">
            <button className="rounded bg-green-600 px-3 py-1 text-sm text-white" onClick={() => decide(item.source_id, "confirm")}>Confirm</button>
            <button className="rounded bg-red-600 px-3 py-1 text-sm text-white" onClick={() => decide(item.source_id, "reject")}>Reject</button>
          </div>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: Build; commit**

Run (from `frontend/`): `npm run build`.
```bash
git add "frontend/src/app/(dash)/discovery"
git commit -m "feat(frontend): discovery-review page (confirm/reject proposed sources)"
```

---

### Task 9: Alerts page (ConditionBuilder + CRUD)

**Files:** Create `frontend/src/components/ConditionBuilder.tsx`, `frontend/src/app/(dash)/alerts/page.tsx`. Test: `frontend/src/components/ConditionBuilder.test.tsx`.

**Interfaces:** Consumes `client`. Produces `ConditionBuilder` (emits a `condition` object, omitting empty fields) + the alerts page (`GET/POST/PATCH/DELETE /alert_rules`).

- [ ] **Step 1: `components/ConditionBuilder.tsx` + its failing test**

```tsx
"use client";
import { useState } from "react";

export type Condition = { min_confidence?: number; min_magnitude?: string; directions?: string[]; figure_ids?: number[] };

export function ConditionBuilder({ onChange }: { onChange: (c: Condition) => void }) {
  const [minConf, setMinConf] = useState("");
  const [minMag, setMinMag] = useState("");
  const [dirs, setDirs] = useState<string[]>([]);
  function emit(next: Partial<{ minConf: string; minMag: string; dirs: string[] }>) {
    const mc = next.minConf ?? minConf, mm = next.minMag ?? minMag, d = next.dirs ?? dirs;
    const c: Condition = {};
    if (mc !== "") c.min_confidence = Number(mc);
    if (mm !== "") c.min_magnitude = mm;
    if (d.length) c.directions = d;
    onChange(c);
  }
  function toggleDir(dir: string) {
    const d = dirs.includes(dir) ? dirs.filter((x) => x !== dir) : [...dirs, dir];
    setDirs(d); emit({ dirs: d });
  }
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <label>min confidence <input aria-label="min_confidence" type="number" step="0.1" min="0" max="1"
        className="w-20 rounded border p-1" value={minConf} onChange={(e) => { setMinConf(e.target.value); emit({ minConf: e.target.value }); }} /></label>
      <label>min magnitude
        <select aria-label="min_magnitude" className="rounded border p-1" value={minMag} onChange={(e) => { setMinMag(e.target.value); emit({ minMag: e.target.value }); }}>
          <option value="">any</option><option value="small">small</option><option value="moderate">moderate</option><option value="large">large</option></select></label>
      {["up", "down", "neutral"].map((d) => (
        <label key={d}><input type="checkbox" checked={dirs.includes(d)} onChange={() => toggleDir(d)} /> {d}</label>
      ))}
    </div>
  );
}
```
`frontend/src/components/ConditionBuilder.test.tsx`:
```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ConditionBuilder } from "./ConditionBuilder";

describe("ConditionBuilder", () => {
  it("emits only the set fields", async () => {
    const onChange = vi.fn();
    render(<ConditionBuilder onChange={onChange} />);
    await userEvent.type(screen.getByLabelText("min_confidence"), "0.7");
    await userEvent.click(screen.getByLabelText("up"));
    const last = onChange.mock.calls.at(-1)![0];
    expect(last).toEqual({ min_confidence: 0.7, directions: ["up"] });
    expect(last).not.toHaveProperty("min_magnitude");
  });
});
```

- [ ] **Step 2: `app/(dash)/alerts/page.tsx`**

```tsx
"use client";
import { useState } from "react";
import useSWR from "swr";
import { client } from "@/api/client";
import { ConditionBuilder, type Condition } from "@/components/ConditionBuilder";
import { Badge } from "@/components/Badge";

export default function AlertsPage() {
  const { data: rules, mutate } = useSWR("/alert_rules", async () => (await client.GET("/alert_rules")).data ?? []);
  const [name, setName] = useState("");
  const [webhook, setWebhook] = useState("");
  const [condition, setCondition] = useState<Condition>({});

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    await client.POST("/alert_rules", { body: { name, condition, webhook_url: webhook || null, enabled: true } as any });
    setName(""); setWebhook(""); mutate();
  }
  async function toggle(id: number, enabled: boolean) { await client.PATCH("/alert_rules/{rule_id}", { params: { path: { rule_id: id } }, body: { enabled } as any }); mutate(); }
  async function remove(id: number) { await client.DELETE("/alert_rules/{rule_id}", { params: { path: { rule_id: id } } }); mutate(); }

  return (
    <div>
      <form onSubmit={create} className="mb-6 space-y-2 rounded border bg-white p-4">
        <div className="flex gap-2">
          <input className="rounded border p-2" placeholder="rule name" value={name} onChange={(e) => setName(e.target.value)} />
          <input className="flex-1 rounded border p-2" placeholder="webhook URL (optional)" value={webhook} onChange={(e) => setWebhook(e.target.value)} />
        </div>
        <ConditionBuilder onChange={setCondition} />
        <button className="rounded bg-black px-4 py-1 text-white">Create rule</button>
      </form>
      <ul className="space-y-2">
        {(rules ?? []).map((r: any) => (
          <li key={r.id} className="flex items-center gap-2 rounded border bg-white p-3">
            <span className="font-medium">{r.name}</span>
            <Badge tone={r.enabled ? "green" : "gray"}>{r.enabled ? "enabled" : "disabled"}</Badge>
            <span className="text-xs text-gray-500">{JSON.stringify(r.condition)}</span>
            <button className="ml-auto text-sm text-gray-500 hover:text-black" onClick={() => toggle(r.id, !r.enabled)}>{r.enabled ? "disable" : "enable"}</button>
            <button className="text-sm text-red-500 hover:text-red-700" onClick={() => remove(r.id)}>delete</button>
          </li>
        ))}
        {(rules ?? []).length === 0 && <li className="text-gray-500">No rules yet.</li>}
      </ul>
    </div>
  );
}
```

- [ ] **Step 3: Run tests + build; commit**

Run (from `frontend/`): `npm test` (ConditionBuilder passes) + `npm run build`.
```bash
git add "frontend/src/app/(dash)/alerts" frontend/src/components/ConditionBuilder.tsx frontend/src/components/ConditionBuilder.test.tsx
git commit -m "feat(frontend): alerts page + condition builder (rule CRUD)"
```

---

### Task 10: Impact & Leaderboard page

**Files:** Create `frontend/src/app/(dash)/impact/page.tsx`.

**Interfaces:** Consumes `client`. Produces the impact page (`GET /leaderboard`, `GET /impacts`).

- [ ] **Step 1: `app/(dash)/impact/page.tsx`**

```tsx
"use client";
import useSWR from "swr";
import { client } from "@/api/client";

export default function ImpactPage() {
  const { data: board } = useSWR("/leaderboard", async () => (await client.GET("/leaderboard")).data ?? []);
  const { data: impacts } = useSWR("/impacts", async () => (await client.GET("/impacts")).data ?? []);
  return (
    <div className="grid gap-6 md:grid-cols-2">
      <section>
        <h2 className="mb-2 font-semibold">Leaderboard — market impact by figure</h2>
        <table className="w-full text-sm">
          <thead><tr className="text-left text-gray-500"><th>figure</th><th>n</th><th>avg move</th><th>avg |move|</th><th>hit rate</th></tr></thead>
          <tbody>
            {(board ?? []).map((r: any) => (
              <tr key={r.figure_id} className="border-t">
                <td>{r.figure_name}</td><td>{r.n}</td>
                <td>{r.avg_pct_move?.toFixed?.(2)}</td><td>{r.avg_abs_pct_move?.toFixed?.(2)}</td>
                <td>{(r.directional_hit_rate * 100).toFixed(0)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
        {(board ?? []).length === 0 && <p className="text-sm text-gray-500">No measured impacts yet.</p>}
      </section>
      <section>
        <h2 className="mb-2 font-semibold">Measured impacts</h2>
        <ul className="space-y-1 text-sm">
          {(impacts ?? []).map((i: any) => (
            <li key={i.id} className="rounded border bg-white p-2">
              {i.symbol} · {i.window} · {i.status} · move {i.pct_move != null ? i.pct_move.toFixed(2) : "—"}
            </li>
          ))}
        </ul>
        {(impacts ?? []).length === 0 && <p className="text-sm text-gray-500">No impacts yet.</p>}
      </section>
    </div>
  );
}
```

- [ ] **Step 2: Build; full test run; commit**

Run (from `frontend/`): `npm run build` + `npm test` (all unit tests green).
```bash
git add "frontend/src/app/(dash)/impact"
git commit -m "feat(frontend): impact + leaderboard page"
```

- [ ] **Step 3: Manual end-to-end verification (pre-merge)**

With the backend running (`uvicorn`, the workers) and `CORS_ORIGINS` including `http://localhost:3000`, run `npm run dev` and walk the flow: login → feed streams a real alert (trigger one via the alert worker) → add a figure → discovery fills sources → review-and-correct an extraction → confirm a discovered source → create a rule → check the leaderboard. Note the result in the final review.

---

## Self-Review

**Spec coverage (7b spec):**
- Scaffold (§3) — Task 1 ✓ (Next+TS+Tailwind+Vitest, CORS `.env` line).
- Typed client from OpenAPI + token store + auth middleware (§3, §4) — Task 2 ✓ (offline codegen via `create_app().openapi()`).
- Login + guard (§4, §5) — Task 3 ✓.
- App shell + guarded route group (§5) — Task 4 ✓ (`(dash)` group so `/login` is public).
- Live Feed: SSE + signals (§5, §6) — Task 5 ✓ (`useAlertStream` tested).
- Watchlist + async discovery status (§5, §6) — Task 6 ✓.
- Review-and-correct incl. verbatim-422 inline (§5) — Task 7 ✓.
- Discovery review (§5) — Task 8 ✓.
- Alerts + ConditionBuilder (§5) — Task 9 ✓ (builder tested).
- Impact/Leaderboard (§5) — Task 10 ✓ + the manual e2e (§7).
- **Testing (§7):** logic units — token, middleware, login, useAlertStream, ConditionBuilder; pages via build + manual e2e. ✓
- **Deferred (§9):** eval panel, optimistic UI, multi-user, deploy — no task, correct.

**Deliberate flags for the reviewer:**
- **`npm install`/`build`/`test` need a real toolchain + network** — those steps run unsandboxed; a subagent that can't reach the npm registry must report BLOCKED (not fake green). This is the one environmental dependency.
- **Two library-API calibration points** (flagged in-step): the exact `openapi-fetch` `Middleware` arg shape (Task 2 Step 4) and the generated `params.query`/`body` shapes on `client.GET/POST` (Tasks 5/6/9) — adjust to the installed `openapi-fetch@0.13`/generated `schema.ts` if tsc/tests disagree. The generated `schema.ts` is the source of truth for request/response types.
- Pages are verified by `npm run build` (tsc) + the manual e2e, not unit tests — per the design's testing decision.

**Placeholder scan:** every step has complete code or a concrete command; no TBD. The two calibration points are explicit "adjust to the installed types" seams, not placeholders.

**Type consistency:** `getToken/setToken/clearToken` (Task 2) are used by the middleware, guard, login, and `useAlertStream` (Tasks 2/3/5). `client` + `API_BASE` (Task 2) are consumed by every page + the stream hook. `<AuthGuard>` + `<AppShell>` (Tasks 3/4) wrap the `(dash)` pages (Tasks 5–10). `Condition` (Task 9) matches the backend `AlertCondition` fields. `Badge` (Task 6) is reused in Tasks 8/9. Each page's endpoints match the real API surface (18 paths, verified).
