"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { clearToken } from "@/auth/token";

const NAV = [
  { href: "/", label: "Feed" }, { href: "/watchlist", label: "Watchlist" },
  { href: "/review", label: "Review" }, { href: "/discovery", label: "Discovery" },
  { href: "/alerts", label: "Alerts" }, { href: "/impact", label: "Impact" },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  return (
    <div className="min-h-screen">
      <nav className="flex items-center gap-4 border-b bg-white px-6 py-3">
        <span className="font-semibold">bellwether</span>
        {NAV.map((n) => (
          <Link key={n.href} href={n.href}
                className={pathname === n.href ? "font-medium text-black" : "text-gray-500 hover:text-black"}>
            {n.label}
          </Link>
        ))}
        <button className="ml-auto text-sm text-gray-500 hover:text-black"
                onClick={() => { clearToken(); router.replace("/login"); }}>Logout</button>
      </nav>
      <main className="p-6">{children}</main>
    </div>
  );
}
