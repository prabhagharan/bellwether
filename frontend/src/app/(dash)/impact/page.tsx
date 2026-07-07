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
