---
name: universal-reviewer
version: "20.0.0"
description: Agent-driven review pipeline with explicit OpenAlex acquisition, stage guards, and workspace-scoped outputs.
---

# UniversalReviewer (v21.0)

UniversalReviewer is a workspace-scoped literature review pipeline for end-to-end review generation. It is not an open-ended writing assistant. The default behavior must follow the staged SOP below and must stay aligned with the real script interfaces.

## Core Rules

The pipeline must begin with deterministic OpenAlex acquisition.

- Do not skip `step0b_queries.py`.
- Do not skip `step1_acquire.py`.
- Do not start screening, writing, or freeform literature collection before OpenAlex candidates exist in the database.
- If acquisition is blocked by network or dependencies, report the blocker explicitly and request approval when needed. Do not replace it with manual writing.
- The initial OpenAlex pass is mandatory, but it is not always sufficient for narrow intersection topics.
- For pairwise or mechanism-focused topics such as `A + B in disease X`, the pipeline must run a corpus adequacy check after the first acquisition round and may generate a second targeted OpenAlex bundle for bridge and mechanism papers.

## Workspace Rule

All generated project files must stay inside:

`workspaces/<project_slug>/`

Expected directories:

- `db/`
- `data/query_bundles/`
- `data/fulltext/`
- `data/extraction_prompts/`
- `data/writing_contexts/`
- `outputs/`
- `outputs/sections/`

Always prefer running scripts with the workspace as the working directory. If a script writes outside the workspace because of `cwd`-relative defaults, treat that as a bug and correct it rather than accepting the leak.

## Topic Shapes

Use the topic form to choose retrieval behavior after the mandatory first pass.

- `Broad review`: single domain or field summary. Usually one OpenAlex pass is enough before screening.
- `Narrow mechanism review`: pathway, molecule, compartment, or process within a disease. Expect targeted bridge queries.
- `Intersection review`: explicit `A and B in X` or `A-B-X` question. Expect many papers on `A` alone and `B` alone; direct bridge retrieval is usually required.

## Corpus Adequacy Check

After the first OpenAlex acquisition round, inspect the candidate pool before treating it as sufficient.

Trigger a targeted second-pass OpenAlex bundle when any of these are true:

- the topic is an explicit pairwise or intersection prompt
- the pool contains many papers on each anchor separately but very few direct bridge papers
- the first-pass hits are dominated by broad disease reviews or tangential biomarker papers
- the user asked for mechanistic association rather than a generic topic summary

For the targeted second pass:

- generate a second query bundle with `step0b_queries.py --strategy bridge`
- keep the first pass intact; do not replace `outputs/search_queries.json`
- save bridge queries inside the same workspace and ingest them additively
- prefer query patterns like `A B disease`, `A B mechanism`, `A B amyloid beta`, `A B lipid raft`, `A B transport`, and `A B review`

## Two-Corpus Model

UniversalReviewer should build two related corpora before final writing:

- `primary corpus`: included papers used for screening, full-text fetch, extraction, and core evidence synthesis
- `citation corpus`: metadata-complete papers used for citation density, historical framing, and background support even when they are not full-text extracted

Citation papers must be ingested into the database with `paper_role = 'citation'`. They are valid for `\cite{...}` in the final review and must appear in generated BibTeX, but they do not count toward primary include/full-text/extraction quotas.

## Required Execution Order

### Step 0: Initialize workspace

```bash
python scripts/step0_init.py --prompt "your topic"
```

Creates the workspace, DuckDB database, and standard directory structure.

### Step 0b: Generate OpenAlex queries

```bash
python scripts/step0b_queries.py --workspace workspaces/<project_slug>
```

Required outputs:

- `outputs/search_queries.json`
- `data/query_bundles/refinement_prompt.txt`

This step is mandatory. `step1_acquire.py` must consume this query file.

For narrow intersection topics, a second targeted bundle is allowed and expected after the first acquisition round:

```bash
python scripts/step0b_queries.py --workspace workspaces/<project_slug> --strategy bridge
python scripts/step1_acquire.py --db-path <db> --queries-json <workspace>/outputs/search_queries_bridge.json
```

### Step 1: Acquire metadata from OpenAlex

```bash
python scripts/step1_acquire.py --db-path <db> --queries-json <workspace>/outputs/search_queries.json
```

This is the only supported initial acquisition path.

### Step 2: Screen abstracts

```bash
python scripts/step2_screen.py --db-path <db> --create-bundles --output-dir <workspace>/data/screening_bundles --topic "your topic"
```

Then use `agents/screener.md` and apply results with:

```bash
python scripts/step2_screen.py --db-path <db> --apply-json <decisions.json>
```

Screening does not need to clear the entire pending pool before later stages can start. Once a credible included seed set exists, fetch/extract/design/write may proceed while remaining pending papers are screened later.

Prioritize `include` decisions for:

