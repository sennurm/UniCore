"use client";

import { useCallback, useEffect, useState } from "react";
import { api, downloadFile, upload } from "@/lib/api";
import TemplateLinks from "@/components/TemplateLinks";

type Batch = {
  id: string;
  filename: string;
  term_code: string;
  status: string;
  rows_total: number;
  rows_created: number;
  rows_updated: number;
  rows_unchanged: number;
  rows_rejected: number;
  created_at: string;
};

type RowError = { row_number: number; field: string; reason: string; raw_row: string };

type RosterRow = {
  user_id: string;
  sif_id: string | null;
  enrollment_id: string | null;
  full_name: string;
  status: string;
  roll_number: string | null;
  credential_delivery: string | null;
};

const DELIVERY_TAG: Record<string, string> = {
  delivered: "tag tag-accent",
  pending: "tag tag-neutral",
  failed: "tag tag-outline",
};

export default function OnboardingPage() {
  const [batches, setBatches] = useState<Batch[]>([]);
  const [termCode, setTermCode] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [selected, setSelected] = useState<Batch | null>(null);
  const [errors, setErrors] = useState<RowError[] | null>(null);
  const [sectionId, setSectionId] = useState("");
  const [enrolFile, setEnrolFile] = useState<File | null>(null);
  const [enrolResult, setEnrolResult] = useState<{
    rows_assigned: number;
    rows_unchanged: number;
    rows_rejected: number;
    errors: { row_number: number; field: string; reason: string }[];
  } | null>(null);
  const [roster, setRoster] = useState<RosterRow[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const loadBatches = useCallback(async () => {
    try {
      setBatches(await api<Batch[]>("/onboarding/imports"));
    } catch (err) {
      setError(String((err as Error).message));
    }
  }, []);

  useEffect(() => {
    void loadBatches();
  }, [loadBatches]);

  async function submitImport(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const form = new FormData();
      form.append("term_code", termCode);
      form.append("file", file);
      const batch = await upload<Batch>("/onboarding/imports", form);
      setMessage(
        `${batch.rows_created} created · ${batch.rows_updated} updated · ` +
          `${batch.rows_unchanged} unchanged · ${batch.rows_rejected} rejected`,
      );
      await loadBatches();
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
    }
  }

  async function openErrors(batch: Batch) {
    setSelected(batch);
    setErrors(await api<RowError[]>(`/onboarding/imports/${batch.id}/errors`));
  }

  async function deliver(batch: Batch) {
    setError("");
    try {
      const result = await api<{ delivered: number; failed: number }>(
        `/onboarding/imports/${batch.id}/deliver-credentials`,
        { method: "POST" },
      );
      setMessage(`Credentials delivered: ${result.delivered}, failed: ${result.failed}`);
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  async function importEnrollmentIds(e: React.FormEvent) {
    e.preventDefault();
    if (!enrolFile) return;
    setBusy(true);
    setError("");
    setEnrolResult(null);
    try {
      const form = new FormData();
      form.append("file", enrolFile);
      setEnrolResult(await upload("/onboarding/enrollment-ids", form));
      if (sectionId) setRoster(await api<RosterRow[]>(`/onboarding/sections/${sectionId}/roster`));
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
    }
  }

  async function loadRoster(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      setRoster(await api<RosterRow[]>(`/onboarding/sections/${sectionId}/roster`));
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  return (
    <div className="uc-screen">
      <div>
        <h3 style={{ margin: 0 }}>Student import</h3>
        <div className="uc-screen-sub">
          Import-only provisioning from the ERP · valid rows commit, invalid rows go to the error
          report
        </div>
      </div>

      <div className="card" style={{ maxWidth: 460 }}>
        <div className="card-kicker">Upload CSV</div>
        <form onSubmit={submitImport}>
          <div className="field">
            <label>Term code (e.g. 2026-S1)</label>
            <input className="input" value={termCode} onChange={(e) => setTermCode(e.target.value)} required />
          </div>
          <div className="field">
            <label>CSV file — schema v1</label>
            <input className="input" type="file" accept=".csv,text/csv"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)} required />
          </div>
          <button className="btn btn-primary" type="submit" disabled={busy || !file}>
            {busy ? "Importing…" : "Import"}
          </button>
        </form>
        <p className="card-meta" style={{ marginTop: 8 }}>
          Columns: sif_id, full_name, date_of_birth (DD-MM-YYYY), gender, mobile, email,
          program_code, section_label, admission_year, roll_number. The enrollment number is
          issued later — upload it separately below.
        </p>
        {message && <p className="card-meta">{message}</p>}
      </div>

      <div className="card" style={{ maxWidth: 520 }}>
        <div className="card-kicker">Enrollment numbers</div>
        <p className="card-meta">
          <strong>Enrollment No</strong> is the student's canonical identifier, but it is
          issued after admission — students onboard with their <strong>SIF id</strong> and get
          their enrollment number here, matched on SIF. Re-uploading is safe, and correcting a
          number is allowed and audited.
        </p>
        <form onSubmit={importEnrollmentIds}>
          <div className="field">
            <label>Enrollment numbers CSV (sif_id, enrollment_id)</label>
            <input className="input" type="file" accept=".csv,text/csv"
              onChange={(e) => setEnrolFile(e.target.files?.[0] ?? null)} required />
          </div>
          <button className="btn btn-primary" type="submit" disabled={busy || !enrolFile}>
            {busy ? "Assigning…" : "Assign enrollment numbers"}
          </button>
        </form>
        {enrolResult && (
          <p className="card-meta">
            {enrolResult.rows_assigned} assigned · {enrolResult.rows_unchanged} unchanged ·{" "}
            {enrolResult.rows_rejected} rejected
          </p>
        )}
        {enrolResult && enrolResult.errors.length > 0 && (
          <table className="table">
            <thead><tr><th>Row</th><th>Field</th><th>Reason</th></tr></thead>
            <tbody>
              {enrolResult.errors.map((e, i) => (
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

      <TemplateLinks only={["students", "enrollment-ids"]} />

      <div className="card">
        <div className="card-kicker">Batches</div>
        <table className="table">
          <thead>
            <tr>
              <th>File</th><th>Term</th><th>Status</th>
              <th>Created</th><th>Updated</th><th>Unchanged</th><th>Rejected</th><th />
            </tr>
          </thead>
          <tbody>
            {batches.map((b) => (
              <tr key={b.id}>
                <td>{b.filename}</td>
                <td>{b.term_code}</td>
                <td>
                  <span className={b.status === "committed" ? "tag tag-accent" : "tag tag-outline"}>
                    {b.status}
                  </span>
                </td>
                <td>{b.rows_created}</td>
                <td>{b.rows_updated}</td>
                <td>{b.rows_unchanged}</td>
                <td>{b.rows_rejected}</td>
                <td style={{ whiteSpace: "nowrap" }}>
                  {b.rows_rejected > 0 && (
                    <>
                      <button className="btn btn-ghost" onClick={() => void openErrors(b)}>Errors</button>
                      <button
                        className="btn btn-ghost"
                        onClick={() =>
                          void downloadFile(
                            `/onboarding/imports/${b.id}/errors.csv`,
                            `errors_${b.id}.csv`,
                          ).catch((err) => setError(String((err as Error).message)))
                        }
                      >
                        CSV
                      </button>
                    </>
                  )}
                  <button className="btn btn-ghost" onClick={() => void deliver(b)}>Deliver</button>
                </td>
              </tr>
            ))}
            {batches.length === 0 && (
              <tr><td colSpan={8} className="card-meta">No imports yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {selected && errors && (
        <div className="card">
          <div className="card-kicker">Error report · {selected.filename}</div>
          <table className="table">
            <thead>
              <tr><th>Row</th><th>Field</th><th>Reason</th><th>Raw row</th></tr>
            </thead>
            <tbody>
              {errors.map((e, i) => (
                <tr key={i}>
                  <td>{e.row_number}</td>
                  <td><span className="tag tag-outline">{e.field}</span></td>
                  <td>{e.reason}</td>
                  <td style={{ fontSize: 11, opacity: 0.6 }}>{e.raw_row}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card">
        <div className="card-kicker">Section roster</div>
        <form onSubmit={loadRoster} style={{ display: "flex", gap: 12, alignItems: "flex-end", maxWidth: 520 }}>
          <div className="field" style={{ flex: 1, marginBottom: 0 }}>
            <label>Section org-unit id</label>
            <input className="input" value={sectionId} onChange={(e) => setSectionId(e.target.value)} required />
          </div>
          <button className="btn btn-secondary" type="submit">Load</button>
        </form>
        {roster && (
          <table className="table" style={{ marginTop: 12 }}>
            <thead>
              <tr>
                <th>Enrollment No</th><th>Name</th><th>Roll</th><th>SIF id</th>
                <th>Account</th><th>Credentials</th>
              </tr>
            </thead>
            <tbody>
              {roster.map((r) => (
                <tr key={r.user_id}>
                  <td>
                    {r.enrollment_id ? (
                      <strong>{r.enrollment_id}</strong>
                    ) : (
                      <span className="tag tag-neutral">not issued</span>
                    )}
                  </td>
                  <td>{r.full_name}</td>
                  <td>{r.roll_number ?? "—"}</td>
                  <td style={{ fontSize: 12, opacity: 0.7 }}>{r.sif_id ?? "—"}</td>
                  <td><span className="tag tag-neutral">{r.status}</span></td>
                  <td>
                    <span className={DELIVERY_TAG[r.credential_delivery ?? "pending"]}>
                      {r.credential_delivery ?? "—"}
                    </span>
                  </td>
                </tr>
              ))}
              {roster.length === 0 && (
                <tr><td colSpan={6} className="card-meta">No students in this Section today.</td></tr>
              )}
            </tbody>
          </table>
        )}
      </div>
      {error && <p className="error">{error}</p>}
    </div>
  );
}
