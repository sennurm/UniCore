"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { api, upload } from "@/lib/api";
import TemplateLinks from "@/components/TemplateLinks";

type Unit = {
  id: string;
  name: string;
  code: string;
  path: string;
  status: string;
  cadence: string | null;
  cadence_unconfirmed: boolean;
  class_size_cap: number | null;
};

type Term = {
  id: string;
  school_id: string;
  term_code: string;
  version: number;
  start_date: string;
  end_date: string;
  parity: string | null;
  archival_backstop_date: string | null;
  status: string;
  approved_by: string | null;
};

type Section = { id: string; name: string; code: string; term_code: string | null; status: string };

/** One position's proposal: what exists, what's missing, and the arithmetic behind it. */
type PlanRow = {
  programme_id: string;
  programme_name: string;
  programme_code: string;
  cadence: string;
  position: number;
  year: number;
  headcount: number;
  headcount_source: "roster" | "expected" | "none";
  class_size_cap: number;
  required: number;
  existing: Section[];
  to_create: string[];
};
type GenerationPlan = {
  term_code: string;
  parity: string;
  school_id: string;
  school_name: string;
  rows: PlanRow[];
  warnings: string[];
};
type GenerationResult = { created: Section[]; existing: number; warnings: string[] };
type ImportResult = {
  rows_created: number;
  rows_rejected: number;
  errors: { row_number: number; reason: string; raw_row: string }[];
};
type MultiResult = {
  school_id: string;
  school_name: string;
  outcome: string;
  version: number | null;
  detail: string | null;
};

const BLANK_TERM = {
  term_code: "",
  start_date: "",
  end_date: "",
  parity: "odd",
  archival_backstop_date: "",
};

/** Terms of one School, newest first, with every version of a code kept together. */
function groupVersions(terms: Term[]): { code: string; versions: Term[] }[] {
  const byCode = new Map<string, Term[]>();
  for (const term of terms) {
    const list = byCode.get(term.term_code) ?? [];
    list.push(term);
    byCode.set(term.term_code, list);
  }
  return Array.from(byCode.entries())
    .map(([code, versions]) => ({
      code,
      versions: versions.sort((a, b) => b.version - a.version),
    }))
    .sort((a, b) => b.code.localeCompare(a.code));
}

