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
    <>
      <h1>Provision user</h1>
      <div className="panel">
        <form onSubmit={provision}>
          <label>Username</label>
          <input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required />
          <label>Full name</label>
          <input value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} required />
          <label>Kind</label>
          <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
            <option value="staff">staff</option>
            <option value="student">student</option>
          </select>
          <label>ERP id (students)</label>
          <input value={form.erp_id} onChange={(e) => setForm({ ...form, erp_id: e.target.value })} />
          <label>Mobile (for OTP/credentials)</label>
          <input value={form.mobile} onChange={(e) => setForm({ ...form, mobile: e.target.value })} />
          <button type="submit">Provision + send temp password</button>
        </form>
        {created && (
          <p>
            Created <strong>{created.username}</strong> ({created.id}), status {created.status}
            {delivery && <> — temp password delivered via {delivery}</>}
          </p>
        )}
        {error && <p className="error">{error}</p>}
      </div>
    </>
  );
}
