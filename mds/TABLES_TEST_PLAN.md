# Tables pipeline — accuracy test plan

Goal: be able to say "the tables pipeline is 100% accurate" and have the sentence
mean something. Right now it does not.

Everything below the "Measured today" heading was produced by running the real code
over `tables/*.pdf` on 2026-07-25 — no estimates.

## CORRECTION (2026-07-25, later the same day)

**The "silent drop bug" below was wrong about what it costs, and Maoz called it.**
He said: find the material table by keywords, and maybe there is no table in the
file. He was right about the second part. Two measurements settled it:

1. **No text layer.** `page.get_text()` returns **0 characters on nine of the ten
   833.1 sheets** (40 words on the tenth, none of it Hebrew). All Hebrew is stroke
   ink. Keyword matching therefore has nothing to read on those sheets, and never
   will without an OCR model that reads Hebrew — this is not a tuning problem.
2. **There is no BOM on those sheets to find.** Reading every cell of the two
   ground-truth tables on 833.1-01-20:
   - the 7×8 is a **concrete mix specification** — exposure class S5/S8, cement
     content 400/350, w/c ratio 0.45, `CEM-I 52.5N`
   - the 12×7 is a **pile setting-out schedule** — level, diameter, depth, count,
     **NORTHING 176487.97 / EASTING 654411.85**, marks P1…P29

   The remaining candidate grids on the other nine sheets OCR to empty cells and
   line fragments: they are drawing geometry the ruling-line detector mistook for
   grids, not tables at all.

So the gate rejecting them is **correct behaviour**, the `expected_kind:
"materials"` labels I put in the fixture were **wrong ground truth** (exactly the
failure CLAUDE.md warns about, and Maoz caught it again), and the ten Hebrew
sheets are not a recall problem at all — **they are the best precision fixture in
the repo.** A gate that gets greedy chasing a table that isn't there turns survey
northings into a cutting list, and nothing downstream would look wrong.

What survives from the original finding: `readable` still means "the OCR returned
characters", not "we understood them", so unreadable Hebrew ink and a genuine
English coordinate header reach the same verdict by the same route. Today both
answers are right. It is a blind spot, not a live loss — pinned as a fact in
`test_table_gate.py::test_garbage_and_real_text_are_indistinguishable_to_the_gate`
rather than asserted as a bug. It only bites the day a Hebrew sheet does carry a
BOM, and no amount of keyword work closes it.

## Status (2026-07-25)

Two lanes, because a suite nobody runs protects nothing:

```bash
cd server
uv run pytest -m "not slow" tests/test_table_*.py   # 88 tests, 1.9 s — pre-commit
uv run pytest tests/test_table_*.py                 # + 51 slow, ~9 min — pre-push
```

| File | Layer | Lane | What it pins |
|---|---|---|---|
| `tests/test_table_classify.py` | L1 | fast | header→role mapping, header position, marker words |
| `tests/test_table_gate.py` | L0 | fast | process-or-drop, on OCR strings captured from the sheets |
| `tests/test_table_recall.py` | L0+L1 | slow | real sheets: BOMs survive, non-BOMs never classify materials |
| `tests/test_table_absence.py` | L0 | slow | **"no material table here" is a correct, explicit answer** |
| `tests/test_table_decisions.py` | L3 | slow | **no wrong row may auto-approve** — deliberate corruptions |
| `tests/test_table_reconciliation.py` | L4 | slow | NCD = printed 3814.4 kg; pooling; no double count |
| `tests/bom_factory.py` | — | — | shared generator: BOMs whose right answer is known by construction |

Supporting changes: the gate was extracted from `service.py` into
`classify.gate_decision()` and the header OCR into `cells.header_candidates()`, so
the tests exercise the shipped path instead of a copy. `tools/eval_tables.py` now
matches grids by bbox IoU instead of row/col-count similarity.

Mutation-checked: forcing `row_status` to always approve turns 3 decision tests
red; making the material key document-local turns the pooling test red.

Only the table modules carry the `slow` marker — the drawings/extraction tests
belong to another workstream and were left alone.

Still to do: L2 cell fixtures for the 11 unlabelled sheets (needs Maoz — though
see the correction above: the 833.1 sheets have no BOM cells to label), the
by-script accuracy breakout, and the L5 golden.

---

## 1. Measured today

`uv run python tools/eval_tables.py` → **210/210 cells, 0 wrong-and-unflagged.**

What those 210 cells are:

| | |
|---|---|
| PDFs in `tables/` | 12 |
| PDFs with cell-level ground truth | **1** (`NCD5168[_EN](5)`) |
| Tables with cell-level ground truth | **1** (the 30×7 BOM) |
| Cells = 30 rows × 7 cols | 210 |
| Languages covered | English only |
| `833.1-01-20.json` | bbox + row/col counts for 2 tables, **no `cells`** — the eval skips it |
| Other 10 Hebrew sheets | no ground truth at all |

