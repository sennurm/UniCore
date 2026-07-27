"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { setToken } from "@/lib/api";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  return (
    <>
      <nav>
        <Link href="/dashboard">Org structure</Link>
        <Link href="/dashboard/users">Users</Link>
        <Link href="/dashboard/grants">Roles &amp; grants</Link>
        <Link href="/dashboard/audit">Audit log</Link>
        <a
          href="/login"
          onClick={(e) => {
            e.preventDefault();
            setToken(null);
            router.push("/login");
          }}
        >
          Sign out
        </a>
      </nav>
      <main>{children}</main>
    </>
  );
}
