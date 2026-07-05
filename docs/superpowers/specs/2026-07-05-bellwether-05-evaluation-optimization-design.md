# bellwether Plan 5 — Evaluation & Optimization — Design Spec

**Date:** 2026-07-05
**Status:** Draft — awaiting review
**Author:** Prabhagharan
**Parent spec:** `2026-07-04-bellwether-design.md` (§6 optimization flywheel, §7 firewalled tracks, §12 data model, §13 API, §17 testing)
**Builds on:** Plans 1–4 (esp. Plan 3's DSPy Detect/Extract modules + `build_*()` factories, the `version` column on `detections`/`extractions`)

---

## 1. Goal

Build the **optimization flywheel** that improves Detect/Extract over time, and the **firewall** that keeps semantic accuracy separate from market impact.

The loop: a human **reviews and corrects** pipeline output → **golden labels** (train/held-out split) → **evaluate** a program on the held-out set (Track A) → **optimize** a new program version with **GEPA** on the train set → **promote** it only if it beats the current **champion** on the frozen held-out set → the pipeline's `build_*()` factories **load the champion**, so the next run uses the better prompt.

**Firewall (§7):** Track A (human golden labels) — and only Track A — drives optimization. Track B (measured market impact from Plan 4) is reported separately and **never** feeds Track A; a test enforces it.

**In scope:** golden-label tables, review-and-correct API, Track-A metrics (with GEPA feedback), held-out evaluation, GEPA optimize + champion/challenger + versioned program store + rollback, the champion-loading seam into Plan 3's factories, and the firewall (separation + test + a minimal Track-B aggregation placeholder).

**Out of scope (later plans):** the rich Track-B per-figure impact **leaderboard** API/UI (Plan 7 — the dashboard); LLM-as-judge bulk labeling; optimizing the Resolve/Discovery modules (a future symmetric application of the same machinery).

## 2. Hard invariants

- **The firewall.** Track-A scoring depends **only** on `(statement text, model output, human gold label)`. It never reads `impacts`/`resolutions`. Concretely: a Track-A score is **invariant to market data** (removing/altering impacts cannot change it), and the eval module does not import `Impact`/`Resolution`. Tested (§9).
- **Honest held-out.** The optimizer (GEPA) sees **only the train split**. The **held-out split is frozen** and used solely for champion/challenger comparison — never given to GEPA. A statement is entirely train **or** held-out (no per-field leakage).
- **Anti-fabrication preserved.** A human-corrected gold `evidence_quote` must still be a **verbatim substring** of the statement (reuse `is_verbatim`) — even gold can't be fabricated.
- **DSPy-only, provider-agnostic.** GEPA is a DSPy optimizer; the reflection model is config-selected (`reflection_model`), credential from env. Consistent with Plans 3–4.
- **Instant rollback.** Every compiled program is a stored artifact; the champion is a flag, so promotion/rollback is a DB flip, never a recompile.
- **Shared corpus.** `relevance_labels`, `extraction_labels`, `eval_runs`, `dspy_programs` carry **no `owner_id`** (golden truth / program artifacts are global). Review endpoints are still authenticated; the review *queue* is owner-scoped by figure ownership.
- **Tests: real Postgres, no live network.** Metrics are pure; eval/review use stubs/DummyLM; the champion/challenger promotion logic is tested with a **fake optimizer** (a real GEPA `compile` is LLM-intensive and run manually/live, like the market adapter in Plan 4).

## 3. Data model

Four tables, all shared corpus (no `owner_id`).

### 3.1 `relevance_labels`
| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `statement_id` | FK→statements.id (CASCADE, indexed) | |
| `is_relevant` | bool | human truth |
| `source` | str | `review` (LLM-judge later) |
| `split` | str | `train` / `holdout` |
| `created_at` | tz datetime | |

Unique `(statement_id)` — one relevance label per statement (a re-review updates it).

### 3.2 `extraction_labels`
| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `statement_id` | FK→statements.id (CASCADE, indexed) | |
| `entities` | JSONB (list) | gold |
| `direction` | str | gold `up`/`down`/`neutral` |
| `magnitude` | str | gold `none`/`small`/`moderate`/`large` |
| `evidence_quote` | text | gold (verbatim-guarded) |
| `source` | str | `review` |
| `split` | str | `train` / `holdout` |
| `created_at` | tz datetime | |

Unique `(statement_id)`. Only written when the statement is relevant.

### 3.3 `eval_runs`
| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `module` | str | `detect` / `extract` |
| `dspy_program_id` | FK→dspy_programs.id, nullable | which program was scored (null = baseline) |
| `split` | str | usually `holdout` |
| `metric` | str | e.g. `accuracy`, `extract_avg` |
| `score` | float | |
| `n` | int | # labeled statements scored |
| `created_at` | tz datetime | |

### 3.4 `dspy_programs`
| Column | Type | Notes |
|---|---|---|
| `id` | PK | |
| `module` | str | `detect` / `extract` (indexed) |
| `version` | int | monotonic per module (1, 2, …) |
| `artifact` | JSONB | the compiled program (`program.dump_state()`) |
| `holdout_score` | float, nullable | its frozen held-out score |
| `is_champion` | bool | at most one true per module |
| `created_at` | tz datetime | |

`version="baseline"` is represented by the **absence** of any champion row for the module (the factory falls back to the un-compiled module and stamps `"baseline"`).

## 4. Split assignment

Deterministic and immutable: `split_for(statement_id) = "holdout" if statement_id % holdout_modulus == 0 else "train"` (§11, default `holdout_modulus=5` ≈ 20% held-out). Both label types for a statement inherit the same split (statement-level), so a statement is wholly train or wholly held-out. Assigned once at label creation.

## 5. Review-and-correct (authenticated API)

- **`GET /review/queue?module={detect|extract}&limit=`** — statements that have been processed by the pipeline but not yet labeled for that module (for `extract`: status `extracted`/`resolved` with no `extraction_label`; for `detect`: any `detected`/`irrelevant`/… with no `relevance_label`). **Owner-scoped** to the caller's figures. Returns the statement text, the model's current detection/extraction, and figure name (so the reviewer can judge).
- **`POST /review/{statement_id}`** — body:
  - `{ "is_relevant": false }` → writes a negative `relevance_label`; no extraction label.
  - `{ "is_relevant": true, "extraction": { "direction", "magnitude", "entities", "evidence_quote" } }` → writes a positive `relevance_label` **and** an `extraction_label` with those gold fields.
  - Omitting `extraction` while `is_relevant: true` = **confirm**: the statement's current model `Extraction` is copied verbatim as the gold label.
  - The gold `evidence_quote` is **verbatim-guarded** (`is_verbatim` against the statement) → `422` if not a substring.
  - Split assigned per §4. A repeat review upserts the labels.
- Auth required (`get_current_user`); the queue and submit verify the statement's figure is owned by the caller.

## 6. Track-A metrics (with GEPA feedback)

A metric module (`eval/metrics.py`) — **pure**, no DB, no market data. Each returns a **score** and a **feedback string** (GEPA consumes feedback; the plain score is used by Evaluate).

- **Detect** — `score_detection(pred_is_relevant, gold_is_relevant) -> (float, str)`: `1.0` iff they match, else `0.0`, with feedback naming the mismatch. Aggregate accuracy (and report F1) across the labeled set.
- **Extract** — `score_extraction(pred, gold, statement_text) -> (float, str)`, the four-part average:
  1. `direction` exact match → 0/1
  2. `magnitude` exact match → 0/1
  3. `evidence_quote` is a verbatim substring of `statement_text` → 0/1
  4. `entities` set-F1 vs gold (case-insensitive) → 0…1
  
  **score = mean(1,2,3,4)**; feedback lists the failing parts (e.g. *"direction wrong: pred up, gold down; entities missed {TSLA}"*). This is the definition of "a good extraction"; weights are a tunable choice.

A thin **DSPy adapter** wraps these into GEPA's metric signature (`metric(gold, pred, trace=None, pred_name=None, pred_trace=None) -> dspy.Prediction(score=…, feedback=…)`).

## 7. Evaluation & optimization

### 7.1 Evaluate (Track A)
`evaluate(module, program|None, split) -> EvalResult(score, n)`: load the labeled statements for `split`, run the program (or the un-compiled module) on each statement's text, score with §6, average. Writes an `eval_runs` row. Uses only labels + model output — the firewall boundary.

### 7.2 Build the trainset
DSPy `Example`s from the **train** labels: Detect → `Example(statement_text, is_relevant).with_inputs("statement_text")`; Extract → `Example(statement_text, entities, direction, magnitude, evidence_quote).with_inputs("statement_text")`.

### 7.3 Optimize with GEPA + champion/challenger
`optimize(module) -> OptimizeResult`:
1. Load the **train** trainset. Current = the champion program (or the baseline module).
2. `compiled = dspy.GEPA(metric=<feedback metric>, reflection_lm=make_lm(settings.reflection_model), auto="light").compile(student=current, trainset=train, valset=train)`. GEPA sees only train; **held-out is untouched.**
3. Persist `compiled.dump_state()` as a new `dspy_programs` row (next `version`, `is_champion=false`, `holdout_score=null`).
4. **Evaluate the challenger on the frozen held-out split** → `holdout_score` (store it). Get the champion's held-out score (stored, or evaluate the baseline).
5. **Promote iff** `challenger.holdout_score > champion.holdout_score`: set the challenger `is_champion=true`, demote the old. Otherwise store unpromoted. Record `eval_runs` for both.
6. Return `{challenger_version, challenger_holdout, champion_holdout, promoted}`.

`optimize` is exposed as an API endpoint and an internal function (heavy; also runnable as a CLI). Real GEPA runs live; the **promotion decision** (`promote_if_better`) is a pure function, unit-tested.

## 8. The champion-loading seam (modifies Plan 3)

The payoff of the frozen `build_*()` factories:
- `build_detector()` / `build_extractor()` gain an optional program source. At worker startup they **load the current champion** `dspy_programs.artifact` for the module into the DSPy module (`program.load_state(artifact)`), and the adapter exposes `.version` (the champion's version, or `"baseline"`).
- The detect/extract **stages stamp `version = detector.version` / `extractor.version`** on the `detections`/`extractions` rows (replacing the hardcoded `"baseline"`), so each row records the prompt version that produced it.
- Loading is a small DB read at build time; the factory takes an optional `session`/loader so tests inject a program or force baseline. No stage/queue/worker-loop changes beyond the `version` field source.

## 9. Firewall (§7)

- **Structure:** Track-A code (`eval/`) imports only `Statement`, `RelevanceLabel`, `ExtractionLabel`, the DSPy modules, and `is_verbatim`. Track-B lives separately (`trackb/report.py`) — a **minimal** aggregation (e.g. average `pct_move` per figure over `impacts`) standing in for the Plan-7 leaderboard.
- **Tests:**
  1. **Invariance:** compute a Track-A score for a labeled set; add/alter/remove `impacts` rows; recompute — the score is **unchanged**.
  2. **Import boundary:** assert the `eval` package's modules do not reference `Impact`/`Resolution` (module-attribute / source check).

## 10. API surface (additions)

All authenticated. Owner-scope the review queue by figure ownership; programs/eval/optimize are global (single-tenant).
- Review: `GET /review/queue`, `POST /review/{statement_id}` (§5).
- Optimize: `POST /optimize/{module}` → runs §7.3, returns the result.
- Programs: `GET /programs?module=` (version history: version, holdout_score, is_champion, created_at); `POST /programs/{id}/promote` (set champion / rollback).
- Eval: `GET /eval_runs?module=` (scoreboard history).

## 11. Configuration (env)

Add to `Settings`: `reflection_model: str = "anthropic/claude-sonnet-5"` (GEPA reflection LM), `gepa_auto: str = "light"` (GEPA budget preset), `holdout_modulus: int = 5` (~20% held-out). Task models unchanged (`detect_model`/`extract_model`).

## 12. File structure (informs the plan)

```
src/bellwether/
├── models/            # relevance_label.py, extraction_label.py, eval_run.py, dspy_program.py
├── labels.py          # split_for(); label upsert helpers
├── eval/
│   ├── metrics.py     # score_detection, score_extraction (pure, + feedback)
│   ├── gepa_metric.py # DSPy/GEPA metric adapter
│   ├── evaluate.py    # evaluate(module, program, split) -> EvalResult
│   └── optimize.py    # build_trainset, optimize(module), promote_if_better
├── trackb/report.py   # minimal impact aggregation (firewall-separated)
├── programs.py        # champion load/save; dspy_programs CRUD
├── api/review.py, api/optimize.py, api/programs.py
├── config.py          # (modify) reflection_model, gepa_auto, holdout_modulus
└── llm/{detect,extract}.py  # (modify) build_* loads champion; adapter.version
```

## 13. Testing

- **Metrics** — pure: `score_extraction` component-by-component (direction/magnitude/evidence/entity-F1) + the average + the feedback text; `score_detection`.
- **Split** — `split_for` determinism + ~20% ratio.
- **Review API** — real Postgres: queue lists unlabeled owned statements; confirm copies the model extraction to gold; correct writes edited gold; reject writes a negative relevance label; non-verbatim gold quote → 422; owner-scoping.
- **Evaluate** — DummyLM/stub module over labeled statements → expected score; writes an `eval_run`.
- **Optimize / champion-challenger** — `promote_if_better` pure logic (promote iff strictly better); `optimize` orchestration with a **fake optimizer** (returns a canned program) + stub eval → correct promotion + `dspy_programs`/`eval_runs` rows. (Real GEPA `compile` is manual/live.)
- **Champion-loading seam** — `build_detector()` loads a stored champion artifact and stamps its version; falls back to baseline when none.
- **Firewall** — the invariance test + the import-boundary test (§9).

## 14. Deferred with intent

- Track-B per-figure impact **leaderboard** API/UI — Plan 7.
- **LLM-as-judge** bulk labeling (validated against the human golden anchor).
- Optimizing **Resolve/Discovery** with the same machinery.
- MIPROv2 or a larger GEPA budget once the golden set grows (swappable behind `optimize`).
- Automatic/scheduled optimize (v1 is a manual trigger).
