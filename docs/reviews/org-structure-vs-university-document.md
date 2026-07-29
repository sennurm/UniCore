# Review — Takshashila University structure document vs UniCore requirements

Reviewed: 28-07-2026 · Source: `Takshashila_University_Schools_and_Programmes_Updated.docx`
(“Updated as per the official website — July 2026”)
Compared against: [00-overview.md](../../requirements/00-overview.md),
[01-authentication-authorization-security.md](../../requirements/01-authentication-authorization-security.md),
and `requirements/sources/module_access_matrix.xlsx` (25-07-2026).

## 1. Verdict on the hierarchy

The document **confirms our five-level academic hierarchy**:

```
University → Faculty (Stream) → School → Department → Programme
```

with per-term Sections below Programme. No structural rework is needed — but the
document contradicts two things we locked earlier and reveals one modelling gap
that would block importing this university's real structure.

## 2. Contradictions with what we locked

### 2.1 Pro-Chancellor exists — and there are **two** ⚠️ blocking

The document's Chancellor's Office lists:

| Name | Role |
|---|---|
| Shri. Rajarajan Dhanasekaran | **Pro-Chancellor** |
| Dr. Nila Priyadharshini Dhanasekaran | **Pro-Chancellor** |
| Prof. (Dr.) Vivek Inder Kochhar | Vice Chancellor |
| Prof. (Dr.) S. Senthil | Registrar |

We **removed the Pro-Chancellor role entirely on 25-07-2026** because the access
matrix had no such row, and rewired the leave chain to VC/Registrar → Chancellor
(AUTH-FR-18, LVE routing map, migration 0003 `reporting_edges`, LVE-FR-16).

Two further problems beyond mere existence:
- **Two simultaneous holders** breaks the singleton assumption we apply to every
  University-level role (`chancellor`, `vc`, `registrar`, `dean-academic-affairs`).
- **No Chancellor is named at all** in the document, yet our chain terminates at
  the Chancellor and LVE treats it as the terminal approver.

### 2.2 “Principal” is a School-level title, not a campus head ⚠️

The document uses Principal for the **School of Pharmacy** (“Principal: Dr. S.
Sathya … *equal to school incharge*”) and the **School of Nursing** (“Principal –
kasturi *(equal to school incharge)*”).

Our AUTH-FR-18 chain and TSK assigner set carry **Principal/Director → VC**,
modelled as a campus head — with an open question asking whether the role exists
at all. It does exist, but it is **a School Incharge by another name**, not a
campus tier. Left as-is, a Pharmacy Principal would route leave to the VC instead
of their Faculty Dean.

### 2.3 Faculty count: body says 7, summary says 6

Seven Faculty headings appear in the body (Engineering & Technology, Management &
Commerce, Sciences, Agricultural Sciences, Humanities & Social Sciences,
**Medicine**, Health Sciences), but the summary states “Total Faculties: 6” and
omits Medicine from its own list. Our access-matrix-derived set of seven
(FET, FMC, FHSS, FSC, FMS, FHS, Agri) matches the **body**, so the summary line is
the error — worth confirming.

## 3. Open questions this document CLOSES ✅

| Previously open | Now answered |
|---|---|
| “Dean FSC — Faculty of Science *(confirm)*” | **Faculty of Sciences** — confirmed |
| Agri Division mislabelled “Faculty of Health Sciences Agri” in the matrix | **Faculty of Agricultural Sciences** — corrected |
| Do campus Principals/Directors exist? | Yes, but as **School-level heads** (see 2.2) — not a campus tier |
| Faculty Dean additional charge | Confirmed in the wild: FMC has **no Dean** and is covered by FET's Dean Suphalakshmi; FHS is covered by Medicine's Dean Jayasri. Our additional-charge model (AUTH-FR-17) handles both |
| School Incharge additional charge | Dr. P. Murugamani heads **both** School of Social Sciences and School of Humanities |

## 4. Modelling gap: Departments are optional in practice ⚠️ blocking

Our model requires **Programme → Department → School**. The document shows only
**2 of 14 Schools** actually have named Departments:

| School | Departments |
|---|---|
| School of Computational Engineering (SCOPE) | 3 — CSE; AI & Data Science; AI & Machine Learning |
| School of Core Engineering (SCORE) | 1 — Electronics & Communication Engineering |
| **The other 12 Schools** | **none — programmes are listed directly under the School** |

