"""
Step 6: Design Architecture.
Generates a knowledge dump for blueprint creation and saves blueprint JSON.
"""

import argparse
import json
import os
import sys

# Add core to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from core.db import DatabaseManager
from pipeline_state import ensure_stage_ready


def get_knowledge_dump(db_path: str) -> str:
    db = DatabaseManager(db_path=db_path)
    conn = db.get_connection()
    try:
        rows = conn.execute(
            """
            SELECT k.knowledge_text, k.knowledge_type, k.source_type, k.original_reference_id, k.paper_id, p.title, p.year
            FROM knowledge k
            JOIN papers p ON k.paper_id = p.paper_id
            ORDER BY k.knowledge_type, p.citation_count DESC NULLS LAST
            """
        ).fetchall()
    finally:
        conn.close()

    lines = ["# Knowledge Corpus for Architecture Design", f"Total knowledge points: {len(rows)}", ""]
    for text, knowledge_type, source_type, original_reference_id, paper_id, title, year in rows:
        lines.append(f"[{knowledge_type.upper()}|{source_type}] {text}")
        lines.append(f"  Source: {original_reference_id} | Paper: {title} ({year}) | Paper ID: {paper_id}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--dump", action="store_true", help="Save knowledge dump to data/knowledge_dump.md")
    parser.add_argument("--save-blueprint", help="Save blueprint JSON and update chapter links")
    args = parser.parse_args()

    if sys.stdout.encoding != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass

    workspace = None

    if args.dump:
        try:
            workspace = ensure_stage_ready("design", args.db_path).workspace
        except RuntimeError as exc:
            print(str(exc))
            sys.exit(1)

        dump = get_knowledge_dump(args.db_path)
        data_dir = os.path.join(workspace, "data")
        os.makedirs(data_dir, exist_ok=True)
        dump_path = os.path.join(data_dir, "knowledge_dump.md")
        with open(dump_path, "w", encoding="utf-8") as handle:
            handle.write(dump)
        print(f"Knowledge dump saved to {dump_path}")
        return

    if args.save_blueprint:
        try:
            workspace = ensure_stage_ready("design", args.db_path).workspace
        except RuntimeError as exc:
            print(str(exc))
            sys.exit(1)

        with open(args.save_blueprint, "r", encoding="utf-8") as handle:
            blueprint = json.load(handle)

        output_dir = args.output_dir if os.path.isabs(args.output_dir) else os.path.join(workspace, args.output_dir)
        output_path = os.path.join(output_dir, "blueprint.json")
        os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(blueprint, handle, indent=2)

        db = DatabaseManager(db_path=args.db_path)
        conn = db.get_connection()
        try:
            conn.execute("DELETE FROM paper_chapter_links")
            for chapter in blueprint.get("chapters", []):
                tag = chapter.get("tag", f"ch{chapter.get('number', '0')}")
                for paper_id in chapter.get("paper_ids", []):
                    db.link_paper_to_chapter(paper_id, tag)
        finally:
            conn.close()
        print(f"Blueprint saved to {output_path}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
