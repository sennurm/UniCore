"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getToken, setToken } from "@/lib/api";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [ready, setReady] = useState(false);

  // Auth guard: no session token -> straight to sign-in, never a raw API error.
  useEffect(() => {
    if (!getToken()) router.replace("/login");
    else setReady(true);
  }, [router]);

  if (!ready) return null;

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
