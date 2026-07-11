"use client";
import { useState } from "react";
import useSWR from "swr";
import { client } from "@/api/client";
import { Badge } from "@/components/Badge";

// Pull the meaningful identifier out of a source's connector config so the
// user can see WHAT a source points to (feed URL, X handle, page URL), not
// just its type. Returns a link href when the detail is navigable.
function sourceDetail(s: any): { text: string; href?: string } | null {
  const c = s.config ?? {};
  if (c.feed_url) return { text: c.feed_url, href: c.feed_url };
  if (c.handle) return { text: `@${c.handle}`, href: `https://x.com/${c.handle}` };
  if (c.url) return { text: c.url, href: c.url };
  if (c.platform) return { text: c.note ? `${c.platform} — ${c.note}` : c.platform };
  const keys = Object.keys(c);
  return keys.length ? { text: keys.map((k) => `${k}: ${c[k]}`).join(", ") } : null;
}

function SourceList({ figureId }: { figureId: number }) {
  const { data } = useSWR(["/figures/sources", figureId], async () => {
    const { data } = await client.GET("/figures/{figure_id}/sources", { params: { path: { figure_id: figureId } } });
    return data ?? [];
  });
  return (
    <ul className="ml-4 mt-1 space-y-1.5 text-sm">
      {(data ?? []).map((s: any) => {
        const detail = sourceDetail(s);
        return (
          <li key={s.id}>
            <div className="flex items-center gap-2">
              <span className="text-gray-700">{s.connector_type}</span>
              <Badge tone={s.status === "active" ? "green" : s.status === "pending_review" ? "amber" : "gray"}>{s.status}</Badge>
              {s.discovery_confidence != null && <span className="text-gray-400">conf {s.discovery_confidence.toFixed(2)}</span>}
            </div>
            {detail && (
              <div className="truncate text-xs text-gray-500" title={detail.text}>
                {detail.href ? (
                  <a href={detail.href} target="_blank" rel="noreferrer" className="hover:underline">{detail.text}</a>
                ) : (
                  detail.text
                )}
              </div>
            )}
          </li>
        );
      })}
      {(data ?? []).length === 0 && <li className="text-gray-400">no sources yet</li>}
    </ul>
  );
}

function errText(e: unknown): string {
  return typeof e === "object" ? JSON.stringify(e) : String(e);
}

export default function WatchlistPage() {
  const { data: figures, mutate, error: loadError } = useSWR("/figures", async () => (await client.GET("/figures")).data ?? []);
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);

  async function addFigure(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setErr(null);
    const res = await client.POST("/figures", { body: { name, type: "individual", discover: true } as any });
    if (res.error) { setErr(errText(res.error)); return; }
    setName(""); mutate();
  }
  async function rediscover(id: number) {
    setErr(null);
    const res = await client.POST("/figures/{figure_id}/discover", { params: { path: { figure_id: id } } });
    if (res.error) { setErr(errText(res.error)); return; }
    mutate();
  }
  async function remove(id: number) {
    setErr(null);
    const res = await client.DELETE("/figures/{figure_id}", { params: { path: { figure_id: id } } });
    if (res.error) { setErr(errText(res.error)); return; }
    mutate();
  }

  return (
    <div>
      <form onSubmit={addFigure} className="mb-4 flex gap-2">
        <input className="rounded border p-2" placeholder="Add a figure by name…" value={name} onChange={(e) => setName(e.target.value)} />
        <button className="rounded bg-black px-4 text-white">Add</button>
      </form>
      {err && <p className="mb-3 text-sm text-red-600">{err}</p>}
      {loadError && <p className="mb-3 text-sm text-red-600">Failed to load figures.</p>}
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
