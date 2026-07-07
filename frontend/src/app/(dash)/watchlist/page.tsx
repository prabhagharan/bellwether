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
