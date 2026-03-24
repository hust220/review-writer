"""
Step 3: Snowball Discovery.
Expands the paper pool from included papers and extracted references.
"""

import argparse
import json
import os
import sys

# Add core to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from core.db import DatabaseManager
from core.search import OpenAlexSearcher
from pipeline_state import ensure_stage_ready


def single_snowball_round(db_path: str) -> int:
    db = DatabaseManager(db_path=db_path)
    conn = db.get_connection()
    searcher = OpenAlexSearcher()

    try:
        rows = conn.execute("SELECT referenced_works_json FROM papers WHERE screening_status = 'include'").fetchall()
        all_refs = set()
        for row in rows:
            if row[0]:
                for ref in json.loads(row[0]):
                    all_refs.add(ref.split("/")[-1])

        rows = conn.execute("SELECT found_references FROM summaries WHERE found_references IS NOT NULL").fetchall()
        for row in rows:
            if row[0]:
                found = json.loads(row[0])
                for ref in found:
                    title = ref.get("title")
                    if not title:
                        continue
                    results = searcher.search(title, limit=1)
                    for result in results:
                        paper_id = result.get("id", "").split("/")[-1]
                        if not paper_id:
                            continue
                        abstract = searcher.reconstruct_abstract(result.get("abstract_inverted_index"))
                        db.upsert_paper(
                            {
                                "paper_id": paper_id,
                                "doi": (result.get("doi") or "").replace("https://doi.org/", ""),
                                "title": result.get("title"),
                                "abstract": abstract,
                                "year": result.get("publication_year"),
                                "journal": ((result.get("primary_location") or {}).get("source") or {}).get("display_name", "Unknown"),
                                "referenced_works_json": json.dumps(result.get("referenced_works", [])),
                                "citation_count": result.get("cited_by_count"),
                            }
                        )

        existing_ids = {row[0] for row in conn.execute("SELECT paper_id FROM papers").fetchall()}
        new_ids = list(all_refs - existing_ids)[:100]

        total_ingested = 0
        if new_ids:
            print(f"Fetching {len(new_ids)} new references from OpenAlex IDs...")
            results = searcher.get_works_by_ids(new_ids)
            for result in results:
                paper_id = result.get("id", "").split("/")[-1]
                if not paper_id:
                    continue
                abstract = searcher.reconstruct_abstract(result.get("abstract_inverted_index"))
                db.upsert_paper(
                    {
                        "paper_id": paper_id,
                        "doi": (result.get("doi") or "").replace("https://doi.org/", ""),
                        "title": result.get("title"),
                        "abstract": abstract,
                        "year": result.get("publication_year"),
                        "journal": ((result.get("primary_location") or {}).get("source") or {}).get("display_name", "Unknown"),
                        "referenced_works_json": json.dumps(result.get("referenced_works", [])),
                        "citation_count": result.get("cited_by_count"),
                    }
                )
                total_ingested += 1
        return total_ingested
    finally:
        conn.close()


def auto_snowball(db_path: str, target: int = 80, max_rounds: int = 3, min_new_per_round: int = 10) -> None:
    db = DatabaseManager(db_path=db_path)
    conn = db.get_connection()
    try:
        for round_num in range(1, max_rounds + 1):
            included = conn.execute("SELECT COUNT(*) FROM papers WHERE screening_status = 'include'").fetchone()[0]
            print("=" * 60)
            print(f"Snowball round {round_num}/{max_rounds}")
            print(f"Included papers: {included}/{target}")
            print("=" * 60)

            if included >= target:
                print(f"Target reached ({included} >= {target}).")
                break

            new_ingested = single_snowball_round(db_path)
            print(f"Ingested {new_ingested} new candidates.")
            if new_ingested < min_new_per_round:
                print(f"Only {new_ingested} new papers were found. Stopping snowballing.")
                break

            pending = conn.execute("SELECT COUNT(*) FROM papers WHERE screening_status = 'pending'").fetchone()[0]
            print(f"{pending} papers are now pending screening.")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--target", type=int, default=80)
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--min-new-per-round", type=int, default=10)
    parser.add_argument("--auto", action="store_true")
    args = parser.parse_args()

    if sys.stdout.encoding != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass

    try:
        ensure_stage_ready("snowball", args.db_path)
    except RuntimeError as exc:
        print(str(exc))
        sys.exit(1)

    if args.auto:
        auto_snowball(args.db_path, args.target, args.max_rounds, args.min_new_per_round)
        return

    new_ingested = single_snowball_round(args.db_path)
    print(f"Ingested {new_ingested} new candidates.")
    print("Next step: screen the new candidates with step2_screen.py")


if __name__ == "__main__":
    main()
