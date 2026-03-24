# UniversalReviewer

UniversalReviewer is a staged review-writing pipeline for building a literature corpus, extracting structured evidence, and producing chapter-writing context inside a dedicated workspace.

## What Changed

This version makes the OpenAlex acquisition path explicit and enforceable:

- `step0b_queries.py` generates the required `outputs/search_queries.json`
- `step_status.py` reports the current stage and next command
- `run_pipeline.py` executes the next valid non-agent step
- stage guards block out-of-order execution

## Quick Start

1. Initialize a workspace:

```bash
python scripts/step0_init.py --prompt "your topic"
```

2. Generate OpenAlex queries:

```bash
python scripts/step0b_queries.py --workspace workspaces/<project_slug>
```

3. Acquire initial candidates from OpenAlex:

```bash
python scripts/step1_acquire.py --db-path workspaces/<project_slug>/db/review.duckdb --queries-json workspaces/<project_slug>/outputs/search_queries.json
```

4. Check the next valid stage at any time:

```bash
python scripts/step_status.py --db-path workspaces/<project_slug>/db/review.duckdb
```

Or let the helper run the next valid scripted step:

```bash
python scripts/run_pipeline.py --db-path workspaces/<project_slug>/db/review.duckdb --step next
```

## Notes

- Screening, extraction, blueprint design, and final chapter drafting still require the corresponding agents.
- The pipeline is workspace-scoped. Generated files should stay under `workspaces/<project_slug>/`.
- If OpenAlex acquisition is blocked, stop and report the blocker. Do not replace it with direct manual writing.
