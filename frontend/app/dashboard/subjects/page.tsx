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
  duration_years: number | null;
};
type Subject = {
  id: string;
  code: string;
  name: string;
  department_id: string;
  kind: string;
  elective_group: string | null;
  credits: number;
  theory_hours: number;
  lab_hours: number;
  status: string;
};
type Offering = {
  id: string;
  subject_id: string;
  program_id: string | null;
  position: number | null;
  capacity: number | null;
  seats_taken: number;
  subject: Subject;
};
type Venue = {
  id: string;
  code: string;
  name: string;
  campus_code: string | null;
  building: string | null;
  room: string | null;
  capacity: number;
  kind: string;
  status: string;
};
type ImportResult = {
  rows_total?: number;
  subjects_created?: number;
  offerings_created?: number;
  rows_created?: number;
  rows_updated?: number;
  rows_rejected: number;
  errors: { row_number: number; field: string; reason: string }[];
};

const ELECTIVE_GROUPS = ["general", "professional", "open"];
const VENUE_KINDS = ["classroom", "lab", "seminar", "auditorium", "workshop"];

const BLANK_SUBJECT = {
  code: "",
  name: "",
  department_id: "",
  kind: "core",
  elective_group: "",
  credits: "3",
  theory_hours: "3",
  lab_hours: "0",
};
const BLANK_VENUE = {
  code: "",
  name: "",
  capacity: "60",
  kind: "classroom",
  campus_code: "",
  building: "",
  room: "",
};

