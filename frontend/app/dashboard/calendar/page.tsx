"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";

type Unit = { id: string; type: string; name: string; code: string; campus_code: string | null };
type Holiday = {
  id: string;
  from_date: string;
  to_date: string;
  label: string;
  kind: string;
  campus_codes: string[];
  status: string;
};
type Pattern = { days: Record<string, boolean | number[]>; is_default: boolean };
type Exception = {
  id: string;
  on_date: string;
  working: boolean;
  follows_day_of_week: number | null;
  reason: string;
};
type ResolvedDay = {
  on_date: string;
  teaching: boolean;
  effective_day_of_week: number | null;
  decided_by: string;
  detail: string;
};

const DAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const FULL_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const HOLIDAY_KINDS = ["public", "vacation", "local"];

/** How each layer of the calendar reads to someone asking why a date is closed. */
const DECIDED_BY: Record<string, { label: string; tone: string }> = {
  "school-exception": { label: "School exception", tone: "tag-outline" },
  "school-override": { label: "Worked through holiday", tone: "tag-outline" },
  "university-holiday": { label: "University holiday", tone: "tag-neutral" },
  "school-pattern": { label: "School week", tone: "tag-neutral" },
  "school-pattern-default": { label: "Default week", tone: "tag-neutral" },
};

function iso(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function ddmmyyyy(value: string): string {
  const [y, m, d] = value.split("-");
  return `${d}-${m}-${y}`;
}

const BLANK_HOLIDAY = {
  from_date: "",
  to_date: "",
  label: "",
  kind: "public",
  campus_codes: "",
};
const BLANK_EXCEPTION = {
  on_date: "",
  working: "true",
  follows_day_of_week: "",
  reason: "",
};

export default function CalendarPage() {
  const [units, setUnits] = useState<Unit[]>([]);
  const [schoolId, setSchoolId] = useState("");

  const [holidays, setHolidays] = useState<Holiday[]>([]);
  const [holidayForm, setHolidayForm] = useState(BLANK_HOLIDAY);

  const [pattern, setPattern] = useState<Pattern | null>(null);
  const [draftDays, setDraftDays] = useState<Record<string, boolean | number[]>>({});
  /** The occurrence boxes are controlled off their own text state. An
   *  uncontrolled input keyed only by weekday keeps the previous School's text
   *  when you switch Schools — and blurring it would then write that School's
   *  rule onto this one. */
  const [occText, setOccText] = useState<Record<string, string>>({});

  const [exceptions, setExceptions] = useState<Exception[]>([]);
  const [exceptionForm, setExceptionForm] = useState(BLANK_EXCEPTION);

  const [month, setMonth] = useState(() => iso(new Date()).slice(0, 7));
  const [resolved, setResolved] = useState<ResolvedDay[]>([]);

  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");

  const schools = useMemo(
    () => units.filter((u) => u.type === "school").sort((a, b) => a.code.localeCompare(b.code)),
    [units],
  );
  const school = useMemo(() => schools.find((s) => s.id === schoolId) ?? null, [schools, schoolId]);

  function fail(err: unknown) {
    setError(String((err as Error).message));
  }

  const loadHolidays = useCallback(async () => {
    setHolidays(await api<Holiday[]>("/timetable/holidays?limit=500"));
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        setUnits(await api<Unit[]>("/org/units?limit=2000"));
        await loadHolidays();
      } catch (err) {
        fail(err);
      }
    })();
  }, [loadHolidays]);

  const loadSchool = useCallback(async () => {
    if (!schoolId) {
      setPattern(null);
      setExceptions([]);
      setResolved([]);
      return;
    }
    const [first, last] = monthBounds(month);
    const [p, e, r] = await Promise.all([
      api<Pattern>(`/timetable/schools/${schoolId}/working-pattern`),
      api<Exception[]>(
        `/timetable/schools/${schoolId}/exceptions?from_date=${first}&to_date=${last}`,
      ),
      api<ResolvedDay[]>(
        `/timetable/schools/${schoolId}/calendar?from_date=${first}&to_date=${last}`,
      ),
    ]);
    setPattern(p);
    setDraftDays(p.days);
    setOccText(
      Object.fromEntries(
        Object.entries(p.days).map(([day, rule]) => [
          day,
          Array.isArray(rule) ? rule.join(", ") : "",
        ]),
      ),
    );
    setExceptions(e);
    setResolved(r);
  }, [schoolId, month]);

  useEffect(() => {
    void loadSchool().catch(fail);
  }, [loadSchool]);

  // ── actions ───────────────────────────────────────────────────────────────
  async function createHoliday(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api("/timetable/holidays", {
        method: "POST",
        body: {
          ...holidayForm,
          // One entry covers a whole vacation block; a single day is a one-day range.
          to_date: holidayForm.to_date || holidayForm.from_date,
          campus_codes: holidayForm.campus_codes
            .split(",")
            .map((c) => c.trim())
            .filter(Boolean),
        },
      });
      setNotice(`${holidayForm.label} added.`);
      setHolidayForm(BLANK_HOLIDAY);
      await loadHolidays();
      await loadSchool();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }

  async function withdrawHoliday(holiday: Holiday) {
    setError("");
    try {
      await api(`/timetable/holidays/${holiday.id}/withdraw`, { method: "POST" });
      await loadHolidays();
      await loadSchool();
    } catch (err) {
      fail(err);
    }
  }

  async function savePattern(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api(`/timetable/schools/${schoolId}/working-pattern`, {
        method: "PUT",
        body: { days: draftDays },
      });
      setNotice(`${school?.name} working days saved.`);
      await loadSchool();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }

  async function addException(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await api(`/timetable/schools/${schoolId}/exceptions`, {
        method: "POST",
        body: {
          on_date: exceptionForm.on_date,
          working: exceptionForm.working === "true",
          follows_day_of_week: exceptionForm.follows_day_of_week
            ? Number(exceptionForm.follows_day_of_week)
            : null,
          reason: exceptionForm.reason,
        },
      });
      setNotice(`${ddmmyyyy(exceptionForm.on_date)} recorded.`);
      setExceptionForm(BLANK_EXCEPTION);
      await loadSchool();
    } catch (err) {
      fail(err);
    } finally {
      setBusy(false);
    }
  }

  async function removeException(item: Exception) {
    setError("");
    try {
      await api(`/timetable/schools/${schoolId}/exceptions/${item.on_date}`, {
        method: "DELETE",
      });
      await loadSchool();
    } catch (err) {
      fail(err);
    }
  }

  function toggleDay(n: number) {
    const key = String(n);
    setDraftDays((days) => {
      const next = { ...days };
      const current = days[key];
      if (current === true || Array.isArray(current)) delete next[key];
      else next[key] = true;
      return next;
    });
    setOccText((text) => ({ ...text, [key]: "" }));
  }

  /** Parsed on blur rather than on every keystroke, so typing "1, 3" is not
   *  fought halfway through. Unparseable text means "every occurrence". */
  function commitOccurrences(n: number) {
    const key = String(n);
    const list = (occText[key] ?? "")
      .split(",")
      .map((v) => Number(v.trim()))
      .filter((v) => v >= 1 && v <= 5);
    setDraftDays((days) => ({ ...days, [key]: list.length ? list : true }));
    setOccText((text) => ({ ...text, [key]: list.join(", ") }));
  }

  // ── render ────────────────────────────────────────────────────────────────
  return (
    <div className="uc-screen" style={{ maxWidth: 1180 }}>
      <div>
        <h3 style={{ margin: 0 }}>Calendar</h3>
        <div className="uc-screen-sub">
          Two layers decide whether a date is a teaching day. The university closes for holidays;
          each School declares which weekdays it teaches — so Nursing can run clinical postings on
          Sunday while Engineering next door is shut. Timetabling, attendance, leave balances and
          recurring tasks all read the same answer.
        </div>
      </div>

      {/* --- university holidays ------------------------------------------- */}
      <div className="card">
        <div className="card-kicker">University holidays</div>
        <p className="card-meta">
          A holiday is a <strong>date range</strong>, so a two-week break is one entry rather than
          fourteen. Leave the campuses blank to close the whole university; name them for a
          regional festival that only one campus observes.
        </p>

        <form
          onSubmit={createHoliday}
          style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap" }}
        >
          <div className="field" style={{ marginBottom: 0, width: 150 }}>
            <label>From</label>
            <input
              className="input"
              type="date"
              value={holidayForm.from_date}
              required
              onChange={(e) => setHolidayForm({ ...holidayForm, from_date: e.target.value })}
            />
          </div>
          <div className="field" style={{ marginBottom: 0, width: 150 }}>
            <label>To (blank = same day)</label>
            <input
              className="input"
              type="date"
              value={holidayForm.to_date}
              onChange={(e) => setHolidayForm({ ...holidayForm, to_date: e.target.value })}
            />
          </div>
          <div className="field" style={{ marginBottom: 0, flex: 1, minWidth: 200 }}>
            <label>Label</label>
            <input
              className="input"
              value={holidayForm.label}
              required
              placeholder="e.g. Pongal"
              onChange={(e) => setHolidayForm({ ...holidayForm, label: e.target.value })}
            />
          </div>
          <div className="field" style={{ marginBottom: 0, width: 130 }}>
            <label>Kind</label>
            <select
              className="input"
              value={holidayForm.kind}
              onChange={(e) => setHolidayForm({ ...holidayForm, kind: e.target.value })}
            >
              {HOLIDAY_KINDS.map((k) => (
                <option key={k}>{k}</option>
              ))}
            </select>
          </div>
          <div className="field" style={{ marginBottom: 0, width: 170 }}>
            <label>Campuses (optional)</label>
            <input
              className="input"
              value={holidayForm.campus_codes}
              placeholder="all campuses"
              onChange={(e) => setHolidayForm({ ...holidayForm, campus_codes: e.target.value })}
            />
          </div>
          <button className="btn btn-primary" type="submit" disabled={busy}>
            Add
          </button>
        </form>

        <table className="table" style={{ marginTop: 12 }}>
          <thead>
            <tr>
              <th>Dates</th>
              <th>Label</th>
              <th>Kind</th>
              <th>Where</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {holidays.map((h) => (
              <tr key={h.id} style={h.status === "withdrawn" ? { opacity: 0.45 } : undefined}>
                <td>
                  {ddmmyyyy(h.from_date)}
                  {h.to_date !== h.from_date && ` → ${ddmmyyyy(h.to_date)}`}
                </td>
                <td>
                  <strong>{h.label}</strong>
                </td>
                <td>
                  <span className="tag tag-neutral">{h.kind}</span>
                </td>
                <td className="card-meta">
                  {h.campus_codes.length ? h.campus_codes.join(", ") : "university-wide"}
                </td>
                <td>
                  {h.status === "active" ? (
                    <button className="btn btn-secondary" onClick={() => void withdrawHoliday(h)}>
                      Withdraw
                    </button>
                  ) : (
                    <span className="card-meta">withdrawn</span>
                  )}
                </td>
              </tr>
            ))}
            {holidays.length === 0 && (
              <tr>
                <td colSpan={5} className="card-meta">
                  No holidays yet — every date is a working day until one is added.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* --- school working days -------------------------------------------- */}
      <div className="card">
        <div className="org-detail-head">
          <div className="card-kicker">School working days</div>
          <div style={{ flex: 1 }} />
          <select
            className="input"
            style={{ width: 260 }}
            value={schoolId}
            onChange={(e) => setSchoolId(e.target.value)}
          >
            <option value="">— select a School —</option>
            {schools.map((s) => (
              <option key={s.id} value={s.id}>
                {s.code} · {s.name}
              </option>
            ))}
          </select>
        </div>

        {!schoolId ? (
          <p className="card-meta">
            Pick a School to see and change the week it teaches. Until then, every School runs the
            university default of Monday–Saturday.
          </p>
        ) : (
          <>
            {pattern?.is_default && (
              <p className="card-meta">
                This School has never declared its week, so the university default (Monday–Saturday)
                is in force. Saving below makes it explicit.
              </p>
            )}
            <form onSubmit={savePattern}>
              <table className="table" style={{ marginTop: 6 }}>
                <thead>
                  <tr>
                    <th style={{ width: 60 }}>Teach</th>
                    <th style={{ width: 120 }}>Day</th>
                    <th>Which occurrences</th>
                  </tr>
                </thead>
                <tbody>
                  {DAY_LABELS.map((label, i) => {
                    const n = i + 1;
                    const value = draftDays[String(n)];
                    const teaches = value === true || Array.isArray(value);
                    return (
                      <tr key={n}>
                        <td>
                          <input
                            type="checkbox"
                            checked={teaches}
                            onChange={() => toggleDay(n)}
                          />
                        </td>
                        <td>{FULL_DAYS[i]}</td>
                        <td>
                          {teaches ? (
                            <input
                              className="input"
                              style={{ maxWidth: 220, padding: "2px 6px" }}
                              value={occText[String(n)] ?? ""}
                              placeholder="every one — or 1, 3 for alternate"
                              onChange={(e) =>
                                setOccText({ ...occText, [String(n)]: e.target.value })
                              }
                              onBlur={() => commitOccurrences(n)}
                            />
                          ) : (
                            <span className="card-meta">not taught</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <button className="btn btn-primary" type="submit" disabled={busy}>
                {busy ? "Saving…" : "Save working days"}
              </button>
              <p className="card-meta" style={{ marginTop: 6 }}>
                Leave the occurrences blank for every week; enter <code>1, 3</code> on Saturday for
                a School that works alternate Saturdays. Turning a day off is refused while a
                published timetable still teaches on it.
              </p>
            </form>

            {/* --- exceptions ------------------------------------------------- */}
            <div className="org-detail-head" style={{ marginTop: 18 }}>
              <div className="card-kicker">Dated exceptions</div>
            </div>
            <p className="card-meta">
              A one-off closure, or a day worked anyway — a ward that does not close for a public
              holiday. A working day may <strong>follow another weekday</strong>, which is how a
              made-up Saturday runs Monday&rsquo;s timetable.
            </p>
            <form
              onSubmit={addException}
              style={{ display: "flex", gap: 10, alignItems: "flex-end", flexWrap: "wrap" }}
            >
              <div className="field" style={{ marginBottom: 0, width: 150 }}>
                <label>Date</label>
                <input
                  className="input"
                  type="date"
                  value={exceptionForm.on_date}
                  required
                  onChange={(e) => setExceptionForm({ ...exceptionForm, on_date: e.target.value })}
                />
              </div>
              <div className="field" style={{ marginBottom: 0, width: 150 }}>
                <label>This date is</label>
                <select
                  className="input"
                  value={exceptionForm.working}
                  onChange={(e) =>
                    setExceptionForm({
                      ...exceptionForm,
                      working: e.target.value,
                      follows_day_of_week: "",
                    })
                  }
                >
                  <option value="true">working</option>
                  <option value="false">closed</option>
                </select>
              </div>
              {exceptionForm.working === "true" && (
                <div className="field" style={{ marginBottom: 0, width: 170 }}>
                  <label>Follows (optional)</label>
                  <select
                    className="input"
                    value={exceptionForm.follows_day_of_week}
                    onChange={(e) =>
                      setExceptionForm({ ...exceptionForm, follows_day_of_week: e.target.value })
                    }
                  >
                    <option value="">its own weekday</option>
                    {FULL_DAYS.map((d, i) => (
                      <option key={d} value={i + 1}>
                        {d}&rsquo;s timetable
                      </option>
                    ))}
                  </select>
                </div>
              )}
              <div className="field" style={{ marginBottom: 0, flex: 1, minWidth: 200 }}>
                <label>Reason</label>
                <input
                  className="input"
                  value={exceptionForm.reason}
                  required
                  onChange={(e) => setExceptionForm({ ...exceptionForm, reason: e.target.value })}
                />
              </div>
              <button className="btn btn-primary" type="submit" disabled={busy}>
                Record
              </button>
            </form>

            {exceptions.length > 0 && (
              <table className="table" style={{ marginTop: 10 }}>
                <tbody>
                  {exceptions.map((x) => (
                    <tr key={x.id}>
                      <td style={{ width: 120 }}>{ddmmyyyy(x.on_date)}</td>
                      <td style={{ width: 110 }}>
                        <span className={x.working ? "tag tag-outline" : "tag tag-neutral"}>
                          {x.working ? "working" : "closed"}
                        </span>
                      </td>
                      <td style={{ width: 160 }} className="card-meta">
                        {x.follows_day_of_week
                          ? `runs ${FULL_DAYS[x.follows_day_of_week - 1]}'s timetable`
                          : ""}
                      </td>
                      <td>{x.reason}</td>
                      <td style={{ width: 90 }}>
                        <button
                          className="btn btn-secondary"
                          onClick={() => void removeException(x)}
                        >
                          Remove
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            {/* --- resolved month --------------------------------------------- */}
            <div className="org-detail-head" style={{ marginTop: 18 }}>
              <div className="card-kicker">Resolved month</div>
              <div style={{ flex: 1 }} />
              <input
                className="input"
                style={{ width: 170 }}
                type="month"
                value={month}
                onChange={(e) => setMonth(e.target.value)}
              />
            </div>
            <p className="card-meta">
              What this School actually teaches, date by date, and which rule decided — the answer
              a student gets when they ask why they have class on a Sunday.
            </p>
            <div className="cal-grid">
              {resolved.map((d) => {
                const badge = DECIDED_BY[d.decided_by] ?? { label: d.decided_by, tone: "tag-neutral" };
                const weekday = new Date(`${d.on_date}T00:00:00`).getDay();
                return (
                  <div
                    key={d.on_date}
                    className="cal-cell"
                    title={`${badge.label} — ${d.detail}`}
                    style={{
                      opacity: d.teaching ? 1 : 0.42,
                      borderStyle: d.decided_by.startsWith("school-") &&
                        d.decided_by !== "school-pattern" &&
                        d.decided_by !== "school-pattern-default"
                        ? "dashed"
                        : "solid",
                    }}
                  >
                    <div className="cal-date">
                      {d.on_date.slice(8)} <span className="card-meta">{DAY_LABELS[(weekday + 6) % 7]}</span>
                    </div>
                    <div className="card-meta">{d.teaching ? "teaching" : "closed"}</div>
                    {d.effective_day_of_week !== null &&
                      d.effective_day_of_week !== ((weekday + 6) % 7) + 1 && (
                        <span className="tag tag-outline">
                          {DAY_LABELS[d.effective_day_of_week - 1]}
                        </span>
                      )}
                  </div>
                );
              })}
            </div>
          </>
        )}
        {notice && <p className="card-meta">{notice}</p>}
      </div>

      {error && <p className="error">{error}</p>}
    </div>
  );
}

function monthBounds(month: string): [string, string] {
  const [y, m] = month.split("-").map(Number);
  const last = new Date(Date.UTC(y, m, 0)).getUTCDate();
  return [`${month}-01`, `${month}-${String(last).padStart(2, "0")}`];
}
