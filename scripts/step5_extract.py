"""
Step 5: Knowledge Extraction.
Prepares prompts for full-text extraction and ingests extracted knowledge JSON.
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Add core to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from core.db import DatabaseManager
from pipeline_state import ensure_stage_ready


def generate_knowledge_id(paper_id: str, index: int = 0) -> str:
    return f"{paper_id}_k{index}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--prepare-prompts", action="store_true", help="Generate extraction prompts")
    parser.add_argument("--output-dir", default="data/extraction_prompts")
    parser.add_argument("--apply-json", help="Ingest agent JSON output into the database")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    if sys.stdout.encoding != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass

    db = DatabaseManager(db_path=args.db_path)
    conn = db.get_connection()

    try:
        if args.prepare_prompts:
            try:
                ensure_stage_ready("extract", args.db_path)
            except RuntimeError as exc:
                print(str(exc))
                sys.exit(1)

            output_dir = Path(args.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

            papers = conn.execute(
                """
                SELECT paper_id, title, abstract, year, journal, fulltext_status, fulltext_path
                FROM papers
                WHERE screening_status = 'include'
                AND fulltext_status = 'fetched'
                AND paper_id NOT IN (SELECT DISTINCT paper_id FROM knowledge)
                ORDER BY citation_count DESC NULLS LAST
                LIMIT ?
                """,
                [args.limit],
            ).fetchall()

            if not papers:
                print("No full-text papers need knowledge extraction.")
                return

            for paper_id, title, abstract, year, journal, fulltext_status, fulltext_path in papers:
                fulltext = ""
                if fulltext_status == "fetched" and fulltext_path and os.path.exists(fulltext_path) and fulltext_path.endswith(".html"):
                    try:
                        with open(fulltext_path, "r", encoding="utf-8", errors="ignore") as handle:
                            from bs4 import BeautifulSoup

                            soup = BeautifulSoup(handle, "html.parser")
                            fulltext = soup.get_text(separator=" ", strip=True)[:15000]
                    except Exception as exc:
                        print(f"Error reading full text for {paper_id}: {exc}")
                        continue

                if not fulltext:
                    print(f"Skipping {paper_id}: no readable HTML full text")
                    continue

                payload = {
                    "task": "Extract scientific knowledge and suggest references",
                    "paper": {"id": paper_id, "title": title, "year": year, "journal": journal},
                    "content": fulltext,
                    "abstract": abstract,
                    "instructions": (
                        "Extract 3-5 knowledge points, one background summary, and up to 10 suggested references. "
                        "Suggested references should focus on foundational, key experimental, or conflicting papers."
                    ),
                    "schema": [
                        {"field": "knowledge_points", "type": "array"},
                        {"field": "background_summary", "type": "string"},
                        {"field": "suggested_references", "type": "array"},
                    ],
                }

                with open(output_dir / f"{paper_id}.json", "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2)

            print(f"Generated extraction prompts in {output_dir}")
            return

        if args.apply_json:
            if not os.path.exists(args.apply_json):
                print(f"Error: {args.apply_json} not found.")
                sys.exit(1)

            with open(args.apply_json, "r", encoding="utf-8") as handle:
                data = json.load(handle)

            extractions = data if isinstance(data, list) else [data]
            knowledge_count = 0
            reference_count = 0

            for extraction in extractions:
                paper_id = extraction.get("paper_id") or extraction.get("paper", {}).get("id")
                if not paper_id:
                    continue

                for index, knowledge_point in enumerate(extraction.get("knowledge_points", [])):
                    db.upsert_knowledge(
                        {
                            "knowledge_id": generate_knowledge_id(paper_id, index),
                            "paper_id": paper_id,
                            "knowledge_text": knowledge_point.get("knowledge_text", ""),
                            "knowledge_type": knowledge_point.get("knowledge_type", "finding"),
                            "source_type": knowledge_point.get("source_type", "original"),
                            "original_reference_id": paper_id,
                            "confidence_score": 0.9,
                        }
                    )
                    knowledge_count += 1

                background_summary = extraction.get("background_summary", "")
                if background_summary:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO summaries (summary_id, paper_id, background_summary, found_references)
                        VALUES (?, ?, ?, ?)
                        """,
                        [
                            f"{paper_id}_summary",
                            paper_id,
                            background_summary,
                            json.dumps(extraction.get("suggested_references", [])),
                        ],
                    )
                reference_count += len(extraction.get("suggested_references", []))

            conn.commit()
            print(f"Ingested {knowledge_count} knowledge points from {len(extractions)} papers.")
            print(f"Found {reference_count} suggested references for snowballing.")
            return

        parser.print_help()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