So "100%" = one table, one sheet, one language, one drafting convention, one job
mode (VLM off). It is a real result — it just answers a much smaller question than
the one being asked.

### The bigger problem the probe found

I replayed `_process_table`'s classification gate over every grid in every sheet
(deterministic half only, no DB, no VLM). 73 grids across 12 PDFs:

| Outcome | Grids |
|---|---|
| **Silently rejected** (`kind="other"`, `status="rejected"`, no VLM call) | **54** |
| Escalated to VLM classify | 14 |
| Processed as materials | **5 — all on NCD5168** |

Both BOM tables on `833.1-01-20` — the two that already have geometry ground truth,
`piles` 12×7 and `concrete-components` 7×8 — land in the *silently rejected* bucket.

Cause: the gate at `app/tables/service.py:224`.

```python
read_ok  = [c for c in inked if (c.value or "").strip()]
readable = bool(inked) and len(read_ok) / len(inked) >= 0.5
```

`readable` means "OCR returned some characters", not "the text was understood".
RapidOCR reads Hebrew header ink as Latin garbage — `'117V O9n I JU'`, `'NTUNJ X'`
— which is non-empty, so `readable=True`. No marker word matches garbage, so the
grid is declared `other` and rejected **without ever earning the VLM call that was
specifically designed for unreadable Hebrew headers**. The one branch that would
have saved it (`elif readable or not inked` → the `else`: VLM) is unreachable for
exactly the case it exists for.

It gets worse downstream: `aggregate.py:109-122` counts `pending_tables` and
`flagged_rows` only for `kind == "materials"`. A rejected table is neither. So the
project summary reports **zero material rows and zero unreviewed items** for all ten
Hebrew sheets — it looks complete and correct while contributing nothing.

That is the failure mode this test plan has to be able to catch, and no current test
or metric can. Cell accuracy is the wrong altitude: **table recall is ~9% and cell
accuracy is 100%, at the same time.**

---

## 2. The six layers

Each layer gets its own number. A single headline "accuracy" figure hides exactly
the failure above.

| L | Question | Metric | Target | Today |
|---|---|---|---|---|
| L0 | Did we find every material table? | table recall / precision | recall 100%, no silent drops | **unmeasured (~9% by probe)** |
| L1 | Did we label the columns right? | role accuracy per table | 100% on labelled sheets | unmeasured |
| L2 | Did we read every cell right? | cell accuracy, wrong-unflagged | 100% / 0 | 210/210 on 1 table |
| L3 | Did we approve/flag the right rows? | 2×2 confusion matrix | 0 wrong-and-auto-approved | unmeasured |
| L4 | Does the project summary add up? | per-sheet weight vs printed total | within printed tolerance | unmeasured |
| L5 | Do bid + optimizer consume it correctly? | golden end-to-end numbers | exact | unit-tested only |

L3 is the one that costs money. L0 is the one that is currently broken.

---

### L0 — Table recall (build first)

**Fixture.** Extend the existing per-sheet JSON in `tests/fixtures/tables/` with a
`tables[]` entry per *real* material table: `name`, `bbox`, `rows`, `cols`, plus a
new `expected_kind` of `materials` | `other`. Every sheet gets a file, including
sheets whose answer is "no material table here" — those are the precision cases.

**Harness.** New `tools/eval_table_recall.py`:

