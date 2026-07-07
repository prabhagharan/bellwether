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
