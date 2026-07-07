"use client";
import { useState } from "react";
import useSWR from "swr";
import { client } from "@/api/client";
import type { components } from "@/api/schema";

type ReviewSubmit = components["schemas"]["ReviewSubmit"];

export default function ReviewPage() {
  const { data: queue, mutate } = useSWR("/review/queue", async () =>
    (await client.GET("/review/queue", { params: { query: { module: "extract" } } })).data ?? []);
  const [editing, setEditing] = useState<number | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function submit(id: number, body: ReviewSubmit) {
    setErr(null);
    const res = await client.POST("/review/{statement_id}", { params: { path: { statement_id: id } }, body });
    if (res.error) { const e = res.error; setErr(typeof e === "object" ? JSON.stringify(e) : String(e)); return; }
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
                onClick={() => onSubmit({ direction, magnitude, entities: entities.split(",").map((s: string) => s.trim()).filter(Boolean), evidence_quote: quote })}>Save</button>
        <button className="rounded border px-3 py-1 text-sm" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}
