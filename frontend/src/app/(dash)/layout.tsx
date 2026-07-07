import { AuthGuard } from "@/auth/guard";
import { AppShell } from "@/components/AppShell";
export default function DashLayout({ children }: { children: React.ReactNode }) {
  return (<AuthGuard><AppShell>{children}</AppShell></AuthGuard>);
}
