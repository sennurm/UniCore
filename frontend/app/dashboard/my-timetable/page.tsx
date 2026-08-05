"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";

type Row = {
  day_of_week: number;
  period_name: string;
  start_time: string;
  end_time: string;
  subject_code: string;
  subject_name: string;
  elective_group: string | null;
  section_name: string;
  faculty_name: string;
  venue_code: string;
};
type Personal = {
  role: string;
  section_name: string | null;
  rows: Row[];
  note: string | null;
};
type Term = { term_code: string; status: string };

const DAYS = [
  { n: 1, label: "Monday" },
  { n: 2, label: "Tuesday" },
  { n: 3, label: "Wednesday" },
  { n: 4, label: "Thursday" },
  { n: 5, label: "Friday" },
  { n: 6, label: "Saturday" },
];

function hhmm(value: string): string {
  return value.slice(0, 5);
}

export default function MyTimetablePage() {
  const [terms, setTerms] = useState<string[]>([]);
  const [termCode, setTermCode] = useState("");
  const [personal, setPersonal] = useState<Personal | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    void (async () => {
      try {
        const all = await api<Term[]>("/timetable/terms");
        const approved = Array.from(
          new Set(all.filter((t) => t.status === "approved").map((t) => t.term_code)),
        ).sort((a, b) => b.localeCompare(a));
        setTerms(approved);
        setTermCode(approved[0] ?? "");
      } catch {
        // A student has no permission to read the term list; fall back to a
        // sensible default rather than showing them an error they cannot act on.
        const year = new Date().getFullYear();
        setTerms([`${year}-S1`]);
        setTermCode(`${year}-S1`);
      }
    })();
  }, []);

  const load = useCallback(async () => {
    if (!termCode) return;
    setLoading(true);
    setError("");
    try {
      setPersonal(
        await api<Personal>(`/timetable/me?term_code=${encodeURIComponent(termCode)}`),
      );
    } catch (err) {
      setPersonal(null);
      setError(String((err as Error).message));
    } finally {
      setLoading(false);
    }
  }, [termCode]);

  useEffect(() => {
    void load();
  }, [load]);

  /** Periods in clock order, taken from the rows — the viewer need not know
   *  which grid their School runs. */
  const periods = useMemo(() => {
    const seen = new Map<string, { name: string; start: string; end: string }>();
    for (const row of personal?.rows ?? []) {
      if (!seen.has(row.period_name)) {
        seen.set(row.period_name, {
          name: row.period_name,
          start: row.start_time,
          end: row.end_time,
        });
      }
    }
    return Array.from(seen.values()).sort((a, b) => a.start.localeCompare(b.start));
  }, [personal]);

  const cells = useMemo(() => {
    const map = new Map<string, Row>();
    for (const row of personal?.rows ?? []) map.set(`${row.day_of_week}:${row.period_name}`, row);
    return map;
  }, [personal]);

  const isFaculty = personal?.role === "faculty";

  return (
    <div className="uc-screen" style={{ maxWidth: 1060 }}>
      <div>
        <h3 style={{ margin: 0 }}>My timetable</h3>
        <div className="uc-screen-sub">
          {isFaculty
            ? "Your teaching load for the term, across every School you teach in."
            : "Your published week. Electives show the one you chose, not the alternatives."}
        </div>
      </div>

      <div style={{ display: "flex", gap: 12, alignItems: "flex-end", flexWrap: "wrap" }}>
        <div className="field" style={{ marginBottom: 0, width: 160 }}>
          <label>Term</label>
          <select
            className="input"
            value={termCode}
            onChange={(e) => setTermCode(e.target.value)}
          >
            {terms.map((code) => (
              <option key={code}>{code}</option>
            ))}
          </select>
        </div>
        {personal && (
          <p className="card-meta">
            <span className="tag tag-outline">{personal.role}</span>
            {personal.section_name && (
              <>
                {" "}
                Section <strong>{personal.section_name}</strong>
              </>
            )}
            {personal.rows.length > 0 && ` · ${personal.rows.length} classes a week`}
          </p>
        )}
      </div>

      <div className="card">
        {loading ? (
          <p className="card-meta">Loading…</p>
        ) : personal?.note ? (
          <div className="org-empty">{personal.note}</div>
        ) : periods.length === 0 ? (
          <div className="org-empty">
            Nothing published for {termCode} yet.
            <br />
            {isFaculty
              ? "Classes appear here once the Timetable Cell publishes them."
              : "Your timetable appears once your School publishes it."}
          </div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 130 }}>Period</th>
                {DAYS.map((d) => (
                  <th key={d.n}>{d.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {periods.map((period) => (
                <tr key={period.name}>
                  <td>
                    <strong>{period.name}</strong>
                    <div className="card-meta">
                      {hhmm(period.start)}–{hhmm(period.end)}
                    </div>
                  </td>
                  {DAYS.map((day) => {
                    const cell = cells.get(`${day.n}:${period.name}`);
                    return (
                      <td key={day.n} style={{ verticalAlign: "top" }}>
                        {cell ? (
                          <>
                            <strong>{cell.subject_code}</strong>
                            {cell.elective_group && (
                              <span className="tag tag-neutral" style={{ marginLeft: 6 }}>
                                {cell.elective_group}
                              </span>
                            )}
                            <div className="card-meta">{cell.subject_name}</div>
                            <div className="card-meta">
                              {isFaculty ? cell.section_name : cell.faculty_name} ·{" "}
                              {cell.venue_code}
                            </div>
                          </>
                        ) : (
                          <span className="org-type">—</span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {!isFaculty && personal && personal.rows.length > 0 && (
        <p className="card-meta">
          A gap where an elective should be usually means you have not chosen one yet — pick it on
          the elective screen and it will appear here.
        </p>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  );
}
