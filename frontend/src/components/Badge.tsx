export function Badge({ children, tone = "gray" }: { children: React.ReactNode; tone?: "gray" | "green" | "amber" | "red" }) {
  const cls = { gray: "bg-gray-100 text-gray-700", green: "bg-green-100 text-green-700",
    amber: "bg-amber-100 text-amber-700", red: "bg-red-100 text-red-700" }[tone];
  return <span className={`rounded px-2 py-0.5 text-xs ${cls}`}>{children}</span>;
}
