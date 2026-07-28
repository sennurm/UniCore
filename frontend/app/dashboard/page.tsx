"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

type Unit = {
  id: string;
  type: string;
  name: string;
  code: string;
  path: string;
  status: string;
  term_code: string | null;
};

const CHILD_TYPE: Record<string, string> = {
  university: "faculty_division",
  faculty_division: "school",
  school: "department",
  department: "program",
};

function UnitNode({ unit }: { unit: Unit }) {
  const [children, setChildren] = useState<Unit[] | null>(null);

  async function toggle() {
    if (children !== null) {
      setChildren(null);
      return;
    }
    setChildren(await api<Unit[]>(`/org/units/${unit.id}/children`));
  }

  return (
    <li>
      <button className="tree-toggle" onClick={toggle}>
        {children === null ? "+" : "−"} {unit.name} ({unit.code})
      </button>
      <span className="tag tag-outline" style={{ marginLeft: 6 }}>{unit.type}</span>
      {unit.status !== "active" && (
        <span className="tag tag-neutral" style={{ marginLeft: 6 }}>{unit.status}</span>
      )}
      {children !== null && (
        <ul className="tree">
          {children.length === 0 && <li className="tag tag-neutral">no children</li>}
          {children.map((c) => (
            <UnitNode key={c.id} unit={c} />
          ))}
        </ul>
      )}
    </li>
  );
}

export default function OrgPage() {
  const [root, setRoot] = useState<Unit | null>(null);
  const [form, setForm] = useState({ type: "faculty_division", name: "", code: "", parent_id: "" });
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      setRoot(await api<Unit | null>("/org/root"));
    } catch (err) {
      setError(String((err as Error).message));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function createUnit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api("/org/units", {
        method: "POST",
        body: {
          type: form.type,
          name: form.name,
          code: form.code,
          parent_id: form.parent_id || null,
        },
      });
      setForm({ ...form, name: "", code: "" });
      await load();
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  return (
    <div className="uc-screen">
      <div>
        <h3 style={{ margin: 0 }}>Org structure</h3>
        <div className="uc-screen-sub">
          University → Faculty Division → School → Department → Program · deactivate, never delete
        </div>
      </div>
      <div className="card">
        <div className="card-kicker">Hierarchy</div>
        {root === null ? (
          <p className="card-body">
            No university root yet — create one below (type: university, no parent id).
          </p>
        ) : (
          <ul className="tree" style={{ paddingLeft: 0 }}>
            <UnitNode unit={root} />
          </ul>
        )}
      </div>
      <div className="card">
        <div className="card-kicker">Create unit · Super Admin</div>
        <form onSubmit={createUnit} style={{ maxWidth: 380 }}>
          <div className="field">
            <label>Type</label>
            <select
              className="input"
              value={form.type}
              onChange={(e) => setForm({ ...form, type: e.target.value })}
            >
              {["university", ...Object.values(CHILD_TYPE)].map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </div>
          <div className="field">
            <label>Name</label>
            <input
              className="input"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              required
            />
          </div>
          <div className="field">
            <label>Code</label>
            <input
              className="input"
              value={form.code}
              onChange={(e) => setForm({ ...form, code: e.target.value })}
              required
            />
          </div>
          <div className="field">
            <label>Parent unit id (empty for university)</label>
            <input
              className="input"
              value={form.parent_id}
              onChange={(e) => setForm({ ...form, parent_id: e.target.value })}
            />
          </div>
          <button className="btn btn-primary" type="submit">Create</button>
        </form>
        {error && <p className="error">{error}</p>}
      </div>
    </div>
  );
}
