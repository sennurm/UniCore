"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

type AuditEvent = {
  id: string;
  occurred_at: string;
  actor: string;
  action: string;
  object_type: string;
  object_id: string;
  scope: string | null;
  reason: string | null;
};

export default function AuditPage() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [action, setAction] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const query = action ? `?action=${encodeURIComponent(action)}` : "";
      setEvents(await api<AuditEvent[]>(`/audit/events${query}`));
    } catch (err) {
      setError(String((err as Error).message));
    }
  }, [action]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      <h1>Audit log (read-only)</h1>
      <div className="panel">
        <label>Filter by action (e.g. rbac.grant.issued)</label>
        <input value={action} onChange={(e) => setAction(e.target.value)} />
        <button onClick={() => void load()} className="secondary">Refresh</button>
        <table>
          <thead>
            <tr><th>When (IST view)</th><th>Actor</th><th>Action</th><th>Object</th><th>Scope</th></tr>
          </thead>
          <tbody>
            {events.map((e) => (
              <tr key={e.id}>
                <td>{new Date(e.occurred_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })}</td>
                <td>{e.actor.slice(0, 8)}…</td>
                <td>{e.action}</td>
                <td>{e.object_type}: {e.object_id.slice(0, 8)}…</td>
                <td>{e.scope ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {error && <p className="error">{error}</p>}
      </div>
    </>
  );
}
