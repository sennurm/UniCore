"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";

type Term = {
  id: string;
  term_code: string;
  version: number;
  start_date: string;
  end_date: string;
  status: string;
  approved_by: string | null;
};

type Section = { id: string; name: string; code: string; term_code: string | null };

export default function TermsPage() {
  const [schoolId, setSchoolId] = useState("");
  const [terms, setTerms] = useState<Term[] | null>(null);
  const [termForm, setTermForm] = useState({ term_code: "", start_date: "", end_date: "" });
  const [sectionForm, setSectionForm] = useState({ program_id: "", label: "", term_code: "" });
  const [created, setCreated] = useState<Section | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!schoolId) return;
    setError("");
    try {
      setTerms(await api<Term[]>(`/timetable/schools/${schoolId}/terms`));
    } catch (err) {
      setError(String((err as Error).message));
    }
  }, [schoolId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function uploadTerm(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await api("/timetable/terms", {
        method: "POST",
        body: { school_id: schoolId, ...termForm },
      });
      await load();
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  async function approve(id: string) {
    setError("");
    try {
      await api(`/timetable/terms/${id}/approve`, { method: "POST" });
      await load();
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  async function createSection(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setCreated(null);
    try {
      setCreated(await api<Section>("/timetable/sections", { method: "POST", body: sectionForm }));
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  return (
    <div className="uc-screen">
      <div>
        <h3 style={{ margin: 0 }}>Terms &amp; sections</h3>
        <div className="uc-screen-sub">
          Each School uploads its term calendar; the School Incharge approves it before Sections
          can be created
        </div>
      </div>

      <div className="card">
        <div className="card-kicker">School</div>
        <div className="field" style={{ maxWidth: 380 }}>
          <label>School org-unit id</label>
          <input className="input" value={schoolId} onChange={(e) => setSchoolId(e.target.value)} />
        </div>
        {terms && terms.length > 0 && (
          <table className="table">
            <thead>
              <tr><th>Term</th><th>Version</th><th>Dates</th><th>Status</th><th /></tr>
            </thead>
            <tbody>
              {terms.map((t) => (
                <tr key={t.id}>
                  <td>{t.term_code}</td>
                  <td>v{t.version}</td>
                  <td>{t.start_date} → {t.end_date}</td>
                  <td>
                    <span className={t.status === "approved" ? "tag tag-accent" : "tag tag-neutral"}>
                      {t.status}
                    </span>
                  </td>
                  <td>
                    {t.status === "draft" && (
                      <button className="btn btn-secondary" onClick={() => void approve(t.id)}>
                        Approve
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {terms?.length === 0 && <p className="card-meta">No terms uploaded for this School yet.</p>}
      </div>

      <div className="card" style={{ maxWidth: 420 }}>
        <div className="card-kicker">Upload term calendar</div>
        <form onSubmit={uploadTerm}>
          <div className="field">
            <label>Term code (e.g. 2026-S1)</label>
            <input className="input" value={termForm.term_code}
              onChange={(e) => setTermForm({ ...termForm, term_code: e.target.value })} required />
          </div>
          <div className="field">
            <label>Start date</label>
            <input className="input" type="date" value={termForm.start_date}
              onChange={(e) => setTermForm({ ...termForm, start_date: e.target.value })} required />
          </div>
          <div className="field">
            <label>End date</label>
            <input className="input" type="date" value={termForm.end_date}
              onChange={(e) => setTermForm({ ...termForm, end_date: e.target.value })} required />
          </div>
          <button className="btn btn-primary" type="submit" disabled={!schoolId}>Upload</button>
        </form>
      </div>

      <div className="card" style={{ maxWidth: 420 }}>
        <div className="card-kicker">Create Section instance · Timetable Cell</div>
        <form onSubmit={createSection}>
          <div className="field">
            <label>Program org-unit id</label>
            <input className="input" value={sectionForm.program_id}
              onChange={(e) => setSectionForm({ ...sectionForm, program_id: e.target.value })} required />
          </div>
          <div className="field">
            <label>Label (e.g. 3B)</label>
            <input className="input" value={sectionForm.label}
              onChange={(e) => setSectionForm({ ...sectionForm, label: e.target.value })} required />
          </div>
          <div className="field">
            <label>Term code</label>
            <input className="input" value={sectionForm.term_code}
              onChange={(e) => setSectionForm({ ...sectionForm, term_code: e.target.value })} required />
          </div>
          <button className="btn btn-primary" type="submit">Create Section</button>
        </form>
        {created && (
          <p className="card-meta">
            Created <strong>{created.name}</strong> — {created.id}
            <span className="tag tag-accent" style={{ marginLeft: 8 }}>{created.term_code}</span>
          </p>
        )}
      </div>
      {error && <p className="error">{error}</p>}
    </div>
  );
}
