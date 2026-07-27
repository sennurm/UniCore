"use client";

import { useState } from "react";
import { api } from "@/lib/api";

type Grant = {
  id: string;
  role_code: string;
  org_unit_id: string | null;
  status: string;
  term_code: string | null;
  revoke_cause: string | null;
};

const ROLES = [
  "system-admin", "chancellor", "vc", "registrar", "dean-academic-affairs",
  "faculty-dean", "school-incharge", "hod", "class-incharge", "professor",
  "associate-professor", "assistant-professor", "tutor", "assistant-teaching-staff",
  "timetable-cell", "exam-cell", "controller-of-examination", "school-admin",
  "subject-coordinator", "subject-author", "school-exam-coordinator", "hr-designate",
];

export default function GrantsPage() {
  const [form, setForm] = useState({ user_id: "", role_code: "hod", org_unit_id: "", term_code: "" });
  const [lookupId, setLookupId] = useState("");
  const [grants, setGrants] = useState<Grant[] | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  async function issue(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setMessage("");
    try {
      await api("/rbac/grants", {
        method: "POST",
        body: {
          user_id: form.user_id,
          role_code: form.role_code,
          org_unit_id: form.org_unit_id || null,
          term_code: form.term_code || null,
        },
      });
      setMessage("Grant issued.");
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  async function lookup(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      setGrants(await api<Grant[]>(`/rbac/users/${lookupId}/grants`));
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  return (
    <>
      <h1>Roles &amp; grants</h1>
      <div className="panel">
        <h2>Issue grant</h2>
        <form onSubmit={issue}>
          <label>User id</label>
          <input value={form.user_id} onChange={(e) => setForm({ ...form, user_id: e.target.value })} required />
          <label>Role</label>
          <select value={form.role_code} onChange={(e) => setForm({ ...form, role_code: e.target.value })}>
            {ROLES.map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
          <label>Org unit id (empty for university-scope roles)</label>
          <input value={form.org_unit_id} onChange={(e) => setForm({ ...form, org_unit_id: e.target.value })} />
          <label>Term code (class-incharge only, e.g. 2026-S1)</label>
          <input value={form.term_code} onChange={(e) => setForm({ ...form, term_code: e.target.value })} />
          <button type="submit">Issue</button>
        </form>
        {message && <p>{message}</p>}
      </div>
      <div className="panel">
        <h2>User grants</h2>
        <form onSubmit={lookup}>
          <label>User id</label>
          <input value={lookupId} onChange={(e) => setLookupId(e.target.value)} required />
          <button type="submit" className="secondary">Look up</button>
        </form>
        {grants && (
          <table>
            <thead>
              <tr><th>Role</th><th>Unit</th><th>Status</th><th>Term</th><th>Revoke cause</th></tr>
            </thead>
            <tbody>
              {grants.map((g) => (
                <tr key={g.id}>
                  <td>{g.role_code}</td>
                  <td>{g.org_unit_id ?? "university"}</td>
                  <td>{g.status}</td>
                  <td>{g.term_code ?? "—"}</td>
                  <td>{g.revoke_cause ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {error && <p className="error">{error}</p>}
    </>
  );
}
