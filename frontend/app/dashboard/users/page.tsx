"use client";

import { useState } from "react";
import { api } from "@/lib/api";

type User = {
  id: string;
  username: string;
  full_name: string;
  kind: string;
  status: string;
  erp_id: string | null;
};

export default function UsersPage() {
  const [form, setForm] = useState({
    username: "",
    full_name: "",
    kind: "staff",
    erp_id: "",
    mobile: "",
  });
  const [created, setCreated] = useState<User | null>(null);
  const [delivery, setDelivery] = useState("");
  const [error, setError] = useState("");

  async function provision(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setDelivery("");
    try {
      const user = await api<User>("/user", {
        method: "POST",
        body: {
          username: form.username,
          full_name: form.full_name,
          kind: form.kind,
          erp_id: form.erp_id || null,
          mobile: form.mobile || null,
        },
      });
      setCreated(user);
      const issued = await api<{ delivered_via: string }>(
        `/auth/users/${user.id}/temp-password`,
        { method: "POST" },
      );
      setDelivery(issued.delivered_via);
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  return (
    <div className="uc-screen">
      <div>
        <h3 style={{ margin: 0 }}>Provision user</h3>
        <div className="uc-screen-sub">
          Accounts are provisioned only — never self-created · temp credential delivered on creation
        </div>
      </div>
      <div className="card" style={{ maxWidth: 420 }}>
        <div className="card-kicker">New account</div>
        <form onSubmit={provision}>
          <div className="field">
            <label>Username</label>
            <input className="input" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required />
          </div>
          <div className="field">
            <label>Full name</label>
            <input className="input" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} required />
          </div>
          <div className="field">
            <label>Kind</label>
            <select className="input" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
              <option value="staff">staff</option>
              <option value="student">student</option>
            </select>
          </div>
          <div className="field">
            <label>ERP id (students)</label>
            <input className="input" value={form.erp_id} onChange={(e) => setForm({ ...form, erp_id: e.target.value })} />
          </div>
          <div className="field">
            <label>Mobile (for OTP/credentials)</label>
            <input className="input" value={form.mobile} onChange={(e) => setForm({ ...form, mobile: e.target.value })} />
          </div>
          <button className="btn btn-primary" type="submit">Provision + send temp password</button>
        </form>
        {created && (
          <p className="card-meta">
            Created <strong>{created.username}</strong> ({created.id}), status{" "}
            <span className="tag tag-accent">{created.status}</span>
            {delivery && <> — temp password delivered via {delivery}</>}
          </p>
        )}
        {error && <p className="error">{error}</p>}
      </div>
    </div>
  );
}
