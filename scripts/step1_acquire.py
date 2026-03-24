"""
Step 1: Acquire Metadata.
Searches OpenAlex using refined queries and ingests results into the database.
"""

import argparse
import json
import os
import sys
import time

# Add core to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from core.db import DatabaseManager
from core.search import OpenAlexSearcher
from pipeline_state import ensure_stage_ready


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True, help="Path to DuckDB")
    parser.add_argument("--queries-json", required=True, help="Path to search_queries.json")
    parser.add_argument("--limit", type=int, default=80, help="Papers per query")
    args = parser.parse_args()

    if sys.stdout.encoding != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass

    if not os.path.exists(args.queries_json):
        print(f"Error: {args.queries_json} not found. Generate search_queries.json first.")
        sys.exit(1)

    try:
        ensure_stage_ready("acquire", args.db_path)
    except RuntimeError as exc:
        print(str(exc))
        sys.exit(1)

    with open(args.queries_json, "r", encoding="utf-8") as handle:
        queries = json.load(handle)

    db = DatabaseManager(db_path=args.db_path)
    searcher = OpenAlexSearcher()

    print(f"Acquiring papers from {len(queries)} queries...")
    total_ingested = 0

    for query in queries:
        print(f"  Searching: {query}...")
        results = searcher.search(query, limit=args.limit)
        for result in results:
            raw_id = result.get("id", "")
            paper_id = raw_id.split("/")[-1] if "/" in raw_id else raw_id
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
        time.sleep(1)

    print(f"Ingested {total_ingested} candidates.")
    print("Next step: screen abstracts with step2_screen.py")


if __name__ == "__main__":
    main()
