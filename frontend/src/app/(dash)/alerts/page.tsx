"use client";
import { useState } from "react";
import useSWR from "swr";
import { client } from "@/api/client";
import { ConditionBuilder, type Condition } from "@/components/ConditionBuilder";
import { Badge } from "@/components/Badge";

function errText(e: unknown): string {
  return typeof e === "object" ? JSON.stringify(e) : String(e);
}

export default function AlertsPage() {
  const { data: rules, mutate, error: loadError } = useSWR("/alert_rules", async () => (await client.GET("/alert_rules")).data ?? []);
  const [name, setName] = useState("");
  const [webhook, setWebhook] = useState("");
  const [condition, setCondition] = useState<Condition>({});
  const [err, setErr] = useState<string | null>(null);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    setErr(null);
    const res = await client.POST("/alert_rules", { body: { name, condition, webhook_url: webhook || null, enabled: true } as any });
    if (res.error) { setErr(errText(res.error)); return; }
    setName(""); setWebhook(""); mutate();
  }
  async function toggle(id: number, enabled: boolean) {
    setErr(null);
    const res = await client.PATCH("/alert_rules/{rule_id}", { params: { path: { rule_id: id } }, body: { enabled } as any });
    if (res.error) { setErr(errText(res.error)); return; }
    mutate();
  }
  async function remove(id: number) {
    setErr(null);
    const res = await client.DELETE("/alert_rules/{rule_id}", { params: { path: { rule_id: id } } });
    if (res.error) { setErr(errText(res.error)); return; }
    mutate();
  }

  return (
    <div>
      {err && <p className="mb-3 text-sm text-red-600">{err}</p>}
      {loadError && <p className="mb-3 text-sm text-red-600">Failed to load rules.</p>}
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
