"""
Step 2: Screen Abstracts.
Creates screening bundles and applies screening decisions.
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Add core to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from batch_processor import create_screening_prompt
from core.db import DatabaseManager
from pipeline_state import ensure_stage_ready


def get_pending_papers(db_path: str, limit: int = 100):
    db = DatabaseManager(db_path=db_path)
    conn = db.get_connection()
    try:
        rows = conn.execute(
            """
            SELECT paper_id, title, abstract
            FROM papers
            WHERE screening_status = 'pending'
            AND abstract IS NOT NULL AND abstract != ''
            ORDER BY citation_count DESC NULLS LAST
            LIMIT ?
            """,
            [limit],
        ).fetchall()
    finally:
        conn.close()
    return [{"paper_id": row[0], "title": row[1], "abstract": row[2]} for row in rows]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--create-bundles", action="store_true", help="Create screening bundles for the agent")
    parser.add_argument("--output-dir", help="Directory for screening bundles")
    parser.add_argument("--apply-json", help="Apply JSON decisions to the database")
    parser.add_argument("--topic", help="Research topic for screening")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    db = DatabaseManager(db_path=args.db_path)

    if args.create_bundles:
        try:
            ensure_stage_ready("screen", args.db_path)
        except RuntimeError as exc:
            print(str(exc))
            sys.exit(1)

        if not args.output_dir:
            print("Error: --output-dir is required with --create-bundles.")
            sys.exit(1)

        pending = get_pending_papers(args.db_path, args.limit)
        if not pending:
            print("No papers are pending screening.")
            return

        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        batch_size = 20
        topic = args.topic or "Search Results"

        for index in range(0, len(pending), batch_size):
            batch = pending[index : index + batch_size]
            if batch:
                batch[0]["topic"] = topic
            prompt = create_screening_prompt(batch)
            path = output_dir / f"bundle_{index // batch_size + 1}.txt"
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(prompt)
            print(f"Created screening bundle: {path}")
        return

    if args.apply_json:
        if not os.path.exists(args.apply_json):
            print(f"Error: {args.apply_json} not found.")
            sys.exit(1)

        with open(args.apply_json, "r", encoding="utf-8") as handle:
            decisions = json.load(handle)

        count = 0
        for decision in decisions:
            db.update_screening_decision(
                decision["paper_id"],
                decision["decision"],
                decision.get("reason", ""),
                decision.get("needs_fulltext", False),
            )
            count += 1
        print(f"Applied {count} screening decisions.")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
