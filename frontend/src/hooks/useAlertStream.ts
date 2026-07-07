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
