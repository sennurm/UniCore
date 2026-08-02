"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, getToken, setToken, validateSession } from "@/lib/api";

const NAV = [
  { num: "01", label: "Org structure", href: "/dashboard" },
  { num: "02", label: "Terms & sections", href: "/dashboard/terms" },
  { num: "03", label: "Timetable", href: "/dashboard/timetable" },
  { num: "04", label: "Student import", href: "/dashboard/onboarding" },
  { num: "05", label: "Users & roles", href: "/dashboard/users" },
  { num: "06", label: "Audit log", href: "/dashboard/audit" },
];

type Me = { user_id: string; username: string; full_name: string; roles: string[] };

function initials(name: string | undefined): string {
  if (!name) return "··";
  return name
    .split(/\s+/)
    .map((part) => part[0] ?? "")
    .join("")
    .slice(0, 2)
    .toUpperCase();
}

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [ready, setReady] = useState(false);
  const [me, setMe] = useState<Me | null>(null);

  // Auth guard. A token in localStorage proves nothing — it may be expired or
  // revoked (deactivation kills sessions server-side), so validate it with the
  // server before rendering anything. An invalid session lands on /login rather
  // than a half-rendered dashboard full of failed requests.
  useEffect(() => {
    let cancelled = false;
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    void (async () => {
      const valid = await validateSession();
      if (cancelled) return;
      if (!valid) {
        router.replace("/login");
        return;
      }
      setReady(true);
      api<Me>("/auth/me")
        .then((data) => {
          if (!cancelled) setMe(data);
        })
        .catch(() => undefined);
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  if (!ready) return null;

  return (
    <div className="uc-shell">
      <header className="uc-header">
        <div className="uc-brand">
          <div className="uc-brand-name">UniCore</div>
          <div className="uc-brand-sub">University operations core</div>
        </div>
        <span className="tag tag-neutral">Odd Term 2026–27 · IST</span>
        <div style={{ flex: 1 }} />
        <button
          className="btn btn-ghost"
          onClick={() => {
            setToken(null);
            router.push("/login");
          }}
        >
          Sign out
        </button>
        <div className="uc-user">
          <div className="uc-avatar">{me ? initials(me.full_name) : "··"}</div>
          <div style={{ lineHeight: 1.25 }}>
            <div style={{ fontSize: 13, fontWeight: 500 }}>{me?.full_name ?? me?.username ?? "…"}</div>
            <div style={{ fontSize: 11, opacity: 0.55 }}>
              {me?.roles?.length ? me.roles.join(" · ") : (me?.username ?? "")}
            </div>
          </div>
        </div>
      </header>
      <div className="uc-body">
        <nav className="uc-sidebar">
          <div className="uc-sidebar-heading">Administration</div>
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="uc-nav-item"
              aria-current={pathname === item.href ? "page" : undefined}
            >
              <span className="num">{item.num}</span>
              {item.label}
            </Link>
          ))}
        </nav>
        <main className="uc-main">{children}</main>
      </div>
    </div>
  );
}
