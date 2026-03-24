# Stage Schemas

Canonical payload shapes for `universal-reviewer`. When agent markdown and scripts disagree, follow this file and the script interface.

## Screening Apply Schema

Consumed by:

```bash
python scripts/step2_screen.py --db-path <db> --apply-json <decisions.json>
```

Shape:

```json
[
  {
    "paper_id": "W123",
    "decision": "include",
    "reason": "One sentence.",
    "needs_fulltext": true
  }
]
```

## Extraction Apply Schema

Consumed by:

```bash
python scripts/step5_extract.py --db-path <db> --apply-json <extractions.json>
```

Shape:

```json
[
  {
    "paper_id": "W123",
    "knowledge_points": [
      {
        "knowledge_text": "Specific technical claim.",
        "knowledge_type": "finding",
        "source_type": "original"
      }
    ],
    "background_summary": "Context summary.",
    "suggested_references": [
      {
        "title": "Referenced paper title",
        "reason": "Why it matters for snowballing"
      }
    ]
  }
]
```

Notes:

- `contribution_summary` is not consumed by the current pipeline.
- `important_references` is not consumed by the current pipeline.

## Blueprint Save Schema

Consumed by:

```bash
python scripts/step6_design.py --db-path <db> --save-blueprint <blueprint.json>
```

Minimum shape:

```json
{
  "title": "Review title",
  "chapters": [
    {
      "number": 1,
      "tag": "chapter-tag",
      "title": "Chapter title",
      "objective": "Chapter objective",
      "key_themes": ["theme one", "theme two"],
      "paper_ids": ["W123", "W456"]
    }
  ]
}
```

## Writing Responsibility

`step7_write.py` only emits a chapter context package. It does not create section LaTeX automatically.

Required outputs for a completed review:

- `outputs/blueprint.json`
- `outputs/sections/sec*.tex` or `outputs/sections/ch*.tex`
- `outputs/sections/main.tex`
- `outputs/review.pdf` when rendering succeeds
