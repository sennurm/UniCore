"use client";

import { useCallback, useEffect, useState } from "react";
import { api, downloadFile, upload } from "@/lib/api";

type RoleEntry = { grant_id: string; role_code: string; unit_path: string | null; spec: string };
type DirectoryRow = {
  user_id: string;
  username: string;
  full_name: string;
  kind: string;
  status: string;
  sif_id: string | null;
  enrollment_id: string | null;
  roles: RoleEntry[];
};

type RoleImportResult = {
  grants_issued: number;
  grants_revoked: number;
  rows_unchanged: number;
  rows_rejected: number;
  errors: { row_number: number; username: string; reason: string }[];
};

/** Fetched from the registry, never hardcoded — a literal list here had already
 *  drifted (it omitted super-admin) and would silently miss every role added. */
type Role = {
  code: string;
  name: string;
  unit_type: string;
  singleton: boolean;
  term_bound: boolean;
};

export default function UsersAndRolesPage() {
  const [roles, setRoles] = useState<Role[]>([]);
  const [rows, setRows] = useState<DirectoryRow[]>([]);
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [grantFor, setGrantFor] = useState<DirectoryRow | null>(null);
  const [grantForm, setGrantForm] = useState({ role_code: "", org_unit_id: "", term_code: "" });
  const [file, setFile] = useState<File | null>(null);
  const [importResult, setImportResult] = useState<RoleImportResult | null>(null);
  const [creating, setCreating] = useState(false);
  const [newUser, setNewUser] = useState({
    username: "", full_name: "", kind: "staff", sif_id: "", mobile: "",
  });
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const params = new URLSearchParams();
      if (search) params.set("search", search);
      if (roleFilter) params.set("role_code", roleFilter);
      if (statusFilter) params.set("status", statusFilter);
      setRows(await api<DirectoryRow[]>(`/rbac/directory?${params}`));
    } catch (err) {
      setError(String((err as Error).message));
    }
  }, [search, roleFilter, statusFilter]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    api<Role[]>("/rbac/roles")
      .then(setRoles)
      .catch((err) => setError(String((err as Error).message)));
  }, []);

  async function provision(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setMessage("");
    try {
      const user = await api<{ id: string }>("/user", {
        method: "POST",
        body: {
          username: newUser.username,
          full_name: newUser.full_name,
          kind: newUser.kind,
          sif_id: newUser.sif_id || null,
          mobile: newUser.mobile || null,
        },
      });
      const issued = await api<{ delivered_via: string }>(
        `/auth/users/${user.id}/temp-password`, { method: "POST" },
      );
      setMessage(`Provisioned ${newUser.username}; temp password sent via ${issued.delivered_via}`);
      setNewUser({ username: "", full_name: "", kind: "staff", sif_id: "", mobile: "" });
      setCreating(false);
      await load();
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  async function issueGrant(e: React.FormEvent) {
    e.preventDefault();
    if (!grantFor) return;
    setError("");
    try {
      await api("/rbac/grants", {
        method: "POST",
        body: {
          user_id: grantFor.user_id,
          role_code: grantForm.role_code,
          org_unit_id: grantForm.org_unit_id || null,
          term_code: grantForm.term_code || null,
        },
      });
      setGrantFor(null);
      await load();
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  async function revoke(entry: RoleEntry) {
    setError("");
    try {
      await api(`/rbac/grants/${entry.grant_id}/revoke`, {
        method: "POST",
        body: { reason: "revoked from Users & roles table" },
      });
      await load();
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  async function importRoles(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError("");
    setImportResult(null);
    try {
      const form = new FormData();
      form.append("file", file);
      setImportResult(await upload<RoleImportResult>("/rbac/directory/imports", form));
      await load();
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="uc-screen">
      <div>
        <h3 style={{ margin: 0 }}>Users &amp; roles</h3>
        <div className="uc-screen-sub">
          Every account with its active role grants · export, edit the roles column, upload back
        </div>
      </div>

      <div className="card">
        <div style={{ display: "flex", gap: 12, alignItems: "flex-end", flexWrap: "wrap" }}>
          <div className="field" style={{ marginBottom: 0, maxWidth: 220 }}>
            <label>Search name, username, SIF or enrollment id</label>
            <input className="input" value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
          <div className="field" style={{ marginBottom: 0, maxWidth: 200 }}>
            <label>Role</label>
            <select className="input" value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)}>
              <option value="">All roles</option>
              {roles.map((r) => <option key={r.code} value={r.code}>{r.name} ({r.unit_type})</option>)}
            </select>
          </div>
          <div className="field" style={{ marginBottom: 0, maxWidth: 160 }}>
            <label>Status</label>
            <select className="input" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
              <option value="">All</option>
              <option value="active">active</option>
              <option value="imported">imported</option>
              <option value="deactivated">deactivated</option>
              <option value="withdrawn">withdrawn</option>
            </select>
          </div>
          <div style={{ flex: 1 }} />
          <button
            className="btn btn-secondary"
            onClick={() =>
              void downloadFile("/rbac/directory.csv", "unicore_users_roles.csv").catch((err) =>
                setError(String((err as Error).message)),
              )
            }
          >
            Download CSV
          </button>
          <button className="btn btn-secondary" onClick={() => setCreating((v) => !v)}>
            {creating ? "Cancel" : "+ Add user"}
          </button>
        </div>

        {creating && (
          <form onSubmit={provision} style={{ marginTop: 12, display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
            <div className="field" style={{ marginBottom: 0, maxWidth: 170 }}>
              <label>Username</label>
              <input className="input" value={newUser.username}
                onChange={(e) => setNewUser({ ...newUser, username: e.target.value })} required />
            </div>
            <div className="field" style={{ marginBottom: 0, maxWidth: 200 }}>
              <label>Full name</label>
              <input className="input" value={newUser.full_name}
                onChange={(e) => setNewUser({ ...newUser, full_name: e.target.value })} required />
            </div>
            <div className="field" style={{ marginBottom: 0, maxWidth: 130 }}>
              <label>Kind</label>
              <select className="input" value={newUser.kind}
                onChange={(e) => setNewUser({ ...newUser, kind: e.target.value })}>
                <option value="staff">staff</option>
                <option value="student">student</option>
              </select>
            </div>
            <div className="field" style={{ marginBottom: 0, maxWidth: 160 }}>
              <label>Mobile</label>
              <input className="input" value={newUser.mobile}
                onChange={(e) => setNewUser({ ...newUser, mobile: e.target.value })} />
            </div>
            <button className="btn btn-primary" type="submit">Provision</button>
          </form>
        )}

        <table className="table" style={{ marginTop: 12 }}>
          <thead>
            <tr>
              <th>Username</th><th>Name</th><th>Enrollment / SIF</th>
              <th>Kind</th><th>Status</th><th>Roles</th><th />
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.user_id}>
                <td>{r.username}</td>
                <td>{r.full_name}</td>
                <td>
                  {r.enrollment_id && <strong>{r.enrollment_id}</strong>}
                  {r.sif_id && (
                    <div style={{ fontSize: 11, opacity: 0.55 }}>{r.sif_id}</div>
                  )}
                  {!r.enrollment_id && !r.sif_id && "—"}
                </td>
                <td>{r.kind}</td>
                <td>
                  <span className={r.status === "active" ? "tag tag-accent" : "tag tag-neutral"}>
                    {r.status}
                  </span>
                </td>
                <td>
                  {r.roles.length === 0 && <span className="card-meta">no roles</span>}
                  {r.roles.map((entry) => (
                    <span key={entry.grant_id} className="tag tag-outline"
                      style={{ marginRight: 6, marginBottom: 3, display: "inline-flex", gap: 6 }}>
                      {entry.role_code}
                      {entry.unit_path && (
                        <span style={{ opacity: 0.6 }}>@{entry.unit_path}</span>
                      )}
                      <button className="btn btn-ghost" style={{ padding: "0 4px", margin: 0 }}
                        title="Revoke" onClick={() => void revoke(entry)}>×</button>
                    </span>
                  ))}
                </td>
                <td style={{ whiteSpace: "nowrap" }}>
                  <button className="btn btn-ghost" onClick={() => setGrantFor(r)}>+ Role</button>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={7} className="card-meta">No users match these filters.</td></tr>
            )}
          </tbody>
        </table>
        {message && <p className="card-meta">{message}</p>}
      </div>

      {grantFor && (
        <div className="card" style={{ maxWidth: 460 }}>
          <div className="card-kicker">Grant role · {grantFor.username}</div>
          <form onSubmit={issueGrant}>
            <div className="field">
              <label>Role</label>
              <select className="input" value={grantForm.role_code} required
                onChange={(e) => setGrantForm({ ...grantForm, role_code: e.target.value })}>
                <option value="">— select a role —</option>
                {roles.map((r) => <option key={r.code} value={r.code}>{r.name} ({r.unit_type})</option>)}
              </select>
            </div>
            <div className="field">
              <label>
                {(() => {
                  const unit = roles.find((r) => r.code === grantForm.role_code)?.unit_type;
                  if (!unit) return "Org unit id";
                  return unit === "university"
                    ? "Org unit id — leave blank, this role is university-scoped"
                    : `Org unit id — must be a ${unit.replace("_", " ")}`;
                })()}
              </label>
              <input className="input" value={grantForm.org_unit_id}
                onChange={(e) => setGrantForm({ ...grantForm, org_unit_id: e.target.value })} />
            </div>
            <div className="field">
              <label>Term code (class-incharge only)</label>
              <input className="input" value={grantForm.term_code}
                onChange={(e) => setGrantForm({ ...grantForm, term_code: e.target.value })} />
            </div>
            <button className="btn btn-primary" type="submit">Issue grant</button>
            <button className="btn btn-ghost" type="button" onClick={() => setGrantFor(null)}>Cancel</button>
          </form>
        </div>
      )}

      <div className="card" style={{ maxWidth: 520 }}>
        <div className="card-kicker">Bulk role update</div>
        <p className="card-meta">
          Download the CSV above, edit the <strong>roles</strong> column
          (<code>role@org.unit.path</code>, separated by <code>;</code>), then upload it back.
          Roles you add are granted; roles you remove are revoked. Everything goes through the
          normal singleton, scope and audit rules.
        </p>
        <form onSubmit={importRoles}>
          <div className="field">
            <label>Edited users &amp; roles CSV</label>
            <input className="input" type="file" accept=".csv,text/csv"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)} required />
          </div>
          <button className="btn btn-primary" type="submit" disabled={busy || !file}>
            {busy ? "Applying…" : "Apply role changes"}
          </button>
        </form>
        {importResult && (
          <p className="card-meta">
            {importResult.grants_issued} granted · {importResult.grants_revoked} revoked ·{" "}
            {importResult.rows_unchanged} unchanged · {importResult.rows_rejected} rejected
          </p>
        )}
        {importResult && importResult.errors.length > 0 && (
          <table className="table">
            <thead><tr><th>Row</th><th>User</th><th>Reason</th></tr></thead>
            <tbody>
              {importResult.errors.map((e, i) => (
                <tr key={i}><td>{e.row_number}</td><td>{e.username}</td><td>{e.reason}</td></tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {error && <p className="error">{error}</p>}
    </div>
  );
}