Those 12 Schools each name a single “Head of Department”, “School In-charge”,
“Head of School” or “Principal” — one person for the whole School, with no
departmental subdivision. Forcing a Department level would mean inventing 12
phantom units that exist nowhere in the university's own documentation, and would
put an HoD grant on a unit that has no real-world counterpart.

## 5. Programme attributes we do not model

We store `level`, `duration_years`, `mode`. The document carries three more
dimensions that appear throughout:

1. **Programme category** — every School groups its list into *Standard*,
   *Industry Collaborated*, *Industry Integrated*, and *Research* programmes.
2. **Industry partner** — IBM, XEBIA, HCL GUVI, NOVAC, AWS, NVIDIA, APPLE,
   TRANSORG, CHAINLEARN, NXTWAVE, Schneider Electric, IMARTICUS, FACEPREP.
   Roughly 25 of the 70+ programmes name a partner in their title.
3. **Specialisations / streams within one programme** —
   “M.Tech CSE (specialisations: AI, Big Data, IoT & Cloud Computing)”,
   “MBA (dual specialisation; 16 areas)”,
   “M.MLS (streams: Medical Biochemistry; Medical Microbiology; Hematology &
   Transfusion Medicine; Histology & Cytology)”.

Item 3 is the significant one: a specialisation is plausibly what a **Section**
or an elective group represents, or it may need its own level. Until decided,
importers will flatten “M.Tech CSE (AI)” into a programme name string.

## 6. Duration nuances our integer cannot express

- “4 years **incl. 1-year internship**” (most Allied Health programmes)
- “5 years incl. 1-year internship” (B.Optom.)
- “BPT — 4 years academic **+ 1 year internship**”
- “M.Tech AI & DS with IBM (**5-Year Integrated**)”
- “B.Pharm — **lateral entry to 3rd semester** for D.Pharm holders”

Internship periods matter to attendance and promotion (a student on internship is
not in timetabled Sessions), and lateral entry matters to ONB (a student joins at
semester 3, not 1). Neither is expressible today.

## 7. Scope question: School of Medicine

Entry 10 is a single line — “Takshashila Medical College
(www.takshashilamedicalcollege.com)” — with no programmes, HoD, or School
In-charge. It appears to be a separately-run institution. Whether UniCore manages
it at all (students, attendance, timetables) needs an explicit decision; today it
would import as an empty School.

## 8. Decisions taken (28-07-2026) and applied

| Finding | Decision | Where implemented |
|---|---|---|
| 12 of 14 Schools have no Departments | **Auto-create a default Department per School.** Blank department columns synthesise one mirroring the School, flagged `auto_created` so the org table shows it as a structural placeholder rather than a real academic unit. A code without a name (or vice versa) is rejected as a mistake | migration 0006, `org.service._import_catalogue_row`, AUTH-FR-19 |
| Pro-Chancellor exists, two holders | **Role restored, deliberately NOT singleton** — VC / Registrar → Pro-Chancellor → Chancellor. Chancellor stays terminal even though the post is currently unnamed, so the chain never dead-ends (the reporting API reports it `vacant`) | migration 0006, `UNIVERSITY_SINGLETON_ROLES`, AUTH-FR-18, LVE routing map |
| "Principal" is a School-level title | **Treated as a display alias for a School Incharge grant**; removed from the reporting chain as a campus tier, so a Pharmacy Principal routes to their Faculty Dean | AUTH-FR-18, LVE §4 rule 1, overview §4 |
| Programme category and industry partner | **Stored** as Programme attributes, imported and editable | migration 0006, flat template |
| Internship and lateral entry | **Stored** — `internship_months` on top of academic `duration_years`, and `lateral_entry_semester` | migration 0006, flat template |
| Specialisations / streams | **Not modelled** — deferred; they remain part of the programme name. Revisit when students need grouping by specialisation (touches TTM electives, PRM) | open |
| Faculty count 7 (body) vs 6 (summary) | Body wins — our seven Divisions match it. Summary line treated as a document error | open question, AUTH §11 |
| School of Medicine (external college) | Unresolved — imports today as an empty School | open question, AUTH §11 |

## 9. What needs no change

- Five-level hierarchy — **confirmed**
- Exactly one University — consistent
- Faculty Dean / School Incharge / HoD role tiers — confirmed, including
  additional charge at both Faculty and School level
- Deactivate-never-delete, per-term Sections, scoped grants — unaffected
- The seven Faculty Divisions already seeded from the access matrix — names now
  corrected and confirmed
