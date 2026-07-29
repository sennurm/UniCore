"use client";

import { useCallback, useEffect, useState } from "react";
import { api, upload } from "@/lib/api";
import TemplateLinks from "@/components/TemplateLinks";

type Unit = {
  id: string;
  type: string;
  name: string;
  code: string;
  path: string;
  status: string;
  level: string | null;
  duration_years: number | null;
  mode: string | null;
  category: string | null;
  industry_partner: string | null;
  internship_months: number | null;
  lateral_entry_semester: number | null;
  auto_created: boolean;
};

type ImportResult = {
  rows_created: number;
  rows_updated: number;
  rows_unchanged: number;
  rows_rejected: number;
  errors: { row_number: number; field: string; reason: string; raw_row: string }[];
};

const UNIT_TYPES = ["faculty_division", "school", "department", "program"];
const LEVELS = [
  "Under Graduate",
  "Post Graduate",
  "PhD (Full-Time)",
  "PhD (Part-Time)",
  "Diploma/Certificate",
];
const CATEGORIES = [
  "Standard",
  "Industry Collaborated",
  "Industry Integrated",
  "Research",
];

export default function OrgPage() {
  const [units, setUnits] = useState<Unit[]>([]);
  const [typeFilter, setTypeFilter] = useState("");
  const [search, setSearch] = useState("");
  const [includeInactive, setIncludeInactive] = useState(false);
  const [editing, setEditing] = useState<Unit | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [adding, setAdding] = useState(false);
  const [newRow, setNewRow] = useState({ type: "faculty_division", code: "", name: "", parent_id: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const params = new URLSearchParams();
      if (typeFilter) params.set("unit_type", typeFilter);
      if (search) params.set("search", search);
      if (includeInactive) params.set("include_inactive", "true");
      setUnits(await api<Unit[]>(`/org/units?${params}`));
    } catch (err) {
      setError(String((err as Error).message));
    }
  }, [typeFilter, search, includeInactive]);

  useEffect(() => {
    void load();
  }, [load]);

  async function saveEdit(unit: Unit) {
    setError("");
    try {
      await api(`/org/units/${unit.id}`, {
        method: "PUT",
        body: {
          name: unit.name,
          level: unit.level,
          duration_years: unit.duration_years,
          mode: unit.mode,
          category: unit.category,
          industry_partner: unit.industry_partner,
          internship_months: unit.internship_months,
          lateral_entry_semester: unit.lateral_entry_semester,
        },
      });
      setEditing(null);
      await load();
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  async function toggleActive(unit: Unit) {
    setError("");
    const action = unit.status === "active" ? "deactivate" : "reactivate";
    try {
      await api(`/org/units/${unit.id}/${action}`, { method: "POST" });
      await load();
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  async function addRow(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api("/org/units", {
        method: "POST",
        body: { ...newRow, parent_id: newRow.parent_id || null },
      });
      setNewRow({ type: "faculty_division", code: "", name: "", parent_id: "" });
      setAdding(false);
      await load();
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  async function importCsv(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError("");
    setImportResult(null);
    try {
      const form = new FormData();
      form.append("file", file);
      setImportResult(await upload<ImportResult>("/org/imports", form));
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
        <h3 style={{ margin: 0 }}>Org structure</h3>
        <div className="uc-screen-sub">
          One university · Faculty Divisions → Schools → Departments → Programmes ·
          Departments marked <span className="tag tag-neutral">default</span> were created by the
          importer for Schools that have none · units are deactivated, never deleted
        </div>
      </div>

      <div className="card">
        <div style={{ display: "flex", gap: 12, alignItems: "flex-end", flexWrap: "wrap" }}>
          <div className="field" style={{ marginBottom: 0, maxWidth: 220 }}>
            <label>Search name or code</label>
            <input className="input" value={search} onChange={(e) => setSearch(e.target.value)} />
          </div>
          <div className="field" style={{ marginBottom: 0, maxWidth: 200 }}>
            <label>Type</label>
            <select className="input" value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
              <option value="">All</option>
              {UNIT_TYPES.map((t) => <option key={t}>{t}</option>)}
            </select>
          </div>
          <label className="card-meta" style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input type="checkbox" checked={includeInactive}
              onChange={(e) => setIncludeInactive(e.target.checked)} />
            Show deactivated
          </label>
          <div style={{ flex: 1 }} />
          <button className="btn btn-secondary" onClick={() => setAdding((v) => !v)}>
            {adding ? "Cancel" : "+ Add unit"}
          </button>
        </div>

        {adding && (
          <form onSubmit={addRow} style={{ marginTop: 12, display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
            <div className="field" style={{ marginBottom: 0, maxWidth: 180 }}>
              <label>Type</label>
              <select className="input" value={newRow.type}
                onChange={(e) => setNewRow({ ...newRow, type: e.target.value })}>
                {UNIT_TYPES.map((t) => <option key={t}>{t}</option>)}
              </select>
            </div>
            <div className="field" style={{ marginBottom: 0, maxWidth: 140 }}>
              <label>Code</label>
              <input className="input" value={newRow.code}
                onChange={(e) => setNewRow({ ...newRow, code: e.target.value })} required />
            </div>
            <div className="field" style={{ marginBottom: 0, maxWidth: 240 }}>
              <label>Name</label>
              <input className="input" value={newRow.name}
                onChange={(e) => setNewRow({ ...newRow, name: e.target.value })} required />
            </div>
            <div className="field" style={{ marginBottom: 0, maxWidth: 300 }}>
              <label>Parent unit id</label>
              <input className="input" value={newRow.parent_id}
                onChange={(e) => setNewRow({ ...newRow, parent_id: e.target.value })} required />
            </div>
            <button className="btn btn-primary" type="submit">Create</button>
          </form>
        )}

        <table className="table" style={{ marginTop: 12 }}>
          <thead>
            <tr>
              <th>Type</th><th>Code</th><th>Name</th>
              <th>Level</th><th>Yrs</th><th>Mode</th>
              <th>Category</th><th>Partner</th><th>Intern</th><th>Lateral</th>
              <th>Status</th><th />
            </tr>
          </thead>
          <tbody>
            {units.map((u) => {
              const isEditing = editing?.id === u.id;
              const row = isEditing ? editing : u;
              return (
                <tr key={u.id} style={{ opacity: u.status === "active" ? 1 : 0.5 }}>
                  <td><span className="tag tag-outline">{u.type}</span></td>
                  <td>{u.code}</td>
                  <td>
                    {isEditing ? (
                      <input className="input" value={row.name}
                        onChange={(e) => setEditing({ ...row, name: e.target.value })} />
                    ) : (
                      <>
                        {u.name}
                        {u.auto_created && (
                          <span className="tag tag-neutral" style={{ marginLeft: 6 }}
                            title="Created by the importer to carry Programmes for a School that has no Departments">
                            default
                          </span>
                        )}
                        <div style={{ fontSize: 10, opacity: 0.45 }}>{u.path}</div>
                      </>
                    )}
                  </td>
                  <td>
                    {isEditing && u.type === "program" ? (
                      <select className="input" value={row.level ?? ""}
                        onChange={(e) => setEditing({ ...row, level: e.target.value })}>
                        <option value="">—</option>
                        {LEVELS.map((l) => <option key={l}>{l}</option>)}
                      </select>
                    ) : (u.level ?? "—")}
                  </td>
                  <td>
                    {isEditing && u.type === "program" ? (
                      <input className="input" type="number" min={1} max={10}
                        value={row.duration_years ?? ""}
                        onChange={(e) => setEditing({ ...row, duration_years: Number(e.target.value) })} />
                    ) : (u.duration_years ?? "—")}
                  </td>
                  <td>
                    {isEditing && u.type === "program" ? (
                      <select className="input" value={row.mode ?? ""}
                        onChange={(e) => setEditing({ ...row, mode: e.target.value })}>
                        <option value="">—</option>
                        <option>Full-Time</option>
                        <option>Part-Time</option>
                      </select>
                    ) : (u.mode ?? "—")}
                  </td>
                  <td>
                    {isEditing && u.type === "program" ? (
                      <select className="input" value={row.category ?? ""}
                        onChange={(e) => setEditing({ ...row, category: e.target.value })}>
                        <option value="">—</option>
                        {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
                      </select>
                    ) : (u.category ?? "—")}
                  </td>
                  <td>
                    {isEditing && u.type === "program" ? (
                      <input className="input" value={row.industry_partner ?? ""}
                        onChange={(e) => setEditing({ ...row, industry_partner: e.target.value })} />
                    ) : (u.industry_partner ?? "—")}
                  </td>
                  <td>
                    {isEditing && u.type === "program" ? (
                      <input className="input" type="number" min={0} max={36}
                        value={row.internship_months ?? ""}
                        onChange={(e) => setEditing({
                          ...row,
                          internship_months: e.target.value ? Number(e.target.value) : null,
                        })} />
                    ) : (u.internship_months ? `${u.internship_months} mo` : "—")}
                  </td>
                  <td>
                    {isEditing && u.type === "program" ? (
                      <input className="input" type="number" min={1} max={12}
                        value={row.lateral_entry_semester ?? ""}
                        onChange={(e) => setEditing({
                          ...row,
                          lateral_entry_semester: e.target.value ? Number(e.target.value) : null,
                        })} />
                    ) : (u.lateral_entry_semester ? `sem ${u.lateral_entry_semester}` : "—")}
                  </td>
                  <td>
                    <span className={u.status === "active" ? "tag tag-accent" : "tag tag-neutral"}>
                      {u.status}
                    </span>
                  </td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    {isEditing ? (
                      <>
                        <button className="btn btn-ghost" onClick={() => void saveEdit(row)}>Save</button>
                        <button className="btn btn-ghost" onClick={() => setEditing(null)}>Cancel</button>
                      </>
                    ) : (
                      <>
                        <button className="btn btn-ghost" onClick={() => setEditing(u)}>Edit</button>
                        <button className="btn btn-ghost" onClick={() => void toggleActive(u)}>
                          {u.status === "active" ? "Deactivate" : "Restore"}
                        </button>
                      </>
                    )}
                  </td>
                </tr>
              );
            })}
            {units.length === 0 && (
              <tr><td colSpan={12} className="card-meta">No units match these filters.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="card" style={{ maxWidth: 520 }}>
        <div className="card-kicker">Bulk upload · course catalogue</div>
        <p className="card-meta">
          One row per Programme with its Faculty Division, School and Department as columns —
          missing ancestors are created automatically.
        </p>
        <form onSubmit={importCsv}>
          <div className="field">
            <label>CSV file</label>
            <input className="input" type="file" accept=".csv,text/csv"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)} required />
          </div>
          <button className="btn btn-primary" type="submit" disabled={busy || !file}>
            {busy ? "Importing…" : "Import catalogue"}
          </button>
        </form>
        {importResult && (
          <p className="card-meta">
            {importResult.rows_created} created · {importResult.rows_updated} updated ·{" "}
            {importResult.rows_unchanged} unchanged · {importResult.rows_rejected} rejected
          </p>
        )}
        {importResult && importResult.errors.length > 0 && (
          <table className="table">
            <thead><tr><th>Row</th><th>Field</th><th>Reason</th></tr></thead>
            <tbody>
              {importResult.errors.map((e, i) => (
                <tr key={i}>
                  <td>{e.row_number}</td>
                  <td><span className="tag tag-outline">{e.field}</span></td>
                  <td>{e.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <TemplateLinks only={["org-structure"]} />
      {error && <p className="error">{error}</p>}
    </div>
  );
}