- foundational genetics or epidemiology papers
- direct bridge papers connecting both sides of the topic
- mechanism-heavy reviews with high synthesis value
- human tissue, in vivo, or translational papers over tangential single-factor hits

If both a preprint and a peer-reviewed version of the same work are present, prefer the peer-reviewed version.

### Step 3: Expand iteratively

Use one or both of:

```bash
python scripts/step3_snowball.py --db-path <db> --auto
python scripts/step3b_references.py --db-path <db>
```

After each expansion round, return to Step 2.

Use the two expansion modes differently:

- `step3_snowball.py`: expand the primary candidate pool from included papers
- `step3b_references.py`: build the citation corpus from extracted reference suggestions and title-level background mentions

`step3b_references.py` should usually be run after extraction and before final design/writing when the review target is a long-form synthesis.

### Step 4: Fetch full text

```bash
python scripts/step4_fetch.py --db-path <db> --output-dir <workspace>/data/fulltext
```

Only valid after at least one paper is marked `include`.

### Step 5: Extract structured knowledge

```bash
python scripts/step5_extract.py --db-path <db> --prepare-prompts --output-dir <workspace>/data/extraction_prompts
```

Then use `agents/extractor.md` and apply results with:

```bash
python scripts/step5_extract.py --db-path <db> --apply-json <extractions.json>
```

Only papers with fetched full text should enter extraction.

The extractor output must match the apply schema accepted by `step5_extract.py`:

- `paper_id`
- `knowledge_points[]` with `knowledge_text`, `knowledge_type`, `source_type`
- `background_summary`
- `suggested_references[]`

Do not rely on stale field names such as `contribution_summary` or `important_references`.

### Step 6: Design blueprint

```bash
python scripts/step6_design.py --db-path <db> --dump
```

Then use `agents/curator.md` to create a blueprint and save it with:

```bash
python scripts/step6_design.py --db-path <db> --save-blueprint <blueprint.json>
```

### Step 7: Prepare chapter writing context

```bash
python scripts/step7_write.py --db-path <db> --chapter-tag <tag> --blueprint <workspace>/outputs/blueprint.json --sections-dir <workspace>/outputs/sections --output <workspace>/data/writing_contexts/<tag>.md
```

Write chapters sequentially. Later chapters must not be prepared before earlier chapter files exist.

`step7_write.py` prepares context only. It does not draft the final section file. After preparing context, the main agent must write the corresponding `secN.tex` or `chN.tex` file inside `outputs/sections/`.

Default writing language is English unless the user explicitly asks for another language and the render template supports it.
Writing context must be chapter-scoped and should include both primary evidence and chapter-relevant citation papers.

### Step 8: Render

```bash
python scripts/step8_render.py --db-path <db> --sections-dir <workspace>/outputs/sections --output-pdf <workspace>/outputs/review.pdf
```

Rendering is part of the default completion target. If `tectonic` needs network access to download bundles or fonts, request approval and continue to PDF rather than stopping at LaTeX source.

Before render, validate article size:

- target overall length: 8k-12k words
- target overall citation count: 120-180 unique references
- target chapter citation density: roughly 25-45 unique citations per chapter

Warn explicitly when these targets are not met. Optionally block render in strict mode.

## Stage Guards

Use `step_status.py` or `run_pipeline.py` before running the next stage.

```bash
python scripts/step_status.py --db-path <db>
python scripts/run_pipeline.py --db-path <db> --step next
```

These scripts enforce:

- no screening before OpenAlex acquisition
- no fetch before `include` papers exist
- no extract before fetched full text exists
- no design before extracted knowledge exists
- no write before blueprint exists
- no out-of-order chapter writing
- no render before section files exist

Guard interpretation:

- `screen` remains the nominal next action while pending papers exist
- later stages may still run once their explicit blockers are cleared, especially for seeded workflows on narrow topics
- use stage guards to prevent invalid transitions, not to force all pending papers to be exhausted before any downstream work

## Agent Responsibilities

| Agent | Stage | Responsibility |
|---|---|---|
| `screener.md` | screening | Include/exclude/maybe decisions and full-text priority |
| `extractor.md` | extraction | Knowledge points, background summary, suggested references |
| `curator.md` | design | Review blueprint and chapter mapping |
| `writer.md` | writing | LaTeX chapter drafting from prepared context |

Canonical stage schemas live in `references/stage-schemas.md`. When agent markdown and script behavior differ, follow the canonical schema and script interface.

## Non-Negotiable Constraints

1. All review artifacts must stay inside the workspace.
2. OpenAlex is the required initial acquisition source.
3. Manual literature additions may calibrate or extend the corpus later, but they must not replace the initial OpenAlex pass.
4. If a stage is blocked, stop and report the blocker instead of bypassing the SOP.
5. A targeted second-pass OpenAlex acquisition may extend the corpus after the mandatory initial pass, but it must remain inside the same workspace and use saved query bundles.
6. A review is only complete when the workspace contains chapter files and an attempted render output; default target is `<workspace>/outputs/review.pdf`.
