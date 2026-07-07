"use client";
import { useState } from "react";
import useSWR from "swr";
import { client } from "@/api/client";
import { Badge } from "@/components/Badge";

export default function DiscoveryPage() {
  const { data: queue, mutate, error: loadError } = useSWR("/discovery/queue", async () => (await client.GET("/discovery/queue")).data ?? []);
  const [err, setErr] = useState<string | null>(null);
  async function decide(sourceId: number, decision: "confirm" | "reject") {
    setErr(null);
    const res = await client.POST("/discovery/{source_id}", { params: { path: { source_id: sourceId } }, body: { decision } });
    if (res.error) { const e = res.error; setErr(typeof e === "object" ? JSON.stringify(e) : String(e)); return; }
    mutate();
  }
  return (
    <div className="space-y-3">
      <h2 className="font-semibold">Discovery review — proposed sources</h2>
      {err && <p className="text-sm text-red-600">{err}</p>}
      {loadError && <p className="text-sm text-red-600">Failed to load the review queue.</p>}
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
