"use client";
import { useState } from "react";

const TONE: Record<string, string> = {
  up: "bg-green-100 text-green-800",
  down: "bg-red-100 text-red-800",
  neutral: "bg-gray-100 text-gray-700",
};

export function SignalItem({ signal }: { signal: any }) {
  const [open, setOpen] = useState(false);
  const headline = (signal.text ?? "").split("\n")[0] || "(no text)";
  const date = signal.published_at ? new Date(signal.published_at).toLocaleDateString() : "";

  return (
    <li className="rounded border bg-white text-sm">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 p-3 text-left"
      >
        <span className="text-gray-400">{open ? "▾" : "▸"}</span>
        <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs ${TONE[signal.direction] ?? TONE.neutral}`}>
          {signal.direction}/{signal.magnitude}
        </span>
        <span className="flex-1 truncate">{headline}</span>
        <span className="shrink-0 text-gray-400">conf {signal.confidence?.toFixed?.(2)}</span>
      </button>
      {open && (
        <div className="space-y-1 border-t px-3 pb-3 pt-2">
          <div className="text-xs text-gray-500">
            {signal.source_type} · {signal.figure_name}{date ? ` · ${date}` : ""}
          </div>
          {signal.url && (
            <a href={signal.url} target="_blank" rel="noreferrer"
               className="block text-blue-600 hover:underline">
              {headline} ↗
            </a>
          )}
          {signal.evidence_quote && (
            <p className="italic text-gray-500">"{signal.evidence_quote}"</p>
          )}
          {(signal.entities ?? []).length > 0 && (
            <div className="text-xs text-gray-500">{(signal.entities ?? []).join(", ")}</div>
          )}
        </div>
      )}
    </li>
  );
}