- run `detect_grids` + the full classification gate (VLM off, then VLM on)
- match detected grids to ground-truth bboxes by IoU > 0.5
- report: recall (GT materials tables found and classified `materials`),
  precision (grids called `materials` that aren't), and — separately —
  **silent drops**: GT materials tables that ended `rejected`

**Pytest guard.** `tests/test_table_recall.py`, parametrized per sheet, VLM off:
every ground-truth materials table must end up either processed as `materials` or
in a state the operator can see. `assert silent_drops == 0` is the single most
valuable assertion missing from this repo.

**Note.** L0 will fail on day one — that is the point. The gate fix (`readable`
should require *meaningful* text: a minimum ratio of dictionary-ish/marker tokens,
or simply "if the sheet is Hebrew-inked and OCR produced no marker, escalate")
belongs after the metric exists, not before.

---

### L1 — Column-role accuracy

Add `column_roles` to every ground-truth materials table (already present for NCD).
Metric: exact role-list match per table, and per-role accuracy across tables.
Run in both modes — VLM-off measures `classify_heuristic`, VLM-on measures the
model. Report separately; the Hebrew sheets will only ever be VLM-on.

Failure here is silent and expensive: one column mis-labelled `unit_length` vs
`total_length` makes every row's arithmetic check pass against the wrong number.

---

### L2 — Cell accuracy (extend the existing harness)

The harness is fine; the fixture set is one table. Grow it to **every material table
on all 12 sheets** — by the probe's counts that is roughly 12–20 tables and on the
order of 1,500–2,500 cells, dominated by Hebrew text columns the current 210 cells
contain none of.

Changes to `tools/eval_tables.py`:

- stop guessing the grid by `argmax(-|Δrows| - |Δcols|)` (`eval_tables.py:44`) —
  match on the ground-truth **bbox IoU** instead. Row/col-count matching will pick
  the wrong grid the moment two tables on a sheet have similar shapes, and then
  report a false 0%.
- break the numbers out by **script** (Latin digits / Latin text / Hebrew text) —
  the current single figure would let a Hebrew-column collapse hide behind the
  numeric columns.
- keep the wrong-flagged vs wrong-unflagged split. It's the right idea.

---

### L3 — Row decision accuracy (the money metric)

Ground truth per row: the correct values, from which the harness derives whether the
row *should* auto-approve. Then a 2×2:

| | should approve | should flag |
|---|---|---|
| **auto_approved** | ✅ | ❌ **money** |
| **needs_review** | 🟡 a click | ✅ |

Targets: bottom-left cell = 0, hard. Top-right (needless review) reported and
tracked, not gated — the flagging philosophy already accepts clicks.

This layer is what actually validates the checksum design (qty×unit=total, weight
column vs printed grand total). Right now that design is asserted by hand-written
unit tests in `test_table_validate.py` with synthetic numbers, and by one real
table.

Run in both VLM modes. **The VLM-on number is non-deterministic — report it as a
range over 3 runs, and never gate CI on it.** CI gates on the VLM-off number only.

---

### L4 — Sheet and project reconciliation

The cheapest strong signal in the whole pipeline, and it needs almost no labelling:

- per sheet: sum of approved rows' `total_weight_kg` vs the sheet's **printed grand
  total** (`read_declared_total_weight`). Already computed as
  `validation.weight_total_matches` — nothing asserts it per sheet.
- per project: ingest all 10 `833.1-*` sheets into one project, approve everything,
  and assert `project_summary` totals equal the sum of the per-sheet printed totals.
- assert the summary's `unreviewed` block is honest: with the L0 bug present, this
  test fails loudly instead of reporting a clean zero.

Only sheets that print a grand total qualify — record which do while labelling.

---

### L5 — Downstream golden

`test_pricing_orders.py` and `test_optimizer.py` cover the math on synthetic input.
What's missing is one **golden end-to-end**: NCD5168 (+ one Hebrew sheet once L0 is
fixed) → job → approve → summary → bid → order plan, asserting the final bar count,
kg, and price against numbers Maoz confirms once. That is the artefact a customer
sees; nothing currently pins it.

---

## 3. Making the labelling cheap

Labelling is the bottleneck (same as the drawings pipeline — "the limiting factor is
labelled drawings, not algorithms"). Don't type ground truth from scratch:

Build `tools/label_tables.py` that, per sheet:

1. runs `detect_grids`, writes a numbered overlay PNG of the sheet with every grid
   boxed and indexed, plus `tables/<sheet>-g<N>.png` crops at review DPI
2. emits a ground-truth **skeleton** JSON pre-filled with the pipeline's own reads —
   bbox, rows, cols, guessed roles, and the OCR'd cell matrix
3. Maoz opens the crop next to the JSON and **corrects** rather than transcribes;
   he marks each grid `materials` / `other` and deletes the rest

Pre-filling with the pipeline's output biases toward agreement — mitigate by
diffing: the harness reports how many cells Maoz changed. A sheet where he changed
nothing is a sheet to re-check by eye. `tables/*-table.png` already exists for some
sheets and covers step 1 partially.

Rough cost: ~15 min/sheet × 12 sheets for a first pass. That is the whole budget for
turning a one-table claim into a twelve-sheet one.

---

## 4. Order of work

1. **L0 fixture + harness + `silent_drops == 0` test.** Fails immediately, exposes
   the gate bug, biggest information gain per hour.
2. **Fix the `readable` gate** so unreadable-ink grids escalate instead of being
   rejected. Measure with L0 before and after.
3. **`label_tables.py`**, then Maoz labels all 12 sheets (L0 + L1 first — bboxes,
   kinds and roles only; cells after).
4. **L1 + L4.** Roles and reconciliation need only the light labelling, and L4 is
   nearly free.
5. **L2 cell labelling** for the Hebrew tables, plus the IoU-matching and
   by-script-breakout fixes to `eval_tables.py`.
6. **L3** confusion matrix on top of the L2 fixtures.
7. **L5** golden, once the numbers upstream are trusted.

One command at the end:

```bash
cd server && uv run python tools/eval_tables_all.py        # L0-L4, VLM off, CI-gating
cd server && uv run python tools/eval_tables_all.py --vlm  # same, VLM on, reported not gated
```

---

## 5. What may not be claimed until this exists

- "the tables pipeline is accurate" — it is accurate *on one English table*
- "the pipeline handles Hebrew sheets" — measured today: it drops all of them
- any project- or bid-level total derived from more than the NCD sheet

And the standing rule from the drawings side applies unchanged here: a wrong value
that is flagged costs a click; a table that is silently dropped costs the whole
sheet, and nothing in the product currently says it happened.
