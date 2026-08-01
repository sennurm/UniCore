"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { api, downloadFile, upload } from "@/lib/api";
import TemplateLinks from "@/components/TemplateLinks";

/**
 * "Batch" is reserved for the admission cohort (00-overview.md §3). One execution
 * of a CSV import is an **import run** — the two appear side by side on this
 * screen, so the distinction has to be visible in the names.
 */
type ImportRun = {
  id: string;
  filename: string;
  term_code: string;
  status: string;
  rows_total: number;
  rows_created: number;
  rows_updated: number;
  rows_unchanged: number;
  rows_rejected: number;
  created_batches: string[];
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
  position: number | null;
  year: number | null;
  batch_code: string | null;
};

type Unit = {
  id: string;
  type: string;
  name: string;
  code: string;
  parent_id: string | null;
  path: string;
  term_code: string | null;
  cadence: string | null;
};
type Term = { id: string; school_id: string; term_code: string; status: string };

const DELIVERY_TAG: Record<string, string> = {
  delivered: "tag tag-accent",
  pending: "tag tag-neutral",
  failed: "tag tag-outline",
};

const BLANK_ADD = {
  sif_id: "",
  full_name: "",
  roll_number: "",
  mobile: "",
  email: "",
  admission_year: String(new Date().getFullYear()),
};

