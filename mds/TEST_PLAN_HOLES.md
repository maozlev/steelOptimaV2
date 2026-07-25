# Hole-detection testing — what was built, and what is left

Supersedes the first draft of this plan. Written after building it, so the ordering
reflects what actually paid off rather than what looked good on paper.

Companion: `mds/SYNTHETIC_DRAWINGS_PLAN.md` (the 505-case corpus — mostly NOT built, and
the reason why is below).

---

## What exists now

| File | What it does |
|---|---|
| `tools/eval_detection.py` | The harness, refactored into a **library**. `reported_cutouts()` / `score_drawing()` are now importable; `main()` just prints. One definition of "what the pipeline found". |
| `tests/test_detection_accuracy.py` | The **gate**. Scores all 9 real drawings against an `expect` block in ground truth, in both directions (a drawing that gets BETTER fails too, demanding the number be raised). Plus forbidden-region and positional checks. ~30 s. |
| `tests/test_thresholds.py` | **54 probes** at the pipeline's decision points, one constant each. ~1 s. |
| `tests/test_ink_crossing.py` | Backlog item 2 as a test: a large hole must survive being crossed; a bolt-hole-sized one currently does not (strict xfail). |
| `tools/export_fixture.py` | Turns a reviewed document into a ground-truth entry. **The bottleneck was never algorithms — it was labelled drawings, and the app has been making them all along and throwing them away.** |
| `tools/eval_ink_crossing.py` | Generates the A5.png failure with the cause on a dial: crossing angle × offset × ink width × hole size × DPI. |

`ground_truth.json` gained `expect` blocks on all 9 drawings, and **441 exact hole centres**
on A (3) and A (4), exported from documents Maoz had already reviewed. In the first draft of
this plan that was item 9, costed at ~3 hours of manual labelling. It took one command.

---

## The ordering lesson

The first draft ranked the synthetic corpus last and the probes seventh. **Both were wrong,
in the same direction: I over-valued volume and under-valued aim.**

A twenty-line probe of ONE decision — how `ink.py` classifies stroke colour — found a bug
that loses **every hole on the page** in ten minutes. Nine real drawings had never caught it
because none of them happens to use a coloured layer, and a 505-case corpus would have found
it on about day four.

The rule that came out of it: **probe decision points to FIND bugs; keep corpora to stop
fixed bugs coming back.** They are different jobs and the first is far cheaper per bug.

---

## What is left, in order

1. **Re-run and re-review Doc_HK3573** (details in the summary below). Its finalized state
   is missing two real bolt holes. Five minutes, and it is a wrong part today.
2. **Export fixtures as you review.** `uv run python tools/export_fixture.py --all --merge`
   after each review session. This is now the cheapest way to grow the corpus, and item 1 of
   the standing backlog ("~20 real drawings") becomes a by-product of ordinary work.
3. **Ink-crossed hole recovery** (backlog 2). The dataset now exists; `test_ink_crossing.py`
   goes red when it lands.
4. **Diameter from circumradius, not area** — see the summary. A one-directional bias in the
   number that gets quoted and cut.
5. **Split-contour gap closing** — `test_thresholds.py` documents it as a strict xfail.
6. **Forbidden regions for the other 7 drawings.** Only rejected cutouts from reviews produce
   them automatically, and there are none yet (the only reviewed document with rejections was
   stale). Drawing 2–4 rectangles per sheet by eye would make precision an asserted property
   rather than an inferred one.

---

## What was NOT built, and why

The 505-case synthetic corpus in `SYNTHETIC_DRAWINGS_PLAN.md`. One slice of it was built —
the ink-crossing generator — because that failure class had three examples in the entire repo
and could not be fixed from three examples.

The rest stayed on paper because the probes covered the same decisions at a fraction of the
cost, and because a corpus I author only ever encodes conventions I already thought of. The
coloured-layer bug is the proof: it was invisible to nine real drawings AND would have been
invisible to any corpus I designed before finding it.

Build the corpus when there is a specific failure class to characterise, the way the
ink-crossing generator was built. Not before.
