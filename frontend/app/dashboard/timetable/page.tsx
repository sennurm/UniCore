"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, api } from "@/lib/api";

type Unit = {
  id: string;
  type: string;
  name: string;
  code: string;
  parent_id: string | null;
  term_code: string | null;
  position: number | null;
};
type Term = { school_id: string; term_code: string; status: string; parity: string | null };
type Period = { id: string; name: string; sequence: number; start_time: string; end_time: string };
type Grid = {
  id: string;
  school_id: string;
  version: number;
  name: string;
  status: string;
  periods: Period[];
};
type Draft = { id: string; school_id: string; term_code: string; version: number; status: string };
type Approval = {
  department_id: string;
  department_name: string;
  status: string;
  reason: string | null;
};
type DraftStatus = {
  draft_id: string;
  term_code: string;
  version: number;
  status: string;
  entry_count: number;
  approvals: Approval[];
  publishable: boolean;
  blocking: string[];
};
type Row = {
  entry_id: string;
  section_id: string;
  section_name: string;
  day_of_week: number;
  period_name: string;
  subject_code: string;
  subject_name: string;
  faculty_name: string;
  venue_code: string;
};
type Offering = {
  id: string;
  position: number | null;
  program_id: string | null;
  subject: { code: string; name: string; kind: string; elective_group: string | null };
};
type Venue = { id: string; code: string; name: string; capacity: number; kind: string };
type Person = { user_id: string; full_name: string; kind: string };
type Clash = { kind: string; message: string };

/** Which days appear is the School's decision, not a constant: a Nursing School
 *  teaches Sunday for clinical postings while Engineering next door does not. */
const DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
type WorkingPattern = { days: Record<string, boolean | number[]>; is_default: boolean };

const BLANK_PERIOD = { name: "", start_time: "09:00", end_time: "10:00" };

function hhmm(value: string): string {
  return value.slice(0, 5);
}