export default function TermsPage() {
  const [schools, setSchools] = useState<Unit[]>([]);
  const [terms, setTerms] = useState<Term[]>([]);
  const [schoolId, setSchoolId] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  const [termForm, setTermForm] = useState(BLANK_TERM);
  const [uploading, setUploading] = useState(false);
  const [multiMode, setMultiMode] = useState(false);
  const [multiTargets, setMultiTargets] = useState<Set<string>>(new Set());
  const [multiResult, setMultiResult] = useState<MultiResult[] | null>(null);

  const [planTerm, setPlanTerm] = useState("");
  const [plan, setPlan] = useState<GenerationPlan | null>(null);
  const [planError, setPlanError] = useState("");
  const [intake, setIntake] = useState<Record<string, string>>({});
  const [result, setResult] = useState<GenerationResult | null>(null);

  const [addingTo, setAddingTo] = useState<string | null>(null);
  const [label, setLabel] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [showImport, setShowImport] = useState(false);

  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const loadTerms = useCallback(async () => {
    setTerms(await api<Term[]>("/timetable/terms"));
  }, []);
  const loadSchools = useCallback(async () => {
    setSchools(await api<Unit[]>("/org/units?unit_type=school&limit=2000"));
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        await Promise.all([loadSchools(), loadTerms()]);
      } catch (err) {
        setError(String((err as Error).message));
      }
    })();
  }, [loadSchools, loadTerms]);

  // ── derived ───────────────────────────────────────────────────────────────
  const termsOf = useMemo(() => {
    const map = new Map<string, Term[]>();
    for (const term of terms) {
      const list = map.get(term.school_id) ?? [];
      list.push(term);
      map.set(term.school_id, list);
    }
    return map;
  }, [terms]);

  const school = useMemo(() => schools.find((s) => s.id === schoolId) ?? null, [schools, schoolId]);
  const schoolTerms = useMemo(
    () => (schoolId ? (termsOf.get(schoolId) ?? []) : []),
    [termsOf, schoolId],
  );
  const grouped = useMemo(() => groupVersions(schoolTerms), [schoolTerms]);
  const approvedCodes = useMemo(
    () =>
      Array.from(
        new Set(schoolTerms.filter((t) => t.status === "approved").map((t) => t.term_code)),
      ).sort((a, b) => b.localeCompare(a)),
    [schoolTerms],
  );

  const visibleSchools = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return schools;
    return schools.filter(
      (s) => s.name.toLowerCase().includes(needle) || s.code.toLowerCase().includes(needle),
    );
  }, [schools, search]);

  const stats = useMemo(() => {
    let approved = 0;
    let awaiting = 0;
    for (const s of schools) {
      const own = termsOf.get(s.id) ?? [];
      if (own.some((t) => t.status === "approved")) approved += 1;
      else if (own.some((t) => t.status === "draft")) awaiting += 1;
    }
    return {
      approved,
      awaiting,
      none: schools.length - approved - awaiting,
      unconfirmed: schools.filter((s) => s.cadence_unconfirmed).length,
    };
  }, [schools, termsOf]);

  const toCreateCount = useMemo(
    () => (plan?.rows ?? []).reduce((n, r) => n + r.to_create.length, 0),
    [plan],
  );
  const existingCount = useMemo(
    () => (plan?.rows ?? []).reduce((n, r) => n + r.existing.length, 0),
    [plan],
  );

  useEffect(() => {
    setTermForm(BLANK_TERM);
    setNotice("");
    setImportResult(null);
    setMultiResult(null);
    setResult(null);
    setIntake({});
    setAddingTo(null);
    setShowImport(false);
    setPlanTerm(approvedCodes[0] ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [schoolId]);

  useEffect(() => {
    if (!planTerm && approvedCodes.length > 0) setPlanTerm(approvedCodes[0]);
  }, [approvedCodes, planTerm]);

  const loadPlan = useCallback(async () => {
    if (!schoolId || !planTerm) {
      setPlan(null);
      return;
    }
    setPlanError("");
    try {
      setPlan(
        await api<GenerationPlan>(
          `/timetable/schools/${schoolId}/generation-plan?term_code=${encodeURIComponent(planTerm)}`,
        ),
      );
    } catch (err) {
      setPlan(null);
      setPlanError(String((err as Error).message));
    }
  }, [schoolId, planTerm]);

  useEffect(() => {
    void loadPlan();
  }, [loadPlan]);

  // ── actions ───────────────────────────────────────────────────────────────
  function termBody() {
    return {
      term_code: termForm.term_code,
      start_date: termForm.start_date,
      end_date: termForm.end_date,
      parity: termForm.parity,
      archival_backstop_date: termForm.archival_backstop_date || null,
    };
  }

  async function uploadTerm(e: React.FormEvent) {
    e.preventDefault();
    if (!schoolId) return;
    setError("");
    setNotice("");
    setMultiResult(null);
    setBusy(true);
    try {
      if (multiMode) {
        // The selected School is always included — it is the one being edited.
        const targets = Array.from(new Set([schoolId, ...Array.from(multiTargets)]));
        setMultiResult(
          await api<MultiResult[]>("/timetable/terms/multi", {
            method: "POST",
            body: { school_ids: targets, ...termBody() },
          }),
        );
      } else {
        await api("/timetable/terms", {
          method: "POST",
          body: { school_id: schoolId, ...termBody() },
        });
        setNotice("Calendar uploaded as a draft — it needs School Incharge approval.");
      }
      setTermForm(BLANK_TERM);
      setUploading(false);
      setMultiTargets(new Set());
      await loadTerms();
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
    }
  }

  async function approve(term: Term) {
    setError("");
    setNotice("");
    try {
      await api(`/timetable/terms/${term.id}/approve`, { method: "POST" });
      setNotice(`${term.term_code} v${term.version} approved.`);
      await loadTerms();
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  async function setParity(term: Term, parity: string) {
    setError("");
    setNotice("");
    try {
      await api(`/timetable/terms/${term.id}/parity`, { method: "PATCH", body: { parity } });
      setNotice(`${term.term_code} v${term.version} is an ${parity} term.`);
      await loadTerms();
      await loadPlan();
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  async function setCadence(value: string) {
    if (!school) return;
    setError("");
    try {
      await api(`/org/units/${school.id}`, { method: "PUT", body: { cadence: value } });
      setNotice(`Cadence set to ${value}.`);
      await loadSchools();
      await loadPlan();
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  async function setCap(value: number) {
    if (!school) return;
    setError("");
    try {
      await api(`/org/units/${school.id}`, { method: "PUT", body: { class_size_cap: value } });
      await loadSchools();
      await loadPlan();
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  async function generate() {
    if (!schoolId || !planTerm) return;
    setBusy(true);
    setError("");
    setResult(null);
    try {
      const expected: Record<string, number> = {};
      for (const [key, value] of Object.entries(intake)) {
        const n = Number(value);
        if (value.trim() && Number.isFinite(n) && n > 0) expected[key] = n;
      }
      setResult(
        await api<GenerationResult>(`/timetable/schools/${schoolId}/generate-sections`, {
          method: "POST",
          body: { term_code: planTerm, expected_intake: expected },
        }),
      );
      await loadPlan();
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
    }
  }

  async function addSection(programmeId: string) {
    setError("");
    try {
      await api("/timetable/sections", {
        method: "POST",
        body: { program_id: programmeId, label, term_code: planTerm },
      });
      setLabel("");
      setAddingTo(null);
      await loadPlan();
    } catch (err) {
      setError(String((err as Error).message));
    }
  }

  async function importSections(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError("");
    setImportResult(null);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("term_code", planTerm);
      setImportResult(await upload<ImportResult>("/timetable/sections/imports", form));
      await loadPlan();
    } catch (err) {
      setError(String((err as Error).message));
    } finally {
      setBusy(false);
    }
  }

  // ── render ────────────────────────────────────────────────────────────────
  function calendarTag(id: string) {
    const own = termsOf.get(id) ?? [];
    const approved = own.filter((t) => t.status === "approved");
    if (approved.length > 0) {
      const latest = approved.sort((a, b) => b.term_code.localeCompare(a.term_code))[0];
      return <span className="tag tag-accent">{latest.term_code}</span>;
    }
    if (own.some((t) => t.status === "draft")) {
      return <span className="tag tag-neutral">awaiting approval</span>;
    }
    return <span className="org-type">no calendar</span>;
  }

  return (
    <div className="uc-screen" style={{ maxWidth: 1180 }}>
      <div>
        <h3 style={{ margin: 0 }}>Terms &amp; sections</h3>
        <div className="uc-screen-sub">
          Every School runs its own academic calendar. Upload it, have the School Incharge approve
          it, then generate that term&rsquo;s Sections — in that order, because a Section cannot
          predate its calendar.
        </div>
      </div>

      <div className="org-stats">
        <div className="org-stat">
          <div className="n">{stats.approved}</div>
          <div className="l">Calendar approved</div>
        </div>
        <div className="org-stat">
          <div className="n">{stats.awaiting}</div>
          <div className="l">Awaiting approval</div>
        </div>
        <div className="org-stat">
          <div className="n">{stats.none}</div>
          <div className="l">No calendar yet</div>
        </div>
        {stats.unconfirmed > 0 && (
          <div className="org-stat">
            <div className="n">{stats.unconfirmed}</div>
            <div className="l" title="Cadence was defaulted by migration and needs confirming">
              Cadence unconfirmed
            </div>
          </div>
        )}
      </div>

      <div className="org-split">
        <div className="card">
          <div className="card-kicker">Schools</div>
          <div className="field" style={{ marginBottom: 10 }}>
            <input
              className="input"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Find a School by name or code"
            />
          </div>
          {schools.length === 0 ? (
            <p className="card-meta">
              No Schools yet — import the course catalogue on Org structure first.
            </p>
          ) : (
            <ul className="org-tree">
              {visibleSchools.map((s) => (
                <li key={s.id}>
                  <button
                    className="org-node"
                    aria-selected={s.id === schoolId}
                    onClick={() => setSchoolId(s.id)}
                  >
                    {/* In multi-School mode the rail doubles as the target picker. */}
                    {multiMode && s.id !== schoolId ? (
                      <input
                        type="checkbox"
                        checked={multiTargets.has(s.id)}
                        onChange={(e) => {
                          const next = new Set(multiTargets);
                          if (e.target.checked) next.add(s.id);
                          else next.delete(s.id);
                          setMultiTargets(next);
                        }}
                        onClick={(e) => e.stopPropagation()}
                      />
                    ) : (
                      <span className="twisty">·</span>
                    )}
                    <span className="label">{s.name}</span>
                    {s.cadence_unconfirmed && (
                      <span className="org-type" title="Cadence defaulted — confirm it">
                        cadence?
                      </span>
                    )}
                    {calendarTag(s.id)}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {visibleSchools.length === 0 && schools.length > 0 && (
            <p className="card-meta">Nothing matches &ldquo;{search}&rdquo;.</p>
          )}
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {!school ? (
            <div className="card">
              <div className="org-empty">
                Select a School on the left to manage its
                <br />
                academic calendar and per-term Sections.
              </div>
            </div>
          ) : (
            <>
              <div className="card">
                <div className="org-detail-head">
                  <h4>{school.name}</h4>
                  <span className="tag tag-outline">{school.code}</span>
                  <div style={{ flex: 1 }} />
                  <button className="btn btn-secondary" onClick={() => setUploading((v) => !v)}>
                    {uploading ? "Cancel" : "+ Upload calendar"}
                  </button>
                </div>

                <div
                  style={{
                    display: "flex",
                    gap: 16,
                    alignItems: "flex-end",
                    flexWrap: "wrap",
                    marginTop: 8,
                  }}
                >
                  <div className="field" style={{ marginBottom: 0, width: 170 }}>
                    <label>Curriculum cadence</label>
                    <select
                      className="input"
                      value={school.cadence ?? ""}
                      onChange={(e) => void setCadence(e.target.value)}
                    >
                      <option value="semester">Semester</option>
                      <option value="yearly">Yearly</option>
                    </select>
                  </div>
                  <div className="field" style={{ marginBottom: 0, width: 150 }}>
                    <label>Class-size cap</label>
                    <input
                      className="input"
                      type="number"
                      min={1}
                      max={500}
                      defaultValue={school.class_size_cap ?? 60}
                      key={`${school.id}-cap`}
                      onBlur={(e) => {
                        const n = Number(e.target.value);
                        if (n > 0 && n !== (school.class_size_cap ?? 60)) void setCap(n);
                      }}
                    />
                  </div>
                  <p className="card-meta" style={{ flex: 1, minWidth: 220 }}>
                    {school.cadence_unconfirmed
                      ? "Cadence was defaulted when this School was migrated — confirm it. It sets every Programme's ladder."
                      : `A 4-year Programme here has ${school.cadence === "yearly" ? "4 years" : "8 semesters"}. The cap defaults to the university-wide 60.`}
                  </p>
                </div>

                <div className="card-kicker" style={{ marginTop: 12 }}>
                  Academic calendar
                </div>

                {uploading && (
                  <form onSubmit={uploadTerm} style={{ marginTop: 10 }}>
                    <div className="org-form-grid">
                      <div className="field">
                        <label>Term code</label>
                        <input
                          className="input"
                          value={termForm.term_code}
                          placeholder="2026-S1"
                          autoFocus
                          required
                          onChange={(e) => setTermForm({ ...termForm, term_code: e.target.value })}
                        />
                      </div>
                      <div className="field">
                        <label>Parity</label>
                        <select
                          className="input"
                          value={termForm.parity}
                          onChange={(e) => setTermForm({ ...termForm, parity: e.target.value })}
                        >
                          <option value="odd">Odd (semesters 1, 3, 5, 7)</option>
                          <option value="even">Even (semesters 2, 4, 6, 8)</option>
                        </select>
                      </div>
                      <div className="field">
                        <label>Start date</label>
                        <input
                          className="input"
                          type="date"
                          value={termForm.start_date}
                          required
                          onChange={(e) => setTermForm({ ...termForm, start_date: e.target.value })}
                        />
                      </div>
                      <div className="field">
                        <label>End date</label>
                        <input
                          className="input"
                          type="date"
                          value={termForm.end_date}
                          required
                          onChange={(e) => setTermForm({ ...termForm, end_date: e.target.value })}
                        />
                      </div>
                      <div className="field">
                        <label>Archival backstop (optional)</label>
                        <input
                          className="input"
                          type="date"
                          value={termForm.archival_backstop_date}
                          onChange={(e) =>
                            setTermForm({ ...termForm, archival_backstop_date: e.target.value })
                          }
                        />
                      </div>
                    </div>

                    <label
                      className="card-meta"
                      style={{ display: "flex", gap: 6, alignItems: "center", marginBottom: 8 }}
                    >
                      <input
                        type="checkbox"
                        checked={multiMode}
                        onChange={(e) => {
                          setMultiMode(e.target.checked);
                          setMultiTargets(new Set());
                        }}
                      />
                      Apply to several Schools — tick them on the left
                      {multiMode && multiTargets.size > 0 && ` (${multiTargets.size} more selected)`}
                    </label>

                    <button className="btn btn-primary" type="submit" disabled={busy}>
                      {busy ? "Applying…" : multiMode ? "Apply to selected Schools" : "Upload as draft"}
                    </button>
                    <p className="card-meta" style={{ marginTop: 8 }}>
                      {multiMode
                        ? "Each School gets its own draft — its School Incharge still approves, and may amend the dates first. A School that already has this term gets a new version, never an overwrite."
                        : "Re-using a term code amends the calendar: it lands as a new draft version and supersedes the approved one only once it is itself approved."}
                    </p>
                  </form>
                )}

                {multiResult && (
                  <table className="table">
                    <thead>
                      <tr>
                        <th>School</th>
                        <th>Outcome</th>
                        <th>Version</th>
                      </tr>
                    </thead>
                    <tbody>
                      {multiResult.map((r) => (
                        <tr key={r.school_id}>
                          <td>{r.school_name}</td>
                          <td>
                            <span
                              className={
                                r.outcome === "skipped" ? "tag tag-neutral" : "tag tag-accent"
                              }
                            >
                              {r.outcome}
                            </span>
                            {r.detail && <span className="card-meta"> {r.detail}</span>}
                          </td>
                          <td>{r.version ? `v${r.version}` : "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}

                {grouped.length === 0 ? (
                  <p className="card-meta">
                    No calendar uploaded for this School yet. Sections cannot be created until one
                    is approved.
                  </p>
                ) : (
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Term</th>
                        <th>Version</th>
                        <th>Dates</th>
                        <th>Parity</th>
                        <th>Status</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {grouped.flatMap((group) =>
                        group.versions.map((term, i) => (
                          <tr key={term.id}>
                            <td>{i === 0 ? <strong>{group.code}</strong> : ""}</td>
                            <td>v{term.version}</td>
                            <td>
                              {term.start_date} → {term.end_date}
                            </td>
                            <td>
                              {term.parity ? (
                                <span className="tag tag-neutral">{term.parity}</span>
                              ) : (
                                // Calendars uploaded before parity existed carry
                                // none, and generation refuses to run without it —
                                // so it is fixable here rather than a dead end.
                                <select
                                  className="input"
                                  style={{ width: 110, padding: "4px 8px" }}
                                  defaultValue=""
                                  onChange={(e) =>
                                    e.target.value && void setParity(term, e.target.value)
                                  }
                                >
                                  <option value="" disabled>
                                    set…
                                  </option>
                                  <option value="odd">odd</option>
                                  <option value="even">even</option>
                                </select>
                              )}
                            </td>
                            <td>
                              <span
                                className={
                                  term.status === "approved"
                                    ? "tag tag-accent"
                                    : term.status === "draft"
                                      ? "tag tag-outline"
                                      : "tag tag-neutral"
                                }
                              >
                                {term.status}
                              </span>
                            </td>
                            <td style={{ textAlign: "right" }}>
                              {term.status === "draft" && (
                                <button
                                  className="btn btn-secondary"
                                  onClick={() => void approve(term)}
                                >
                                  Approve
                                </button>
                              )}
                            </td>
                          </tr>
                        )),
                      )}
                    </tbody>
                  </table>
                )}
                {notice && <p className="card-meta">{notice}</p>}
              </div>

              <div className="card">
                <div className="org-detail-head">
                  <div className="card-kicker">Sections · Timetable Cell</div>
                  <div style={{ flex: 1 }} />
                  {approvedCodes.length > 0 && (
                    <>
                      <select
                        className="input"
                        style={{ width: 150 }}
                        value={planTerm}
                        onChange={(e) => setPlanTerm(e.target.value)}
                      >
                        {approvedCodes.map((code) => (
                          <option key={code}>{code}</option>
                        ))}
                      </select>
                      <button className="btn btn-secondary" onClick={() => setShowImport((v) => !v)}>
                        {showImport ? "Close upload" : "Bulk upload"}
                      </button>
                    </>
                  )}
                </div>

                {approvedCodes.length === 0 ? (
                  <p className="card-meta">
                    Sections open per term, so this School needs an <strong>approved</strong>{" "}
                    calendar first. Upload one above and approve it.
                  </p>
                ) : planError ? (
                  <>
                    <p className="error">{planError}</p>
                    {planError.toLowerCase().includes("parity") && (
                      <p className="card-meta">
                        Set it in the <strong>Parity</strong> column of the calendar above — it
                        decides which half of every semester ladder runs this term.
                      </p>
                    )}
                  </>
                ) : !plan ? (
                  <p className="card-meta">Loading the ladder…</p>
                ) : (
                  <>
                    <p className="card-meta">
                      {plan.parity} term · a Programme&rsquo;s {plan.rows[0]?.cadence ?? "semester"}{" "}
                      ladder runs its <strong>{plan.parity}</strong> positions this term. Each
                      position needs ⌈headcount ÷ cap⌉ Sections.
                    </p>

                    {plan.warnings.length > 0 && (
                      <p className="card-meta">
                        {plan.warnings.map((w) => (
                          <span key={w} className="tag tag-neutral" style={{ marginRight: 6 }}>
                            {w}
                          </span>
                        ))}
                      </p>
                    )}

                    {showImport && (
                      <form
                        onSubmit={importSections}
                        style={{ display: "flex", gap: 10, alignItems: "flex-end", margin: "6px 0" }}
                      >
                        <div className="field" style={{ marginBottom: 0, flex: 1, maxWidth: 320 }}>
                          <label>CSV file — creates Sections in {planTerm}</label>
                          <input
                            className="input"
                            type="file"
                            accept=".csv,text/csv"
                            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                            required
                          />
                        </div>
                        <button className="btn btn-primary" type="submit" disabled={busy || !file}>
                          {busy ? "Uploading…" : "Upload"}
                        </button>
                      </form>
                    )}
                    {importResult && (
                      <p className="card-meta">
                        {importResult.rows_created} created · {importResult.rows_rejected} rejected
                      </p>
                    )}

                    <table className="table">
                      <thead>
                        <tr>
                          <th>Programme · position</th>
                          <th>Students</th>
                          <th>Needs</th>
                          <th>Sections</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {plan.rows.map((row) => {
                          const key = `${row.programme_id}:${row.position}`;
                          return (
                            <tr key={key}>
                              <td>
                                {row.programme_name}
                                <div className="card-meta">
                                  {row.cadence === "yearly"
                                    ? `Year ${row.position}`
                                    : `Semester ${row.position} · Year ${row.year}`}
                                </div>
                              </td>
                              <td>
                                {row.headcount_source === "none" ? (
                                  <input
                                    className="input"
                                    style={{ width: 90 }}
                                    type="number"
                                    min={1}
                                    placeholder="expected"
                                    value={intake[key] ?? ""}
                                    onChange={(e) =>
                                      setIntake({ ...intake, [key]: e.target.value })
                                    }
                                  />
                                ) : (
                                  <>
                                    {row.headcount}
                                    <div className="card-meta">{row.headcount_source}</div>
                                  </>
                                )}
                              </td>
                              <td>
                                {row.required}
                                <div className="card-meta">cap {row.class_size_cap}</div>
                              </td>
                              <td>
                                <span style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                                  {row.existing.map((s) => (
                                    <span className="tag tag-outline" key={s.id}>
                                      {s.name}
                                    </span>
                                  ))}
                                  {row.to_create.map((name) => (
                                    <span
                                      className="tag tag-neutral"
                                      key={name}
                                      title="Will be created on Generate"
                                    >
                                      + {name}
                                    </span>
                                  ))}
                                  {row.existing.length === 0 && row.to_create.length === 0 && (
                                    <span className="org-type">none</span>
                                  )}
                                </span>
                              </td>
                              <td style={{ textAlign: "right", width: 200 }}>
                                {addingTo === key ? (
                                  <span
                                    style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}
                                  >
                                    <input
                                      className="input"
                                      style={{ width: 90 }}
                                      value={label}
                                      placeholder="label"
                                      autoFocus
                                      onChange={(e) => setLabel(e.target.value)}
                                      onKeyDown={(e) => {
                                        if (e.key === "Enter" && label)
                                          void addSection(row.programme_id);
                                        if (e.key === "Escape") setAddingTo(null);
                                      }}
                                    />
                                    <button
                                      className="btn btn-primary"
                                      disabled={!label}
                                      onClick={() => void addSection(row.programme_id)}
                                    >
                                      Add
                                    </button>
                                  </span>
                                ) : (
                                  <button
                                    className="btn btn-ghost"
                                    onClick={() => {
                                      setLabel("");
                                      setAddingTo(key);
                                    }}
                                  >
                                    + Extra
                                  </button>
                                )}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>

                    {plan.rows.length === 0 ? (
                      <p className="card-meta">
                        Nothing to generate — no Programme in this School has a duration set.
                      </p>
                    ) : (
                      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                        <button
                          className="btn btn-primary"
                          onClick={() => void generate()}
                          disabled={busy || toCreateCount === 0}
                        >
                          {busy
                            ? "Generating…"
                            : toCreateCount === 0
                              ? "Nothing to generate"
                              : `Generate ${toCreateCount} Section${toCreateCount === 1 ? "" : "s"}`}
                        </button>
                        <span className="card-meta">
                          {existingCount} already exist · re-running never duplicates or renames
                        </span>
                      </div>
                    )}

                    {result && (
                      <p className="card-meta">
                        Created {result.created.length} · {result.existing} left untouched
                      </p>
                    )}
                  </>
                )}
              </div>

              {approvedCodes.length > 0 && showImport && <TemplateLinks only={["sections"]} />}
            </>
          )}
        </div>
      </div>
      {error && <p className="error">{error}</p>}
    </div>
  );
}
