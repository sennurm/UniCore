"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { api, upload } from "@/lib/api";
import TemplateLinks from "@/components/TemplateLinks";

type Unit = {
  id: string;
  type: string;
  name: string;
  code: string;
  parent_id: string | null;
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

/** The hierarchy is fixed, so a child's type follows from its parent's — no type picker. */
const CHILD_OF: Record<string, string | null> = {
  university: "faculty_division",
  faculty_division: "school",
  school: "department",
  department: "program",
  program: null, // Sections are per-term instances created by the Timetable Cell
};

const TYPE_LABEL: Record<string, string> = {
  university: "University",
  faculty_division: "Faculty",
  school: "School",
  department: "Department",
  program: "Programme",
  section: "Section",
};

const LEVELS = [
  "Under Graduate",
  "Post Graduate",
  "PhD (Full-Time)",
  "PhD (Part-Time)",
  "Diploma/Certificate",
];
const CATEGORIES = ["Standard", "Industry Collaborated", "Industry Integrated", "Research"];

export default function OrgPage() {
  const [units, setUnits] = useState<Unit[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [showInactive, setShowInactive] = useState(false);
  const [draft, setDraft] = useState<Partial<Unit>>({});
  const [addForm, setAddForm] = useState({ code: "", name: "" });
  const [adding, setAdding] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    try {
      const params = new URLSearchParams({ limit: "2000" });
      if (showInactive) params.set("include_inactive", "true");
      setUnits(await api<Unit[]>(`/org/units?${params}`));
    } catch (err) {
      setError(String((err as Error).message));
    }
  }, [showInactive]);

  useEffect(() => {
    void load();
  }, [load]);

  // ── derived structure: one pass over the flat list builds the whole tree ───
  const { childrenOf, root, byId, descendantCount, programmeCount } = useMemo(() => {
    const childrenOf = new Map<string, Unit[]>();
    const byId = new Map<string, Unit>();
    let root: Unit | null = null;
    for (const u of units) {
      byId.set(u.id, u);
      if (u.parent_id === null) root = u;
      else {
        const list = childrenOf.get(u.parent_id) ?? [];
        list.push(u);
        childrenOf.set(u.parent_id, list);
      }
    }
    childrenOf.forEach((list) => list.sort((a, b) => a.name.localeCompare(b.name)));

    const descendantCount = new Map<string, number>();
    const programmeCount = new Map<string, number>();
    const walk = (u: Unit): [number, number] => {
      let total = 0;
      let programmes = u.type === "program" ? 1 : 0;
      for (const c of childrenOf.get(u.id) ?? []) {
        const [t, p] = walk(c);
        total += t + 1;
        programmes += p;
      }
      descendantCount.set(u.id, total);
      programmeCount.set(u.id, programmes);
      return [total, programmes];
    };
    if (root) walk(root);
    return { childrenOf, root, byId, descendantCount, programmeCount };
  }, [units]);

  const stats = useMemo(() => {
    const count = (t: string) => units.filter((u) => u.type === t).length;
    return {
      faculties: count("faculty_division"),
      schools: count("school"),
      departments: count("department"),
      programmes: count("program"),
      defaults: units.filter((u) => u.auto_created).length,
    };
  }, [units]);

  /** Search matches, plus every ancestor so a match stays reachable in the tree. */
  const visible = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return null;
    const keep = new Set<string>();
    for (const u of units) {
      if (!u.name.toLowerCase().includes(term) && !u.code.toLowerCase().includes(term)) continue;
      keep.add(u.id);
      let parent = u.parent_id;
      while (parent) {
        keep.add(parent);
        parent = byId.get(parent)?.parent_id ?? null;
      }
    }
    return keep;
  }, [search, units, byId]);

  useEffect(() => {
    if (visible) setExpanded(new Set(visible)); // auto-open the path to every match
    else if (root) setExpanded(new Set([root.id]));
  }, [visible, root]);

  const selected = selectedId ? byId.get(selectedId) ?? null : null;
  const childType = selected ? CHILD_OF[selected.type] : null;
  const isProgramme = selected?.type === "program";

  useEffect(() => {
    setDraft(selected ? { ...selected } : {});
    setAdding(false);
    setAddForm({ code: "", name: "" });
    setNotice("");
  }, [selected]);

  const breadcrumb = useMemo(() => {
    if (!selected) return [];
    const chain: Unit[] = [];
    let node: Unit | undefined = selected;
    while (node) {
      chain.unshift(node);
      node = node.parent_id ? byId.get(node.parent_id) : undefined;
    }
    return chain;
  }, [selected, byId]);

  // ── actions ───────────────────────────────────────────────────────────────
  function toggle(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function saveDetail() {
    if (!selected) return;
    setError("");
    setNotice("");
    try {
      await api(`/org/units/${selected.id}`, {
        method: "PUT",
        body: {
          name: draft.name,
          level: draft.level,
          duration_years: draft.duration_years,
          mode: draft.mode,
          category: draft.category,
          industry_partner: draft.industry_partner,
          internship_months: draft.internship_months,
          lateral_entry_semester: draft.lateral_entry_semester,
        },
      });
      setNotice("Saved.");
      await load();
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  async function toggleActive() {
    if (!selected) return;
    setError("");
    try {
      const action = selected.status === "active" ? "deactivate" : "reactivate";
      await api(`/org/units/${selected.id}/${action}`, { method: "POST" });
      if (action === "deactivate" && !showInactive) setSelectedId(selected.parent_id);
      await load();
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  async function createChild(e: React.FormEvent) {
    e.preventDefault();
    if (!selected || !childType) return;
    setError("");
    try {
      const created = await api<Unit>("/org/units", {
        method: "POST",
        body: { type: childType, code: addForm.code, name: addForm.name, parent_id: selected.id },
      });
      setAdding(false);
      setAddForm({ code: "", name: "" });
      const parentId = selected.id;
      await load();
      setExpanded((prev) => new Set(prev).add(parentId));
      setSelectedId(created.id);
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

  // ── tree ──────────────────────────────────────────────────────────────────
  function renderNode(unit: Unit): React.ReactNode {
    if (visible && !visible.has(unit.id)) return null;
    const kids = (childrenOf.get(unit.id) ?? []).filter((c) => !visible || visible.has(c.id));
    const isOpen = expanded.has(unit.id);
    const programmes = programmeCount.get(unit.id) ?? 0;

    return (
      <li key={unit.id}>
        <button
          className={`org-node${unit.status === "active" ? "" : " is-inactive"}`}
          aria-selected={unit.id === selectedId}
          onClick={() => {
            setSelectedId(unit.id);
            if (kids.length) toggle(unit.id);
          }}
        >
          <span className="twisty">{kids.length ? (isOpen ? "▾" : "▸") : "·"}</span>
          <span className="label">{unit.name}</span>
          {unit.auto_created && <span className="org-type">default</span>}
          <span className="org-type">{TYPE_LABEL[unit.type]}</span>
          {unit.type !== "program" && programmes > 0 && <span className="count">{programmes}</span>}
        </button>
        {isOpen && kids.length > 0 && <ul>{kids.map(renderNode)}</ul>}
      </li>
    );
  }

  return (
    <div className="uc-screen" style={{ maxWidth: 1180 }}>
      <div>
        <h3 style={{ margin: 0 }}>Org structure</h3>
        <div className="uc-screen-sub">
          One university · browse the hierarchy, edit a unit, or add one beneath the selected node
        </div>
      </div>

      <div className="org-stats">
        {([
          ["Faculties", stats.faculties],
          ["Schools", stats.schools],
          ["Departments", stats.departments],
          ["Programmes", stats.programmes],
        ] as [string, number][]).map(([label, n]) => (
          <div className="org-stat" key={label}>
            <div className="n">{n}</div>
            <div className="l">{label}</div>
          </div>
        ))}
        <div className="org-stat">
          <div className="n">{stats.defaults}</div>
          <div className="l" title="Departments created automatically for Schools that have none">
            Default depts
          </div>
        </div>
      </div>

      <div style={{ display: "flex", gap: 12, alignItems: "flex-end", flexWrap: "wrap" }}>
        <div className="field" style={{ marginBottom: 0, maxWidth: 260 }}>
          <label>Find a unit by name or code</label>
          <input
            className="input"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="e.g. Pharmacy, BT-CSE"
          />
        </div>
        <label className="card-meta" style={{ display: "flex", gap: 6, alignItems: "center" }}>
          <input
            type="checkbox"
            checked={showInactive}
            onChange={(e) => setShowInactive(e.target.checked)}
          />
          Show deactivated
        </label>
        <div style={{ flex: 1 }} />
        <button className="btn btn-secondary" onClick={() => setShowImport((v) => !v)}>
          {showImport ? "Close import" : "Bulk import"}
        </button>
      </div>

      {showImport && (
        <>
          <div className="card">
            <div className="card-kicker">Bulk import · course catalogue</div>
            <p className="card-meta">
              One row per Programme with its Faculty, School and Department as columns. Missing
              ancestors are created automatically; Schools without departments get a default one.
            </p>
            <form onSubmit={importCsv} style={{ display: "flex", gap: 10, alignItems: "flex-end" }}>
              <div className="field" style={{ marginBottom: 0, flex: 1, maxWidth: 320 }}>
                <label>CSV file</label>
                <input
                  className="input"
                  type="file"
                  accept=".csv,text/csv"
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                  required
                />
              </div>
              <button className="btn btn-primary" type="submit" disabled={busy || !file}>
                {busy ? "Importing…" : "Import"}
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
                <thead>
                  <tr><th>Row</th><th>Field</th><th>Reason</th></tr>
                </thead>
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
        </>
      )}

      <div className="org-split">
        <div className="card">
          <div className="card-kicker">Hierarchy</div>
          {root ? (
            <ul className="org-tree">{renderNode(root)}</ul>
          ) : (
            <p className="card-meta">
              No university yet — run bootstrap, then import the course catalogue.
            </p>
          )}
          {visible && visible.size === 0 && (
            <p className="card-meta">Nothing matches “{search}”.</p>
          )}
        </div>

        <div className="card">
          {!selected ? (
            <div className="org-empty">
              Select a unit on the left to view and edit it,
              <br />
              or to add a new one beneath it.
            </div>
          ) : (
            <>
              <div className="org-crumb">
                {breadcrumb.map((b, i) => (
                  <span key={b.id}>
                    {i > 0 && " › "}
                    {i === breadcrumb.length - 1 ? <strong>{b.name}</strong> : b.name}
                  </span>
                ))}
              </div>
              <div className="org-detail-head">
                <h4>{selected.name}</h4>
                <span className="tag tag-outline">{TYPE_LABEL[selected.type]}</span>
                <span className={selected.status === "active" ? "tag tag-accent" : "tag tag-neutral"}>
                  {selected.status}
                </span>
                {selected.auto_created && (
                  <span
                    className="tag tag-neutral"
                    title="Created automatically for a School with no departments"
                  >
                    default
                  </span>
                )}
                <div style={{ flex: 1 }} />
                {selected.parent_id && (
                  <button className="btn btn-ghost" onClick={() => void toggleActive()}>
                    {selected.status === "active" ? "Deactivate" : "Restore"}
                  </button>
                )}
              </div>
              <p className="card-meta" style={{ marginTop: -4 }}>
                Code <strong>{selected.code}</strong> · {descendantCount.get(selected.id) ?? 0} units
                beneath · code is immutable (it is embedded in descendant paths)
              </p>

              <div className="org-form-grid">
                <div className="field">
                  <label>Name</label>
                  <input
                    className="input"
                    value={draft.name ?? ""}
                    onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                  />
                </div>
                {isProgramme && (
                  <>
                    <div className="field">
                      <label>Level</label>
                      <select
                        className="input"
                        value={draft.level ?? ""}
                        onChange={(e) => setDraft({ ...draft, level: e.target.value || null })}
                      >
                        <option value="">—</option>
                        {LEVELS.map((l) => <option key={l}>{l}</option>)}
                      </select>
                    </div>
                    <div className="field">
                      <label>Mode</label>
                      <select
                        className="input"
                        value={draft.mode ?? ""}
                        onChange={(e) => setDraft({ ...draft, mode: e.target.value || null })}
                      >
                        <option value="">—</option>
                        <option>Full-Time</option>
                        <option>Part-Time</option>
                      </select>
                    </div>
                    <div className="field">
                      <label>Category</label>
                      <select
                        className="input"
                        value={draft.category ?? ""}
                        onChange={(e) => setDraft({ ...draft, category: e.target.value || null })}
                      >
                        <option value="">—</option>
                        {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
                      </select>
                    </div>
                    <div className="field">
                      <label>Industry partner</label>
                      <input
                        className="input"
                        value={draft.industry_partner ?? ""}
                        onChange={(e) =>
                          setDraft({ ...draft, industry_partner: e.target.value || null })
                        }
                      />
                    </div>
                    <div className="field">
                      <label>Academic years</label>
                      <input
                        className="input"
                        type="number"
                        min={1}
                        max={10}
                        value={draft.duration_years ?? ""}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            duration_years: e.target.value ? Number(e.target.value) : null,
                          })
                        }
                      />
                    </div>
                    <div className="field">
                      <label>Internship (months)</label>
                      <input
                        className="input"
                        type="number"
                        min={0}
                        max={36}
                        value={draft.internship_months ?? ""}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            internship_months: e.target.value ? Number(e.target.value) : null,
                          })
                        }
                      />
                    </div>
                    <div className="field">
                      <label>Lateral entry (semester)</label>
                      <input
                        className="input"
                        type="number"
                        min={1}
                        max={12}
                        value={draft.lateral_entry_semester ?? ""}
                        onChange={(e) =>
                          setDraft({
                            ...draft,
                            lateral_entry_semester: e.target.value ? Number(e.target.value) : null,
                          })
                        }
                      />
                    </div>
                  </>
                )}
              </div>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <button className="btn btn-primary" onClick={() => void saveDetail()}>
                  Save changes
                </button>
                {notice && <span className="card-meta">{notice}</span>}
              </div>

              <hr
                style={{
                  border: 0,
                  borderTop: "1px solid var(--color-divider)",
                  margin: "14px 0 4px",
                }}
              />

              {childType ? (
                adding ? (
                  <form onSubmit={createChild}>
                    <div className="card-kicker" style={{ marginBottom: 8 }}>
                      New {TYPE_LABEL[childType]} in {selected.name}
                    </div>
                    <div className="org-form-grid">
                      <div className="field">
                        <label>Code</label>
                        <input
                          className="input"
                          value={addForm.code}
                          autoFocus
                          required
                          onChange={(e) => setAddForm({ ...addForm, code: e.target.value })}
                        />
                      </div>
                      <div className="field">
                        <label>Name</label>
                        <input
                          className="input"
                          value={addForm.name}
                          required
                          onChange={(e) => setAddForm({ ...addForm, name: e.target.value })}
                        />
                      </div>
                    </div>
                    <button className="btn btn-primary" type="submit">Create</button>
                    <button className="btn btn-ghost" type="button" onClick={() => setAdding(false)}>
                      Cancel
                    </button>
                  </form>
                ) : (
                  <button
                    className="btn btn-secondary"
                    onClick={() => setAdding(true)}
                    disabled={selected.status !== "active"}
                  >
                    + Add {TYPE_LABEL[childType]}
                  </button>
                )
              ) : (
                <p className="card-meta">
                  Sections live beneath a Programme but are <strong>per-term instances</strong> — the
                  Timetable Cell creates them during term setup, on the Terms &amp; sections screen.
                </p>
              )}
            </>
          )}
        </div>
      </div>
      {error && <p className="error">{error}</p>}
    </div>
  );
}