export default function OnboardingPage() {
  const [runs, setRuns] = useState<ImportRun[]>([]);
  const [units, setUnits] = useState<Unit[]>([]);
  const [terms, setTerms] = useState<Term[]>([]);

  const [termCode, setTermCode] = useState("");
  const [defaultProgram, setDefaultProgram] = useState("");
  const [defaultPosition, setDefaultPosition] = useState("");
  const [file, setFile] = useState<File | null>(null);

  const [selected, setSelected] = useState<ImportRun | null>(null);
  const [errors, setErrors] = useState<RowError[] | null>(null);

  const [enrolFile, setEnrolFile] = useState<File | null>(null);
  const [enrolResult, setEnrolResult] = useState<{
    rows_assigned: number;
    rows_unchanged: number;
    rows_rejected: number;
    errors: { row_number: number; field: string; reason: string }[];
  } | null>(null);

  const [rosterProgram, setRosterProgram] = useState("");
  const [rosterSection, setRosterSection] = useState("");
  const [asOf, setAsOf] = useState("");
  const [roster, setRoster] = useState<RosterRow[] | null>(null);

  const [adding, setAdding] = useState(false);
  const [addForm, setAddForm] = useState(BLANK_ADD);

  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const loadRuns = useCallback(async () => {
    setRuns(await api<ImportRun[]>("/onboarding/imports"));
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const [allUnits, allTerms] = await Promise.all([
          api<Unit[]>("/org/units?limit=2000"),
          api<Term[]>("/timetable/terms"),
          loadRuns(),
        ]);
        setUnits(allUnits);
        setTerms(allTerms);
      } catch (err) {
        setError(String((err as Error).message));
      }
    })();
  }, [loadRuns]);

  // ── derived ───────────────────────────────────────────────────────────────
  const programmes = useMemo(
    () => units.filter((u) => u.type === "program").sort((a, b) => a.code.localeCompare(b.code)),
    [units],
  );
  const byId = useMemo(() => new Map(units.map((u) => [u.id, u])), [units]);

  /** Only approved terms can hold Sections, so only they are offered (ONB-FR-07). */
  const approvedTerms = useMemo(
    () =>
      Array.from(
        new Set(terms.filter((t) => t.status === "approved").map((t) => t.term_code)),
      ).sort((a, b) => b.localeCompare(a)),
    [terms],
  );

  useEffect(() => {
    if (!termCode && approvedTerms.length > 0) setTermCode(approvedTerms[0]);
  }, [approvedTerms, termCode]);

  /** Sections of the chosen Programme for the chosen term — never a typed id. */
  const rosterSections = useMemo(
    () =>
      units
        .filter(
          (u) =>
            u.type === "section" &&
            u.parent_id === rosterProgram &&
            (!termCode || u.term_code === termCode),
        )
        .sort((a, b) => a.name.localeCompare(b.name)),
    [units, rosterProgram, termCode],
  );

  /** The Programme's effective cadence decides whether position means semester or year. */
  const positionNoun = useCallback(
    (programId: string) => {
      const programme = byId.get(programId);
      if (!programme) return "position";
      let node: Unit | undefined = programme;
      while (node && !node.cadence) node = node.parent_id ? byId.get(node.parent_id) : undefined;
      return node?.cadence === "yearly" ? "year" : "semester";
    },
    [byId],
  );

  const needsReview = useMemo(() => runs.filter((r) => r.status === "needs-review"), [runs]);

  // ── actions ───────────────────────────────────────────────────────────────
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
      if (defaultProgram) form.append("default_program_code", defaultProgram);
      if (defaultPosition) form.append("default_position", defaultPosition);
      const run = await upload<ImportRun>("/onboarding/imports", form);
      setMessage(
        `${run.rows_created} created · ${run.rows_updated} updated · ` +
          `${run.rows_unchanged} unchanged · ${run.rows_rejected} rejected` +
          (run.created_batches.length
            ? ` · new batches: ${run.created_batches.join(", ")}`
            : ""),
      );
      await loadRuns();
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
    }
  }

  async function confirmRun(run: ImportRun) {
    setError("");
    setMessage("");
    try {
      await api(`/onboarding/imports/${run.id}/confirm`, { method: "POST" });
      setMessage(`${run.filename} released — credentials can now be delivered.`);
      await loadRuns();
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  async function openErrors(run: ImportRun) {
    setSelected(run);
    setErrors(await api<RowError[]>(`/onboarding/imports/${run.id}/errors`));
  }

  async function deliver(run: ImportRun) {
    setError("");
    try {
      const result = await api<{ delivered: number; failed: number }>(
        `/onboarding/imports/${run.id}/deliver-credentials`,
        { method: "POST" },
      );
      setMessage(`Credentials delivered: ${result.delivered}, failed: ${result.failed}`);
      await loadRuns();
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
      if (rosterSection) await refreshRoster();
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
    }
  }

  const refreshRoster = useCallback(async () => {
    if (!rosterSection) return;
    const query = asOf ? `?as_of=${asOf}` : "";
    setRoster(await api<RosterRow[]>(`/onboarding/sections/${rosterSection}/roster${query}`));
  }, [rosterSection, asOf]);

  async function loadRoster(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    try {
      await refreshRoster();
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  async function addStudent(e: React.FormEvent) {
    e.preventDefault();
    const section = units.find((u) => u.id === rosterSection);
    if (!section) return;
    setBusy(true);
    setError("");
    try {
      await api("/onboarding/students", {
        method: "POST",
        body: {
          ...addForm,
          admission_year: Number(addForm.admission_year),
          program_code: byId.get(rosterProgram)?.code,
          section_label: section.name,
          term_code: termCode,
          mobile: addForm.mobile || null,
          email: addForm.email || null,
        },
      });
      setMessage(`${addForm.full_name} added.`);
      setAddForm(BLANK_ADD);
      setAdding(false);
      await refreshRoster();
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
    }
  }

  async function withdraw(row: RosterRow) {
    const reason = window.prompt(`Reason for withdrawing ${row.full_name}?`);
    if (!reason) return;
    setError("");
    try {
      const form = new FormData();
      form.append("reason", reason);
      form.append("effective_from", new Date().toISOString().slice(0, 10));
      await upload(`/onboarding/students/${row.user_id}/withdraw`, form);
      setMessage(`${row.full_name} withdrawn.`);
      await refreshRoster();
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  // ── render ────────────────────────────────────────────────────────────────
  return (
    <div className="uc-screen" style={{ maxWidth: 1180 }}>
      <div>
        <h3 style={{ margin: 0 }}>Student import</h3>
        <div className="uc-screen-sub">
          Import-only provisioning from the ERP · valid rows commit, invalid rows go to the error
          report. Each student lands in a <strong>batch</strong> (their admission cohort) at a
          position in the curriculum ladder.
        </div>
      </div>

      {needsReview.length > 0 && (
        <div className="card">
          <div className="card-kicker">Held for review</div>
          <p className="card-meta">
            {needsReview.length} import run{needsReview.length === 1 ? "" : "s"} changed the org
            mapping or date of birth of more than 20% of their rows, so they are parked rather than
            committed silently. Credentials cannot be delivered until each is released.
          </p>
          {needsReview.map((run) => (
            <div
              key={run.id}
              style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 6 }}
            >
              <strong>{run.filename}</strong>
              <span className="card-meta">
                {run.rows_created + run.rows_updated} rows would commit
              </span>
              <div style={{ flex: 1 }} />
              <button className="btn btn-primary" onClick={() => void confirmRun(run)}>
                Release
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="card" style={{ maxWidth: 620 }}>
        <div className="card-kicker">Upload student CSV</div>
        <form onSubmit={submitImport}>
          <div className="org-form-grid">
            <div className="field">
              <label>Term</label>
              <select
                className="input"
                value={termCode}
                onChange={(e) => setTermCode(e.target.value)}
                required
              >
                {approvedTerms.length === 0 && <option value="">no approved term</option>}
                {approvedTerms.map((code) => (
                  <option key={code}>{code}</option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>Default Programme (optional)</label>
              <select
                className="input"
                value={defaultProgram}
                onChange={(e) => setDefaultProgram(e.target.value)}
              >
                <option value="">— from the file —</option>
                {programmes.map((p) => (
                  <option key={p.id} value={p.code}>
                    {p.code} · {p.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>Default position (optional)</label>
              <input
                className="input"
                type="number"
                min={1}
                max={12}
                placeholder="1"
                value={defaultPosition}
                onChange={(e) => setDefaultPosition(e.target.value)}
              />
            </div>
            <div className="field">
              <label>CSV file — schema v1</label>
              <input
                className="input"
                type="file"
                accept=".csv,text/csv"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                required
              />
            </div>
          </div>
          <button
            className="btn btn-primary"
            type="submit"
            disabled={busy || !file || !termCode}
          >
            {busy ? "Importing…" : "Import"}
          </button>
        </form>
        <p className="card-meta" style={{ marginTop: 8 }}>
          The two defaults fill <strong>blank cells only</strong> — a value in the file always wins.
          Blank in both means position 1 (first year, first semester).
        </p>
        <p className="card-meta">
          Columns: sif_id, full_name, date_of_birth (DD-MM-YYYY), gender, mobile, email,
          program_code, section_label, admission_year, position, roll_number, enrollment_id. The
          batch is derived from program_code + admission_year — there is no column for it.
        </p>
        <p className="card-meta">
          Every row must carry <strong>at least one</strong> of <strong>SIF id</strong> (issued at
          admission) or <strong>Enrollment No</strong> (issued later), and may carry both — rows
          match on whichever is present. An enrollment-only row <em>updates</em> an existing
          student; a new student needs a SIF id.
        </p>
        {message && <p className="card-meta">{message}</p>}
      </div>

      <div className="card" style={{ maxWidth: 620 }}>
        <div className="card-kicker">Enrollment numbers</div>
        <p className="card-meta">
          <strong>Enrollment No</strong> is the student&rsquo;s canonical identifier, but it is
          issued after admission — students onboard with their <strong>SIF id</strong> and get their
          enrollment number here, matched on SIF. Re-uploading is safe, and correcting a number is
          allowed and audited.
        </p>
        <form onSubmit={importEnrollmentIds}>
          <div className="field">
            <label>Enrollment numbers CSV (sif_id, enrollment_id)</label>
            <input
              className="input"
              type="file"
              accept=".csv,text/csv"
              onChange={(e) => setEnrolFile(e.target.files?.[0] ?? null)}
              required
            />
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
            <thead>
              <tr>
                <th>Row</th>
                <th>Field</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {enrolResult.errors.map((e, i) => (
                <tr key={i}>
                  <td>{e.row_number}</td>
                  <td>
                    <span className="tag tag-outline">{e.field}</span>
                  </td>
                  <td>{e.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <TemplateLinks only={["students", "enrollment-ids"]} />

      <div className="card">
        <div className="card-kicker">Import runs</div>
        <table className="table">
          <thead>
            <tr>
              <th>File</th>
              <th>Term</th>
              <th>Status</th>
              <th>Created</th>
              <th>Updated</th>
              <th>Unchanged</th>
              <th>Rejected</th>
              <th>New batches</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id}>
                <td>{run.filename}</td>
                <td>{run.term_code}</td>
                <td>
                  <span
                    className={
                      run.status === "committed"
                        ? "tag tag-accent"
                        : run.status === "needs-review"
                          ? "tag tag-outline"
                          : "tag tag-neutral"
                    }
                  >
                    {run.status}
                  </span>
                </td>
                <td>{run.rows_created}</td>
                <td>{run.rows_updated}</td>
                <td>{run.rows_unchanged}</td>
                <td>{run.rows_rejected}</td>
                <td>
                  {run.created_batches.length === 0 ? (
                    <span className="org-type">—</span>
                  ) : (
                    <span style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                      {run.created_batches.map((code) => (
                        <span
                          className="tag tag-outline"
                          key={code}
                          title="Admission cohort created by this run — check it was intended"
                        >
                          {code}
                        </span>
                      ))}
                    </span>
                  )}
                </td>
                <td style={{ whiteSpace: "nowrap" }}>
                  {run.rows_rejected > 0 && (
                    <>
                      <button className="btn btn-ghost" onClick={() => void openErrors(run)}>
                        Errors
                      </button>
                      <button
                        className="btn btn-ghost"
                        onClick={() =>
                          void downloadFile(
                            `/onboarding/imports/${run.id}/errors.csv`,
                            `errors_${run.id}.csv`,
                          ).catch((err) => setError(String((err as Error).message)))
                        }
                      >
                        CSV
                      </button>
                    </>
                  )}
                  {run.status === "needs-review" ? (
                    <button className="btn btn-ghost" onClick={() => void confirmRun(run)}>
                      Release
                    </button>
                  ) : (
                    <button className="btn btn-ghost" onClick={() => void deliver(run)}>
                      Deliver
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {runs.length === 0 && (
              <tr>
                <td colSpan={9} className="card-meta">
                  No imports yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {selected && errors && (
        <div className="card">
          <div className="card-kicker">Error report · {selected.filename}</div>
          <table className="table">
            <thead>
              <tr>
                <th>Row</th>
                <th>Field</th>
                <th>Reason</th>
                <th>Raw row</th>
              </tr>
            </thead>
            <tbody>
              {errors.map((e, i) => (
                <tr key={i}>
                  <td>{e.row_number}</td>
                  <td>
                    <span className="tag tag-outline">{e.field}</span>
                  </td>
                  <td>{e.reason}</td>
                  <td style={{ fontSize: 11, opacity: 0.6 }}>{e.raw_row}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card">
        <div className="org-detail-head">
          <div className="card-kicker">Section roster</div>
          <div style={{ flex: 1 }} />
          {rosterSection && (
            <button className="btn btn-secondary" onClick={() => setAdding((v) => !v)}>
              {adding ? "Cancel" : "+ Add one student"}
            </button>
          )}
        </div>

        <form
          onSubmit={loadRoster}
          style={{ display: "flex", gap: 12, alignItems: "flex-end", flexWrap: "wrap" }}
        >
          <div className="field" style={{ marginBottom: 0, minWidth: 220 }}>
            <label>Programme</label>
            <select
              className="input"
              value={rosterProgram}
              onChange={(e) => {
                setRosterProgram(e.target.value);
                setRosterSection("");
                setRoster(null);
              }}
              required
            >
              <option value="">— select —</option>
              {programmes.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.code} · {p.name}
                </option>
              ))}
            </select>
          </div>
          <div className="field" style={{ marginBottom: 0, minWidth: 170 }}>
            <label>Section · {termCode || "term"}</label>
            <select
              className="input"
              value={rosterSection}
              onChange={(e) => setRosterSection(e.target.value)}
              required
              disabled={!rosterProgram}
            >
              <option value="">
                {rosterProgram && rosterSections.length === 0 ? "none this term" : "— select —"}
              </option>
              {rosterSections.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>
          <div className="field" style={{ marginBottom: 0 }}>
            <label>As of (optional)</label>
            <input
              className="input"
              type="date"
              value={asOf}
              onChange={(e) => setAsOf(e.target.value)}
            />
          </div>
          <button className="btn btn-secondary" type="submit" disabled={!rosterSection}>
            Load
          </button>
        </form>
        <p className="card-meta" style={{ marginTop: 6 }}>
          Membership is dated, so an earlier date shows who was in this Section then — not who is
          now.
        </p>

        {adding && (
          <form onSubmit={addStudent} style={{ marginTop: 10 }}>
            <div className="card-kicker" style={{ marginBottom: 8 }}>
              Mid-term add · same validation and activation as a bulk row
            </div>
            <div className="org-form-grid">
              {(
                [
                  ["sif_id", "SIF id", true],
                  ["full_name", "Full name", true],
                  ["roll_number", "Roll number", true],
                  ["admission_year", "Admission year", true],
                  ["mobile", "Mobile", false],
                  ["email", "Email", false],
                ] as [keyof typeof BLANK_ADD, string, boolean][]
              ).map(([key, labelText, required]) => (
                <div className="field" key={key}>
                  <label>{labelText}</label>
                  <input
                    className="input"
                    value={addForm[key]}
                    required={required}
                    onChange={(e) => setAddForm({ ...addForm, [key]: e.target.value })}
                  />
                </div>
              ))}
            </div>
            <button className="btn btn-primary" type="submit" disabled={busy}>
              {busy ? "Adding…" : "Add student"}
            </button>
            <p className="card-meta" style={{ marginTop: 6 }}>
              At least one of mobile or email is required — that is how credentials reach them.
            </p>
          </form>
        )}

        {roster && (
          <table className="table" style={{ marginTop: 12 }}>
            <thead>
              <tr>
                <th>Enrollment No</th>
                <th>Name</th>
                <th>Batch</th>
                <th>Position</th>
                <th>Roll</th>
                <th>SIF id</th>
                <th>Account</th>
                <th>Credentials</th>
                <th />
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
                  <td>
                    {r.batch_code ? (
                      <span className="tag tag-outline">{r.batch_code}</span>
                    ) : (
                      <span className="org-type">—</span>
                    )}
                  </td>
                  <td>
                    {r.position === null ? (
                      <span className="org-type">—</span>
                    ) : (
                      <>
                        {positionNoun(rosterProgram) === "year" ? "Year" : "Semester"} {r.position}
                        {/* For semester cadence the year is derived, never stored. */}
                        {r.year !== null && positionNoun(rosterProgram) === "semester" && (
                          <div className="card-meta">Year {r.year}</div>
                        )}
                      </>
                    )}
                  </td>
                  <td>{r.roll_number ?? "—"}</td>
                  <td style={{ fontSize: 12, opacity: 0.7 }}>{r.sif_id ?? "—"}</td>
                  <td>
                    <span className="tag tag-neutral">{r.status}</span>
                  </td>
                  <td>
                    <span className={DELIVERY_TAG[r.credential_delivery ?? "pending"]}>
                      {r.credential_delivery ?? "—"}
                    </span>
                  </td>
                  <td style={{ textAlign: "right" }}>
                    {r.status !== "withdrawn" && (
                      <button className="btn btn-ghost" onClick={() => void withdraw(r)}>
                        Withdraw
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {roster.length === 0 && (
                <tr>
                  <td colSpan={9} className="card-meta">
                    No students in this Section on the selected date.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </div>
      {error && <p className="error">{error}</p>}
    </div>
  );
}