export default function SubjectsPage() {
  const [units, setUnits] = useState<Unit[]>([]);
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [venues, setVenues] = useState<Venue[]>([]);

  const [search, setSearch] = useState("");
  const [kindFilter, setKindFilter] = useState("");
  const [departmentFilter, setDepartmentFilter] = useState("");

  const [subjectForm, setSubjectForm] = useState(BLANK_SUBJECT);
  const [addingSubject, setAddingSubject] = useState(false);

  const [programmeId, setProgrammeId] = useState("");
  const [offerings, setOfferings] = useState<Offering[]>([]);
  const [offerForm, setOfferForm] = useState({ subject_id: "", position: "1", capacity: "" });

  const [venueForm, setVenueForm] = useState(BLANK_VENUE);
  const [addingVenue, setAddingVenue] = useState(false);

  const [importKind, setImportKind] = useState<"subjects" | "venues" | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);

  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const loadSubjects = useCallback(async () => {
    const params = new URLSearchParams({ limit: "2000" });
    if (search.trim()) params.set("search", search.trim());
    if (kindFilter) params.set("kind", kindFilter);
    if (departmentFilter) params.set("department_id", departmentFilter);
    setSubjects(await api<Subject[]>(`/org/subjects?${params}`));
  }, [search, kindFilter, departmentFilter]);

  const loadVenues = useCallback(async () => {
    setVenues(await api<Venue[]>("/org/venues?limit=2000"));
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        setUnits(await api<Unit[]>("/org/units?limit=2000"));
        await loadVenues();
      } catch (err) {
        setError(String((err as Error).message));
      }
    })();
  }, [loadVenues]);

  useEffect(() => {
    void loadSubjects().catch((err) => setError(String((err as Error).message)));
  }, [loadSubjects]);

  // ── derived ───────────────────────────────────────────────────────────────
  const departments = useMemo(
    () => units.filter((u) => u.type === "department").sort((a, b) => a.code.localeCompare(b.code)),
    [units],
  );
  const programmes = useMemo(
    () => units.filter((u) => u.type === "program").sort((a, b) => a.code.localeCompare(b.code)),
    [units],
  );
  const byId = useMemo(() => new Map(units.map((u) => [u.id, u])), [units]);
  const programme = useMemo(
    () => programmes.find((p) => p.id === programmeId) ?? null,
    [programmes, programmeId],
  );

  /** Positions the chosen Programme actually has — semester Programmes have two
   *  per year, so a 4-year B.Tech runs 1..8. */
  const ladder = useMemo(() => {
    if (!programme?.duration_years) return [];
    const school = (() => {
      let node: Unit | undefined = programme;
      while (node && node.type !== "school") node = node.parent_id ? byId.get(node.parent_id) : undefined;
      return node;
    })();
    const perYear = (school as unknown as { cadence?: string })?.cadence === "yearly" ? 1 : 2;
    return Array.from({ length: programme.duration_years * perYear }, (_, i) => i + 1);
  }, [programme, byId]);

  const loadOfferings = useCallback(async () => {
    if (!programmeId) {
      setOfferings([]);
      return;
    }
    setOfferings(await api<Offering[]>(`/org/programmes/${programmeId}/offerings`));
  }, [programmeId]);

  useEffect(() => {
    void loadOfferings().catch((err) => setError(String((err as Error).message)));
  }, [loadOfferings]);

  const offeringsByPosition = useMemo(() => {
    const map = new Map<string, Offering[]>();
    for (const o of offerings) {
      // A university-wide Open elective has no position — it belongs to every one.
      const key = o.position === null ? "open" : String(o.position);
      map.set(key, [...(map.get(key) ?? []), o]);
    }
    return map;
  }, [offerings]);

  // ── actions ───────────────────────────────────────────────────────────────
  function fail(err: unknown) {
    setError(String((err as Error).message));
  }

  async function createSubject(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api("/org/subjects", {
        method: "POST",
        body: {
          code: subjectForm.code,
          name: subjectForm.name,
          department_id: subjectForm.department_id,
          kind: subjectForm.kind,
          elective_group:
            subjectForm.kind === "elective" ? subjectForm.elective_group || null : null,
          credits: Number(subjectForm.credits),
          theory_hours: Number(subjectForm.theory_hours),
          lab_hours: Number(subjectForm.lab_hours),
        },
      });
      setNotice(`${subjectForm.code} added.`);
      setSubjectForm(BLANK_SUBJECT);
      setAddingSubject(false);
      await loadSubjects();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }

  async function createOffering(e: React.FormEvent) {
    e.preventDefault();
    const subject = subjects.find((s) => s.id === offerForm.subject_id);
    if (!subject) return;
    setBusy(true);
    setError("");
    try {
      // An Open elective is university-wide: offered once, with no Programme and
      // no position, so it reaches every student rather than needing a row each.
      const universityWide = subject.elective_group === "open";
      await api("/org/offerings", {
        method: "POST",
        body: {
          subject_id: subject.id,
          ...(universityWide
            ? {}
            : { program_id: programmeId, position: Number(offerForm.position) }),
          capacity: offerForm.capacity ? Number(offerForm.capacity) : null,
        },
      });
      setNotice(
        universityWide
          ? `${subject.code} offered university-wide.`
          : `${subject.code} offered at position ${offerForm.position}.`,
      );
      setOfferForm({ subject_id: "", position: offerForm.position, capacity: "" });
      await loadOfferings();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }

  async function setCapacity(offering: Offering, capacity: string) {
    setError("");
    try {
      await api(`/org/offerings/${offering.id}`, {
        method: "PUT",
        body: { capacity: capacity ? Number(capacity) : null },
      });
      await loadOfferings();
    } catch (err) {
      fail(err);
    }
  }

  async function createVenue(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/org/venues", {
        method: "POST",
        body: {
          ...venueForm,
          capacity: Number(venueForm.capacity),
          campus_code: venueForm.campus_code || null,
          building: venueForm.building || null,
          room: venueForm.room || null,
        },
      });
      setNotice(`${venueForm.code} added.`);
      setVenueForm(BLANK_VENUE);
      setAddingVenue(false);
      await loadVenues();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }

  async function runImport(e: React.FormEvent) {
    e.preventDefault();
    if (!file || !importKind) return;
    setBusy(true);
    setError("");
    setImportResult(null);
    try {
      const form = new FormData();
      form.append("file", file);
      setImportResult(await upload<ImportResult>(`/org/${importKind}/imports`, form));
      await (importKind === "subjects" ? loadSubjects() : loadVenues());
      await loadOfferings();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }

  // ── render ────────────────────────────────────────────────────────────────
  return (
    <div className="uc-screen" style={{ maxWidth: 1180 }}>
      <div>
        <h3 style={{ margin: 0 }}>Subjects &amp; venues</h3>
        <div className="uc-screen-sub">
          A subject is owned by the Department that teaches it, then <strong>offered</strong> to
          the Programmes that study it — so one subject serves many Programmes instead of being
          copied into each. The timetable can only place what has been offered.
        </div>
      </div>

      <div className="org-stats">
        <div className="org-stat">
          <div className="n">{subjects.length}</div>
          <div className="l">Subjects</div>
        </div>
        <div className="org-stat">
          <div className="n">{subjects.filter((s) => s.kind === "elective").length}</div>
          <div className="l">Electives</div>
        </div>
        <div className="org-stat">
          <div className="n">{venues.length}</div>
          <div className="l">Venues</div>
        </div>
      </div>

      {/* --- subjects ---------------------------------------------------- */}
      <div className="card">
        <div className="org-detail-head">
          <div className="card-kicker">Subject catalogue</div>
          <div style={{ flex: 1 }} />
          <button className="btn btn-secondary" onClick={() => setAddingSubject((v) => !v)}>
            {addingSubject ? "Cancel" : "+ Add subject"}
          </button>
          <button
            className="btn btn-secondary"
            onClick={() => {
              setImportKind(importKind === "subjects" ? null : "subjects");
              setImportResult(null);
            }}
          >
            {importKind === "subjects" ? "Close upload" : "Bulk upload"}
          </button>
        </div>

        <div style={{ display: "flex", gap: 12, alignItems: "flex-end", flexWrap: "wrap" }}>
          <div className="field" style={{ marginBottom: 0, maxWidth: 220 }}>
            <label>Find by code or name</label>
            <input
              className="input"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="e.g. MA101"
            />
          </div>
          <div className="field" style={{ marginBottom: 0, maxWidth: 200 }}>
            <label>Department</label>
            <select
              className="input"
              value={departmentFilter}
              onChange={(e) => setDepartmentFilter(e.target.value)}
            >
              <option value="">All</option>
              {departments.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.code} · {d.name}
                </option>
              ))}
            </select>
          </div>
          <div className="field" style={{ marginBottom: 0, maxWidth: 150 }}>
            <label>Kind</label>
            <select
              className="input"
              value={kindFilter}
              onChange={(e) => setKindFilter(e.target.value)}
            >
              <option value="">All</option>
              <option value="core">Core</option>
              <option value="elective">Elective</option>
            </select>
          </div>
        </div>

        {addingSubject && (
          <form onSubmit={createSubject} style={{ marginTop: 12 }}>
            <div className="org-form-grid">
              <div className="field">
                <label>Code</label>
                <input
                  className="input"
                  value={subjectForm.code}
                  required
                  autoFocus
                  onChange={(e) => setSubjectForm({ ...subjectForm, code: e.target.value })}
                />
              </div>
              <div className="field">
                <label>Name</label>
                <input
                  className="input"
                  value={subjectForm.name}
                  required
                  onChange={(e) => setSubjectForm({ ...subjectForm, name: e.target.value })}
                />
              </div>
              <div className="field">
                <label>Owning Department</label>
                <select
                  className="input"
                  value={subjectForm.department_id}
                  required
                  onChange={(e) =>
                    setSubjectForm({ ...subjectForm, department_id: e.target.value })
                  }
                >
                  <option value="">— select —</option>
                  {departments.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.code} · {d.name}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label>Kind</label>
                <select
                  className="input"
                  value={subjectForm.kind}
                  onChange={(e) =>
                    setSubjectForm({ ...subjectForm, kind: e.target.value, elective_group: "" })
                  }
                >
                  <option value="core">Core</option>
                  <option value="elective">Elective</option>
                </select>
              </div>
              {subjectForm.kind === "elective" && (
                <div className="field">
                  <label>Elective group</label>
                  <select
                    className="input"
                    value={subjectForm.elective_group}
                    required
                    onChange={(e) =>
                      setSubjectForm({ ...subjectForm, elective_group: e.target.value })
                    }
                  >
                    <option value="">— select —</option>
                    {ELECTIVE_GROUPS.map((g) => (
                      <option key={g} value={g}>
                        {g}
                        {g === "open" ? " (university-wide)" : ""}
                      </option>
                    ))}
                  </select>
                </div>
              )}
              {(["credits", "theory_hours", "lab_hours"] as const).map((key) => (
                <div className="field" key={key}>
                  <label>{key.replace("_", " ")}</label>
                  <input
                    className="input"
                    type="number"
                    min={0}
                    value={subjectForm[key]}
                    onChange={(e) => setSubjectForm({ ...subjectForm, [key]: e.target.value })}
                  />
                </div>
              ))}
            </div>
            <button className="btn btn-primary" type="submit" disabled={busy}>
              {busy ? "Adding…" : "Add subject"}
            </button>
            <p className="card-meta" style={{ marginTop: 6 }}>
              The Department is who <em>owns</em> the subject (Maths owns MA101), not who studies
              it — that comes next, when you offer it to a Programme.
            </p>
          </form>
        )}

        {importKind === "subjects" && (
          <form
            onSubmit={runImport}
            style={{ display: "flex", gap: 10, alignItems: "flex-end", margin: "10px 0" }}
          >
            <div className="field" style={{ marginBottom: 0, flex: 1, maxWidth: 320 }}>
              <label>CSV — one row per offering</label>
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
        )}

        <table className="table">
          <thead>
            <tr>
              <th>Code</th>
              <th>Name</th>
              <th>Department</th>
              <th>Kind</th>
              <th>Credits</th>
              <th>T / L hours</th>
            </tr>
          </thead>
          <tbody>
            {subjects.map((s) => (
              <tr key={s.id}>
                <td>
                  <strong>{s.code}</strong>
                </td>
                <td>{s.name}</td>
                <td>{byId.get(s.department_id)?.code ?? "—"}</td>
                <td>
                  {s.kind === "core" ? (
                    <span className="tag tag-neutral">core</span>
                  ) : (
                    <span
                      className="tag tag-outline"
                      title={
                        s.elective_group === "open"
                          ? "Open electives are offered university-wide"
                          : undefined
                      }
                    >
                      {s.elective_group}
                    </span>
                  )}
                </td>
                <td>{s.credits}</td>
                <td>
                  {s.theory_hours} / {s.lab_hours}
                </td>
              </tr>
            ))}
            {subjects.length === 0 && (
              <tr>
                <td colSpan={6} className="card-meta">
                  No subjects yet — add one above, or bulk upload the catalogue.
                </td>
              </tr>
            )}
          </tbody>
        </table>
        {notice && <p className="card-meta">{notice}</p>}
      </div>

      {/* --- offerings ---------------------------------------------------- */}
      <div className="card">
        <div className="org-detail-head">
          <div className="card-kicker">Curriculum · what each Programme studies</div>
          <div style={{ flex: 1 }} />
          <select
            className="input"
            style={{ width: 260 }}
            value={programmeId}
            onChange={(e) => setProgrammeId(e.target.value)}
          >
            <option value="">— select a Programme —</option>
            {programmes.map((p) => (
              <option key={p.id} value={p.id}>
                {p.code} · {p.name}
              </option>
            ))}
          </select>
        </div>

        {!programmeId ? (
          <p className="card-meta">
            Pick a Programme to see and edit its curriculum. Until a subject is offered here, the
            timetable cannot place it.
          </p>
        ) : (
          <>
            <form
              onSubmit={createOffering}
              style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap" }}
            >
              <div className="field" style={{ marginBottom: 0, minWidth: 240 }}>
                <label>Offer a subject</label>
                <select
                  className="input"
                  value={offerForm.subject_id}
                  required
                  onChange={(e) => setOfferForm({ ...offerForm, subject_id: e.target.value })}
                >
                  <option value="">— select —</option>
                  {subjects.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.code} · {s.name}
                      {s.elective_group ? ` (${s.elective_group})` : ""}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field" style={{ marginBottom: 0, width: 130 }}>
                <label>Position</label>
                <select
                  className="input"
                  value={offerForm.position}
                  onChange={(e) => setOfferForm({ ...offerForm, position: e.target.value })}
                  disabled={
                    subjects.find((s) => s.id === offerForm.subject_id)?.elective_group === "open"
                  }
                >
                  {(ladder.length ? ladder : [1, 2, 3, 4, 5, 6, 7, 8]).map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field" style={{ marginBottom: 0, width: 120 }}>
                <label>Seats (optional)</label>
                <input
                  className="input"
                  type="number"
                  min={1}
                  placeholder="∞"
                  value={offerForm.capacity}
                  onChange={(e) => setOfferForm({ ...offerForm, capacity: e.target.value })}
                />
              </div>
              <button className="btn btn-primary" type="submit" disabled={busy}>
                Offer
              </button>
            </form>
            {subjects.find((s) => s.id === offerForm.subject_id)?.elective_group === "open" && (
              <p className="card-meta">
                Open electives are <strong>university-wide</strong>: offered once, with no
                Programme and no position, so every student can choose it. Seats are one shared
                pool.
              </p>
            )}

            <table className="table" style={{ marginTop: 10 }}>
              <thead>
                <tr>
                  <th style={{ width: 130 }}>Position</th>
                  <th>Subjects</th>
                </tr>
              </thead>
              <tbody>
                {[
                  ...(ladder.length ? ladder.map(String) : []),
                  ...(offeringsByPosition.has("open") ? ["open"] : []),
                ].map((key) => {
                  const list = offeringsByPosition.get(key) ?? [];
                  return (
                    <tr key={key}>
                      <td>
                        {key === "open" ? (
                          <>
                            <strong>Open</strong>
                            <div className="card-meta">university-wide</div>
                          </>
                        ) : (
                          <strong>{key}</strong>
                        )}
                      </td>
                      <td>
                        {list.length === 0 ? (
                          <span className="org-type">nothing offered</span>
                        ) : (
                          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                            {list.map((o) => (
                              <div
                                key={o.id}
                                style={{ display: "flex", gap: 8, alignItems: "center" }}
                              >
                                <span className="tag tag-outline">{o.subject.code}</span>
                                <span>{o.subject.name}</span>
                                {o.subject.elective_group && (
                                  <span className="tag tag-neutral">
                                    {o.subject.elective_group}
                                  </span>
                                )}
                                {o.subject.kind === "elective" && (
                                  <span className="card-meta">
                                    seats{" "}
                                    <input
                                      className="input"
                                      style={{ width: 70, padding: "2px 6px" }}
                                      type="number"
                                      min={1}
                                      placeholder="∞"
                                      defaultValue={o.capacity ?? ""}
                                      onBlur={(e) => {
                                        if (e.target.value !== String(o.capacity ?? ""))
                                          void setCapacity(o, e.target.value);
                                      }}
                                    />
                                    {o.capacity !== null && ` · ${o.seats_taken} taken`}
                                  </span>
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {ladder.length === 0 && (
              <p className="card-meta">
                This Programme has no duration set, so its ladder is unknown — set it on Org
                structure and the positions will appear.
              </p>
            )}
          </>
        )}
      </div>

      {/* --- venues -------------------------------------------------------- */}
      <div className="card">
        <div className="org-detail-head">
          <div className="card-kicker">Venues</div>
          <div style={{ flex: 1 }} />
          <button className="btn btn-secondary" onClick={() => setAddingVenue((v) => !v)}>
            {addingVenue ? "Cancel" : "+ Add venue"}
          </button>
          <button
            className="btn btn-secondary"
            onClick={() => {
              setImportKind(importKind === "venues" ? null : "venues");
              setImportResult(null);
            }}
          >
            {importKind === "venues" ? "Close upload" : "Bulk upload"}
          </button>
        </div>
        <p className="card-meta">
          Rooms are university-wide, not owned by a School: clash detection spans the whole
          university, so a room cannot host two classes even if the two Schools never speak.
        </p>

        {addingVenue && (
          <form onSubmit={createVenue}>
            <div className="org-form-grid">
              {(
                [
                  ["code", "Code", true],
                  ["name", "Name", true],
                  ["building", "Building", false],
                  ["room", "Room", false],
                  ["campus_code", "Campus", false],
                ] as [keyof typeof BLANK_VENUE, string, boolean][]
              ).map(([key, labelText, required]) => (
                <div className="field" key={key}>
                  <label>{labelText}</label>
                  <input
                    className="input"
                    value={venueForm[key]}
                    required={required}
                    onChange={(e) => setVenueForm({ ...venueForm, [key]: e.target.value })}
                  />
                </div>
              ))}
              <div className="field">
                <label>Kind</label>
                <select
                  className="input"
                  value={venueForm.kind}
                  onChange={(e) => setVenueForm({ ...venueForm, kind: e.target.value })}
                >
                  {VENUE_KINDS.map((k) => (
                    <option key={k}>{k}</option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label>Capacity</label>
                <input
                  className="input"
                  type="number"
                  min={1}
                  value={venueForm.capacity}
                  required
                  onChange={(e) => setVenueForm({ ...venueForm, capacity: e.target.value })}
                />
              </div>
            </div>
            <button className="btn btn-primary" type="submit" disabled={busy}>
              {busy ? "Adding…" : "Add venue"}
            </button>
          </form>
        )}

        {importKind === "venues" && (
          <form
            onSubmit={runImport}
            style={{ display: "flex", gap: 10, alignItems: "flex-end", margin: "10px 0" }}
          >
            <div className="field" style={{ marginBottom: 0, flex: 1, maxWidth: 320 }}>
              <label>Venues CSV</label>
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
        )}

        <table className="table">
          <thead>
            <tr>
              <th>Code</th>
              <th>Name</th>
              <th>Kind</th>
              <th>Seats</th>
              <th>Where</th>
            </tr>
          </thead>
          <tbody>
            {venues.map((v) => (
              <tr key={v.id}>
                <td>
                  <strong>{v.code}</strong>
                </td>
                <td>{v.name}</td>
                <td>
                  <span className="tag tag-neutral">{v.kind}</span>
                </td>
                <td>{v.capacity}</td>
                <td className="card-meta">
                  {[v.campus_code, v.building, v.room].filter(Boolean).join(" · ") || "—"}
                </td>
              </tr>
            ))}
            {venues.length === 0 && (
              <tr>
                <td colSpan={5} className="card-meta">
                  No venues yet — the timetable needs rooms to put classes in.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {importResult && (
        <div className="card">
          <div className="card-kicker">Import result</div>
          <p className="card-meta">
            {importResult.subjects_created !== undefined
              ? `${importResult.subjects_created} subjects · ${importResult.offerings_created} offerings created`
              : `${importResult.rows_created ?? 0} created · ${importResult.rows_updated ?? 0} updated`}{" "}
            · {importResult.rows_rejected} rejected
          </p>
          {importResult.errors.length > 0 && (
            <table className="table">
              <thead>
                <tr>
                  <th>Row</th>
                  <th>Field</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {importResult.errors.map((e, i) => (
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
      )}

      {importKind && <TemplateLinks only={[importKind]} />}
      {error && <p className="error">{error}</p>}
    </div>
  );
}
