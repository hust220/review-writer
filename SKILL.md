---
name: universal-reviewer
version: "14.5.0"
description: Zero-touch autonomous review engine. One command → PDF. Agent auto-executes all checkpoints. UVA login is the only manual step.
---

# UniversalReviewer (v14.5) - Zero-Touch Autonomous Review

## THE ONE COMMAND

When a user asks you to write a review, run this:

```bash
python3 scripts/pipeline_runner.py --prompt "user's topic here" --full-auto
```

## CORE MANDATE - READ THIS FIRST

**You are an autonomous agent. You MUST execute the ENTIRE pipeline without asking the user for permission or input.**

When `pipeline_runner.py` prints a CHECKPOINT, it gives you STEP A, STEP B, STEP C commands. You MUST:

1. **Read the commands** from the terminal output
2. **Execute Step A** (get the prompt)
3. **Execute Step B** (send prompt to Task agent)
4. **Save the Task output** to a temp JSON file
5. **Execute Step C** (save to database)
6. **Repeat** for all bundles/chapters
7. **Execute the final command** (`python3 pipeline_runner.py --next`)

**NEVER stop and ask "should I continue?" or "what next?".** Just do it.

## FULL AUTONOMOUS WORKFLOW

### Phase 1: Bootstrap (Automatic)
```
python3 pipeline_runner.py --prompt "topic" --full-auto
```
This runs steps 0-2 automatically (search, snowball, env check).

### Phase 2: Knowledge Extraction (YOU execute automatically)

When the pipeline stops at `CHECKPOINT: extract_knowledge`, you see commands like:

```
--- bundle_001.json ---
Step A: python3 scripts/batch_processor.py --db-path <db> --type extract --bundle <file> --output-dir <dir>
Step B: Send the prompt to Task agent
Step C: python3 scripts/extract_knowledge.py --db-path <db> --save-json <output.json>
```

**YOUR ACTION (do this automatically, no user permission needed):**

```python
# For EACH bundle, do this loop automatically:
import subprocess, json, tempfile

# 1. Run Step A to get the prompt
result = subprocess.run(["python3", "scripts/batch_processor.py", "--db-path", db_path, "--type", "extract", "--bundle", bundle_file, "--output-dir", bundles_dir], capture_output=True, text=True)
prompt = result.stdout

# 2. Send to Task agent (you do this with the Task tool)
# The Task agent returns a JSON array of knowledge objects

# 3. Save to temp file and run Step C
with open("/tmp/knowledge_result.json", "w") as f:
    json.dump(task_result, f)
subprocess.run(["python3", "scripts/extract_knowledge.py", "--db-path", db_path, "--save-json", "/tmp/knowledge_result.json"])
```

After ALL bundles processed, run:
```
python3 pipeline_runner.py --next
```

### Phase 3: Architecture Design (YOU execute automatically)

When the pipeline stops at `CHECKPOINT: design_architecture`:

**YOUR ACTION:**
```bash
# 1. Get knowledge summary
python3 scripts/design_architecture.py --db-path <db> --output-dir <dir> --summary-only

# 2. Read the summary, design chapters with Task agent
# Task agent returns a blueprint JSON

# 3. Save blueprint
python3 scripts/design_architecture.py --db-path <db> --output-dir <dir> --save-blueprint /tmp/blueprint.json

# 4. Continue
python3 pipeline_runner.py --next
```

### Phase 4: Chapter Writing (YOU execute automatically)

When the pipeline stops at `CHECKPOINT: write_chapters`:

**YOUR ACTION:**
```bash
# For EACH chapter, do this loop automatically:

# 1. Get writing prompt
python3 scripts/batch_processor.py --db-path <db> --type write --bundle <chapter_X.json> --output-dir <dir>

# 2. Send prompt to Task agent → get LaTeX output

# 3. Save chapter
echo "<task_latex_output>" > outputs/sections/sec<N>.tex
```

After ALL chapters written, run:
```
python3 pipeline_runner.py --next
```

### Phase 5: Render (Automatic)

```
Pipeline renders PDF automatically. You just report the result.
```

## WHAT TO TELL THE USER

After running the initial command, tell the user:
```
"I'm generating your review. This will take several minutes. 
I'll handle all the extraction, design, and writing automatically.
You'll see the PDF when it's ready."
```

Then execute the ENTIRE pipeline autonomously. Only report back when:
- The PDF is generated (success)
- UVA login is needed (ask user to complete browser login)
- A critical error occurs

## EXCEPTIONS (when to ask user)

1. **UVA Login Required**: Cookie expired → ask user to complete browser login
2. **Critical Error**: API down, database corrupt → report and ask for guidance
3. **PDF Render Failure**: Report the error and suggest fix

## DATABASE

| Table | Purpose |
|---|---|
| `papers` | 150+ papers with metadata |
| `knowledge` | Extracted knowledge with `source_type` |
| `reference_stubs` | Referenced papers not in main collection |
| `knowledge_chapter_links` | Maps knowledge to chapters |

## KEY FILES

| File | Purpose |
|---|---|
| `pipeline_runner.py` | Master orchestrator |
| `batch_processor.py` | Bundle creator and prompt generator |
| `extract_knowledge.py` | Knowledge CLI (--save-json) |
| `design_architecture.py` | Architecture CLI (--save-blueprint) |
| `write_chapters.py` | Chapter writing helpers |
| `manager.py` | Snowball, render |