export default function TimetablePage() {
  const [units, setUnits] = useState<Unit[]>([]);
  const [terms, setTerms] = useState<Term[]>([]);
  const [venues, setVenues] = useState<Venue[]>([]);
  const [staff, setStaff] = useState<Person[]>([]);

  const [schoolId, setSchoolId] = useState<string | null>(null);
  const [pattern, setPattern] = useState<WorkingPattern | null>(null);
  const [search, setSearch] = useState("");
  const [termCode, setTermCode] = useState("");

  const [grids, setGrids] = useState<Grid[]>([]);
  const [gridForm, setGridForm] = useState<{ name: string; periods: typeof BLANK_PERIOD[] }>({
    name: "",
    periods: [{ ...BLANK_PERIOD, name: "P1" }],
  });
  const [editingGrid, setEditingGrid] = useState(false);

  const [draft, setDraft] = useState<Draft | null>(null);
  const [status, setStatus] = useState<DraftStatus | null>(null);
  const [rows, setRows] = useState<Row[]>([]);

  const [sectionId, setSectionId] = useState("");
  const [offerings, setOfferings] = useState<Offering[]>([]);
  const [placing, setPlacing] = useState<{ day: number; period: Period } | null>(null);
  const [entryForm, setEntryForm] = useState({ offering_id: "", faculty_user_id: "", venue_id: "" });

  const [clashes, setClashes] = useState<Clash[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    void (async () => {
      try {
        const [allUnits, allTerms, allVenues, directory] = await Promise.all([
          api<Unit[]>("/org/units?limit=2000"),
          api<Term[]>("/timetable/terms"),
          api<Venue[]>("/org/venues?limit=2000"),
          api<Person[]>("/rbac/directory?limit=2000"),
        ]);
        setUnits(allUnits);
        setTerms(allTerms);
        setVenues(allVenues);
        setStaff(directory.filter((p) => p.kind === "staff"));
      } catch (err) {
        setError(String((err as Error).message));
      }
    })();
  }, []);

  // ── derived ───────────────────────────────────────────────────────────────
  const schools = useMemo(() => units.filter((u) => u.type === "school"), [units]);
  const byId = useMemo(() => new Map(units.map((u) => [u.id, u])), [units]);
  const school = useMemo(() => schools.find((s) => s.id === schoolId) ?? null, [schools, schoolId]);

  useEffect(() => {
    if (!schoolId) {
      setPattern(null);
      return;
    }
    void api<WorkingPattern>(`/timetable/schools/${schoolId}/working-pattern`)
      .then(setPattern)
      .catch(() => setPattern(null));
  }, [schoolId]);

  /** The columns this School actually teaches. An nth-weekday rule ("Saturdays:
   *  1st and 3rd") still shows the column — a weekly entry is placed on the
   *  weekday; which dates it runs on is the calendar's business. */
  const days = useMemo(() => {
    const rule = pattern?.days ?? { "1": true, "2": true, "3": true, "4": true, "5": true, "6": true };
    return DAY_LABELS.map((label, i) => ({ n: i + 1, label }))
      .filter((d) => {
        const value = rule[String(d.n)];
        return value === true || (Array.isArray(value) && value.length > 0);
      });
  }, [pattern]);

  const visibleSchools = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return schools;
    return schools.filter(
      (s) => s.name.toLowerCase().includes(needle) || s.code.toLowerCase().includes(needle),
    );
  }, [schools, search]);

  const approvedTerms = useMemo(
    () =>
      Array.from(
        new Set(
          terms.filter((t) => t.school_id === schoolId && t.status === "approved").map(
            (t) => t.term_code,
          ),
        ),
      ).sort((a, b) => b.localeCompare(a)),
    [terms, schoolId],
  );

  const activeGrid = useMemo(() => grids.find((g) => g.status === "active") ?? null, [grids]);

  /** Sections of the selected School for the chosen term. */
  const sections = useMemo(() => {
    if (!schoolId || !termCode) return [];
    return units
      .filter((u) => u.type === "section" && u.term_code === termCode)
      .filter((u) => {
        // section → programme → department → school
        const programme = u.parent_id ? byId.get(u.parent_id) : undefined;
        const department = programme?.parent_id ? byId.get(programme.parent_id) : undefined;
        return department?.parent_id === schoolId;
      })
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [units, byId, schoolId, termCode]);

  const section = useMemo(
    () => sections.find((s) => s.id === sectionId) ?? null,
    [sections, sectionId],
  );

  /** Entries of the chosen Section, keyed by "day:period" for grid lookup. */
  const placed = useMemo(() => {
    const map = new Map<string, Row>();
    for (const row of rows) {
      if (row.section_id === sectionId) map.set(`${row.day_of_week}:${row.period_name}`, row);
    }
    return map;
  }, [rows, sectionId]);

  const loadGrids = useCallback(async () => {
    if (!schoolId) return;
    setGrids(await api<Grid[]>(`/timetable/schools/${schoolId}/grids`));
  }, [schoolId]);

  const loadDraft = useCallback(async () => {
    if (!draft) {
      setStatus(null);
      setRows([]);
      return;
    }
    const [state, entries] = await Promise.all([
      api<DraftStatus>(`/timetable/drafts/${draft.id}`),
      api<Row[]>(`/timetable/drafts/${draft.id}/entries`),
    ]);
    setStatus(state);
    setRows(entries);
  }, [draft]);

  useEffect(() => {
    void loadGrids().catch((err) => setError(String((err as Error).message)));
  }, [loadGrids]);

  useEffect(() => {
    void loadDraft().catch((err) => setError(String((err as Error).message)));
  }, [loadDraft]);

  useEffect(() => {
    setDraft(null);
    setSectionId("");
    setClashes([]);
    setNotice("");
    setTermCode(approvedTerms[0] ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [schoolId]);

  // What may be taught to this Section: its Programme's offerings at its position.
  useEffect(() => {
    if (!section?.parent_id) {
      setOfferings([]);
      return;
    }
    const query = section.position ? `?position=${section.position}` : "";
    api<Offering[]>(`/org/programmes/${section.parent_id}/offerings${query}`)
      .then(setOfferings)
      .catch(() => setOfferings([]));
  }, [section]);

  // ── actions ───────────────────────────────────────────────────────────────
  function report(err: unknown) {
    const apiError = err as ApiError;
    const detail = apiError.detail as { clashes?: Clash[] } | undefined;
    if (detail?.clashes) {
      setClashes(detail.clashes);
      setError("");
    } else {
      setClashes([]);
      setError(String(apiError.message));
    }
  }

  async function saveGrid(e: React.FormEvent) {
    e.preventDefault();
    if (!schoolId) return;
    setBusy(true);
    setError("");
    try {
      await api("/timetable/grids", {
        method: "POST",
        body: {
          school_id: schoolId,
          name: gridForm.name,
          periods: gridForm.periods.map((p, i) => ({ ...p, sequence: i + 1 })),
        },
      });
      setEditingGrid(false);
      setNotice("Grid saved as a new version.");
      await loadGrids();
    } catch (err) {
      report(err);
    } finally {
      setBusy(false);
    }
  }

  async function openDraft() {
    if (!schoolId || !termCode) return;
    setBusy(true);
    setError("");
    setClashes([]);
    try {
      setDraft(
        await api<Draft>("/timetable/drafts", {
          method: "POST",
          body: { school_id: schoolId, term_code: termCode },
        }),
      );
    } catch (err) {
      report(err);
    } finally {
      setBusy(false);
    }
  }

  async function placeEntry(acknowledge = false) {
    if (!draft || !placing || !sectionId) return;
    setBusy(true);
    setClashes([]);
    setError("");
    try {
      await api(`/timetable/drafts/${draft.id}/entries`, {
        method: "POST",
        body: {
          section_id: sectionId,
          day_of_week: placing.day,
          period_id: placing.period.id,
          ...entryForm,
          acknowledge_capacity: acknowledge,
        },
      });
      setPlacing(null);
      setEntryForm({ offering_id: "", faculty_user_id: "", venue_id: "" });
      await loadDraft();
    } catch (err) {
      report(err);
    } finally {
      setBusy(false);
    }
  }

  async function removeEntry(entryId: string) {
    setError("");
    try {
      await api(`/timetable/entries/${entryId}`, { method: "DELETE" });
      await loadDraft();
    } catch (err) {
      report(err);
    }
  }

  async function decide(departmentId: string, approve: boolean) {
    if (!draft) return;
    setError("");
    try {
      const reason = approve ? null : window.prompt("Reason for rejecting?");
      if (!approve && !reason) return;
      await api(`/timetable/drafts/${draft.id}/approvals`, {
        method: "POST",
        body: { department_id: departmentId, approve, reason },
      });
      await loadDraft();
    } catch (err) {
      report(err);
    }
  }

  async function publish() {
    if (!draft) return;
    setBusy(true);
    setError("");
    setClashes([]);
    try {
      const published = await api<Draft>(`/timetable/drafts/${draft.id}/publish`, {
        method: "POST",
      });
      setDraft(published);
      setNotice(`Published version ${published.version}.`);
      await loadDraft();
    } catch (err) {
      report(err);
    } finally {
      setBusy(false);
    }
  }

  // ── render ────────────────────────────────────────────────────────────────
  const readOnly = draft?.status !== "draft";

  return (
    <div className="uc-screen" style={{ maxWidth: 1180 }}>
      <div>
        <h3 style={{ margin: 0 }}>Timetable</h3>
        <div className="uc-screen-sub">
          Define the School&rsquo;s teaching day, place classes in it, collect each
          Department&rsquo;s approval, then publish. A clash is refused as you save; a too-small
          room warns and asks you to confirm.
        </div>
      </div>

      <div className="org-split">
        <div className="card">
          <div className="card-kicker">Schools</div>
          <div className="field" style={{ marginBottom: 10 }}>
            <input
              className="input"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Find a School"
            />
          </div>
          <ul className="org-tree">
            {visibleSchools.map((s) => (
              <li key={s.id}>
                <button
                  className="org-node"
                  aria-selected={s.id === schoolId}
                  onClick={() => setSchoolId(s.id)}
                >
                  <span className="twisty">·</span>
                  <span className="label">{s.name}</span>
                  <span className="org-type">{s.code}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {!school ? (
            <div className="card">
              <div className="org-empty">
                Select a School to define its teaching day
                <br />
                and build its timetable.
              </div>
            </div>
          ) : (
            <>
              {/* --- period grid ------------------------------------------ */}
              <div className="card">
                <div className="org-detail-head">
                  <h4>{school.name}</h4>
                  <span className="tag tag-outline">{school.code}</span>
                  <div style={{ flex: 1 }} />
                  <button
                    className="btn btn-secondary"
                    onClick={() => {
                      setEditingGrid((v) => !v);
                      setGridForm({
                        name: activeGrid ? `${activeGrid.name} (revised)` : "Standard day",
                        periods: activeGrid
                          ? activeGrid.periods.map((p) => ({
                              name: p.name,
                              start_time: hhmm(p.start_time),
                              end_time: hhmm(p.end_time),
                            }))
                          : [{ ...BLANK_PERIOD, name: "P1" }],
                      });
                    }}
                  >
                    {editingGrid ? "Cancel" : activeGrid ? "New grid version" : "Define grid"}
                  </button>
                </div>
                <div className="card-kicker" style={{ marginTop: 8 }}>
                  Period grid
                </div>

                {activeGrid ? (
                  <p className="card-meta">
                    <strong>{activeGrid.name}</strong> · v{activeGrid.version} ·{" "}
                    {activeGrid.periods.map((p) => (
                      <span className="tag tag-neutral" key={p.id} style={{ marginRight: 4 }}>
                        {p.name} {hhmm(p.start_time)}–{hhmm(p.end_time)}
                      </span>
                    ))}
                  </p>
                ) : (
                  <p className="card-meta">
                    No grid yet. Define the teaching day before drafting a timetable.
                  </p>
                )}

                {editingGrid && (
                  <form onSubmit={saveGrid} style={{ marginTop: 8 }}>
                    <div className="field" style={{ maxWidth: 280 }}>
                      <label>Grid name</label>
                      <input
                        className="input"
                        value={gridForm.name}
                        required
                        onChange={(e) => setGridForm({ ...gridForm, name: e.target.value })}
                      />
                    </div>
                    <table className="table">
                      <thead>
                        <tr>
                          <th>#</th>
                          <th>Name</th>
                          <th>Starts</th>
                          <th>Ends</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {gridForm.periods.map((p, i) => (
                          <tr key={i}>
                            <td>{i + 1}</td>
                            <td>
                              <input
                                className="input"
                                style={{ width: 90 }}
                                value={p.name}
                                required
                                onChange={(e) => {
                                  const next = [...gridForm.periods];
                                  next[i] = { ...p, name: e.target.value };
                                  setGridForm({ ...gridForm, periods: next });
                                }}
                              />
                            </td>
                            {(["start_time", "end_time"] as const).map((key) => (
                              <td key={key}>
                                <input
                                  className="input"
                                  style={{ width: 110 }}
                                  type="time"
                                  value={p[key]}
                                  required
                                  onChange={(e) => {
                                    const next = [...gridForm.periods];
                                    next[i] = { ...p, [key]: e.target.value };
                                    setGridForm({ ...gridForm, periods: next });
                                  }}
                                />
                              </td>
                            ))}
                            <td style={{ textAlign: "right" }}>
                              {gridForm.periods.length > 1 && (
                                <button
                                  className="btn btn-ghost"
                                  type="button"
                                  onClick={() =>
                                    setGridForm({
                                      ...gridForm,
                                      periods: gridForm.periods.filter((_, j) => j !== i),
                                    })
                                  }
                                >
                                  Remove
                                </button>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <button
                      className="btn btn-ghost"
                      type="button"
                      onClick={() =>
                        setGridForm({
                          ...gridForm,
                          periods: [
                            ...gridForm.periods,
                            { ...BLANK_PERIOD, name: `P${gridForm.periods.length + 1}` },
                          ],
                        })
                      }
                    >
                      + Period
                    </button>
                    <button className="btn btn-primary" type="submit" disabled={busy}>
                      {busy ? "Saving…" : "Save grid version"}
                    </button>
                    <p className="card-meta" style={{ marginTop: 6 }}>
                      Grids are versioned, never edited: changing one in place would move classes
                      for people already holding the published timetable.
                    </p>
                  </form>
                )}
              </div>

              {/* --- draft + approvals ------------------------------------ */}
              <div className="card">
                <div className="org-detail-head">
                  <div className="card-kicker">Timetable draft</div>
                  <div style={{ flex: 1 }} />
                  <select
                    className="input"
                    style={{ width: 150 }}
                    value={termCode}
                    onChange={(e) => {
                      setTermCode(e.target.value);
                      setDraft(null);
                    }}
                  >
                    {approvedTerms.length === 0 && <option value="">no approved term</option>}
                    {approvedTerms.map((code) => (
                      <option key={code}>{code}</option>
                    ))}
                  </select>
                  <button
                    className="btn btn-secondary"
                    onClick={() => void openDraft()}
                    disabled={!termCode || !activeGrid || busy}
                  >
                    {draft ? "Reload" : "Open draft"}
                  </button>
                </div>

                {!activeGrid ? (
                  <p className="card-meta">A period grid is needed before drafting.</p>
                ) : !draft ? (
                  <p className="card-meta">
                    Open the draft for {termCode || "a term"} to start placing classes.
                  </p>
                ) : (
                  <>
                    <p className="card-meta">
                      Version {draft.version} ·{" "}
                      <span
                        className={
                          draft.status === "published" ? "tag tag-accent" : "tag tag-outline"
                        }
                      >
                        {draft.status}
                      </span>{" "}
                      · {status?.entry_count ?? 0} classes placed
                    </p>

                    {status && status.approvals.length > 0 && (
                      <table className="table">
                        <thead>
                          <tr>
                            <th>Department</th>
                            <th>Approval</th>
                            <th />
                          </tr>
                        </thead>
                        <tbody>
                          {status.approvals.map((a) => (
                            <tr key={a.department_id}>
                              <td>{a.department_name}</td>
                              <td>
                                <span
                                  className={
                                    a.status === "approved"
                                      ? "tag tag-accent"
                                      : a.status === "rejected"
                                        ? "tag tag-outline"
                                        : "tag tag-neutral"
                                  }
                                >
                                  {a.status}
                                </span>
                                {a.reason && <span className="card-meta"> {a.reason}</span>}
                              </td>
                              <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                                {!readOnly && (
                                  <>
                                    <button
                                      className="btn btn-ghost"
                                      onClick={() => void decide(a.department_id, true)}
                                    >
                                      Approve
                                    </button>
                                    <button
                                      className="btn btn-ghost"
                                      onClick={() => void decide(a.department_id, false)}
                                    >
                                      Reject
                                    </button>
                                  </>
                                )}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}

                    {!readOnly && (
                      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                        <button
                          className="btn btn-primary"
                          onClick={() => void publish()}
                          disabled={busy || !status?.publishable}
                        >
                          {busy ? "Publishing…" : "Publish"}
                        </button>
                        {status && status.blocking.length > 0 && (
                          <span className="card-meta">Blocked: {status.blocking.join("; ")}</span>
                        )}
                      </div>
                    )}
                    {notice && <p className="card-meta">{notice}</p>}
                  </>
                )}
              </div>

              {/* --- the weekly grid -------------------------------------- */}
              {draft && activeGrid && (
                <div className="card">
                  <div className="org-detail-head">
                    <div className="card-kicker">Weekly grid</div>
                    <div style={{ flex: 1 }} />
                    <select
                      className="input"
                      style={{ width: 200 }}
                      value={sectionId}
                      onChange={(e) => {
                        setSectionId(e.target.value);
                        setPlacing(null);
                      }}
                    >
                      <option value="">— select a Section —</option>
                      {sections.map((s) => (
                        <option key={s.id} value={s.id}>
                          {byId.get(s.parent_id ?? "")?.code} · {s.name}
                        </option>
                      ))}
                    </select>
                  </div>

                  {clashes.length > 0 && (
                    <div className="card" style={{ borderColor: "var(--color-accent-400)" }}>
                      <div className="card-kicker">Clash — not saved</div>
                      {clashes.map((c, i) => (
                        <p className="card-meta" key={i}>
                          <span className="tag tag-outline">{c.kind}</span> {c.message}
                        </p>
                      ))}
                    </div>
                  )}

                  {!sectionId ? (
                    <p className="card-meta">
                      {sections.length === 0
                        ? "No Sections exist for this term — generate them on Terms & sections first."
                        : "Pick a Section to lay out its week."}
                    </p>
                  ) : (
                    <>
                      <table className="table">
                        <thead>
                          <tr>
                            <th style={{ width: 130 }}>Period</th>
                            {days.map((d) => (
                              <th key={d.n}>{d.label}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {activeGrid.periods.map((period) => (
                            <tr key={period.id}>
                              <td>
                                <strong>{period.name}</strong>
                                <div className="card-meta">
                                  {hhmm(period.start_time)}–{hhmm(period.end_time)}
                                </div>
                              </td>
                              {days.map((day) => {
                                const cell = placed.get(`${day.n}:${period.name}`);
                                const isPlacing =
                                  placing?.day === day.n && placing?.period.id === period.id;
                                return (
                                  <td key={day.n} style={{ verticalAlign: "top" }}>
                                    {cell ? (
                                      <>
                                        <strong>{cell.subject_code}</strong>
                                        <div className="card-meta">{cell.faculty_name}</div>
                                        <div className="card-meta">{cell.venue_code}</div>
                                        {!readOnly && (
                                          <button
                                            className="btn btn-ghost"
                                            onClick={() => void removeEntry(cell.entry_id)}
                                          >
                                            Remove
                                          </button>
                                        )}
                                      </>
                                    ) : isPlacing ? (
                                      <span className="org-type">selected below ↓</span>
                                    ) : readOnly ? (
                                      <span className="org-type">—</span>
                                    ) : (
                                      <button
                                        className="btn btn-ghost"
                                        onClick={() => {
                                          setPlacing({ day: day.n, period });
                                          setClashes([]);
                                        }}
                                      >
                                        +
                                      </button>
                                    )}
                                  </td>
                                );
                              })}
                            </tr>
                          ))}
                        </tbody>
                      </table>

                      {placing && (
                        <div className="card">
                          <div className="card-kicker">
                            {days.find((d) => d.n === placing.day)?.label} · {placing.period.name}{" "}
                            {hhmm(placing.period.start_time)}–{hhmm(placing.period.end_time)}
                          </div>
                          <div className="org-form-grid">
                            <div className="field">
                              <label>Subject</label>
                              <select
                                className="input"
                                value={entryForm.offering_id}
                                onChange={(e) =>
                                  setEntryForm({ ...entryForm, offering_id: e.target.value })
                                }
                              >
                                <option value="">— select —</option>
                                {offerings.map((o) => (
                                  <option key={o.id} value={o.id}>
                                    {o.subject.code} · {o.subject.name}
                                    {o.subject.elective_group
                                      ? ` (${o.subject.elective_group} elective)`
                                      : ""}
                                  </option>
                                ))}
                              </select>
                            </div>
                            <div className="field">
                              <label>Faculty</label>
                              <select
                                className="input"
                                value={entryForm.faculty_user_id}
                                onChange={(e) =>
                                  setEntryForm({ ...entryForm, faculty_user_id: e.target.value })
                                }
                              >
                                <option value="">— select —</option>
                                {staff.map((p) => (
                                  <option key={p.user_id} value={p.user_id}>
                                    {p.full_name}
                                  </option>
                                ))}
                              </select>
                            </div>
                            <div className="field">
                              <label>Venue</label>
                              <select
                                className="input"
                                value={entryForm.venue_id}
                                onChange={(e) =>
                                  setEntryForm({ ...entryForm, venue_id: e.target.value })
                                }
                              >
                                <option value="">— select —</option>
                                {venues.map((v) => (
                                  <option key={v.id} value={v.id}>
                                    {v.code} · {v.kind}, seats {v.capacity}
                                  </option>
                                ))}
                              </select>
                            </div>
                          </div>
                          <button
                            className="btn btn-primary"
                            disabled={
                              busy ||
                              !entryForm.offering_id ||
                              !entryForm.faculty_user_id ||
                              !entryForm.venue_id
                            }
                            onClick={() => void placeEntry()}
                          >
                            {busy ? "Placing…" : "Place class"}
                          </button>
                          <button
                            className="btn btn-ghost"
                            onClick={() => {
                              setPlacing(null);
                              setClashes([]);
                            }}
                          >
                            Cancel
                          </button>
                          {error.includes("seats") && (
                            <button
                              className="btn btn-secondary"
                              onClick={() => void placeEntry(true)}
                            >
                              Place anyway
                            </button>
                          )}
                          {offerings.length === 0 && (
                            <p className="card-meta" style={{ marginTop: 6 }}>
                              No subjects are offered to this Section&rsquo;s Programme at its
                              position — add them to the subject catalogue first.
                            </p>
                          )}
                        </div>
                      )}
                    </>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
      {error && <p className="error">{error}</p>}
    </div>
  );
}
