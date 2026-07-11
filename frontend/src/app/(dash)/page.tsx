"use client";
import { useState } from "react";
import useSWR from "swr";
import { client } from "@/api/client";
import { useAlertStream } from "@/hooks/useAlertStream";
import { SignalItem } from "@/components/SignalItem";

const PAGE = 25;

export default function FeedPage() {
  const { alerts, connected } = useAlertStream();
  const [direction, setDirection] = useState("");
  const [offset, setOffset] = useState(0);
  const { data: signals, isLoading, error } = useSWR(["/signals", direction, offset], async () => {
    const query = { limit: PAGE, offset, ...(direction ? { direction } : {}) };
    const { data } = await client.GET("/signals", { params: { query: query as any } });
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
        <select className="mb-2 rounded border p-1 text-sm" value={direction}
                onChange={(e) => { setDirection(e.target.value); setOffset(0); }}>
          <option value="">all directions</option><option value="up">up</option>
          <option value="down">down</option><option value="neutral">neutral</option>
        </select>
        {isLoading && <p className="text-sm text-gray-500">Loading…</p>}
        {error && <p className="text-sm text-red-600">Failed to load signals.</p>}
        <ul className="space-y-2">
          {(signals ?? []).map((s: any) => (
            <SignalItem key={s.id} signal={s} />
          ))}
        </ul>
        <div className="mt-3 flex items-center gap-3 text-sm">
          <button type="button" className="rounded border px-2 py-1 disabled:opacity-40"
                  disabled={offset === 0}
                  onClick={() => setOffset(Math.max(0, offset - PAGE))}>Prev</button>
          <span className="text-gray-500">Page {offset / PAGE + 1}</span>
          <button type="button" className="rounded border px-2 py-1 disabled:opacity-40"
                  disabled={(signals?.length ?? 0) < PAGE}
                  onClick={() => setOffset(offset + PAGE)}>Next</button>
        </div>
      </section>
    </div>
  );
}
