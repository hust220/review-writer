"""
Step 4: Fetch Full Text.
Downloads PDFs from OA sources and HTML from PMC for included papers.
"""

import argparse
import os
import sys
from pathlib import Path

# Add core to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from core.db import DatabaseManager
from core.pmc import PMCFetcher, resolve_to_pmcid
from core.pubmed import title_to_pmcid
from core.search import download_file, get_oa_link
from pipeline_state import ensure_stage_ready


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--output-dir", default="data/fulltext")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--pmc-only", action="store_true")
    parser.add_argument("--use-title-search", action="store_true")
    args = parser.parse_args()

    if sys.stdout.encoding != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass

    try:
        ensure_stage_ready("fetch", args.db_path)
    except RuntimeError as exc:
        print(str(exc))
        sys.exit(1)

    db = DatabaseManager(db_path=args.db_path)
    conn = db.get_connection()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        pending_pmc_doi = conn.execute(
            """
            SELECT paper_id, doi
            FROM papers
            WHERE screening_status = 'include' AND pmcid IS NULL AND doi IS NOT NULL AND doi != ''
            """
        ).fetchall()

        if pending_pmc_doi:
            doi_list = [row[1] for row in pending_pmc_doi]
            mapping = resolve_to_pmcid(doi_list)
            for doi, pmcid in mapping.items():
                conn.execute("UPDATE papers SET pmcid = ? WHERE doi = ?", [pmcid, doi])

        if args.use_title_search:
            pending_pmc_title = conn.execute(
                """
                SELECT paper_id, title
                FROM papers
                WHERE screening_status = 'include' AND pmcid IS NULL
                AND title IS NOT NULL AND title != ''
                """
            ).fetchall()
            for paper_id, title in pending_pmc_title:
                pmcid = title_to_pmcid(title)
                if pmcid:
                    conn.execute("UPDATE papers SET pmcid = ? WHERE paper_id = ?", [pmcid, paper_id])

        papers = conn.execute(
            """
            SELECT paper_id, doi, pmcid, title
            FROM papers
            WHERE screening_status = 'include' AND (fulltext_status IS NULL OR fulltext_status = 'none')
            ORDER BY needs_fulltext DESC, citation_count DESC NULLS LAST
            LIMIT ?
            """,
            [args.limit],
        ).fetchall()

        if not papers:
            print("No papers need full-text fetching.")
            return

        pmc_fetcher = PMCFetcher(data_dir=str(output_dir))
        success = 0
        for paper_id, doi, pmcid, title in papers:
            print(f"[{paper_id}] {title[:60]}...")
            fetched = False

            if not args.pmc_only and doi:
                oa_link = get_oa_link(doi)
                if oa_link and oa_link.format == "pdf":
                    path = output_dir / f"{paper_id}.pdf"
                    if download_file(oa_link.url, str(path)):
                        conn.execute(
                            "UPDATE papers SET fulltext_status = 'fetched', fulltext_path = ?, access_method = ? WHERE paper_id = ?",
                            [str(path), "oa_pdf", paper_id],
                        )
                        success += 1
                        fetched = True
                        print("  Downloaded PDF via OA")

            if not fetched and pmcid:
                path, method = pmc_fetcher.fetch_article(pmcid)
                if path:
                    conn.execute(
                        "UPDATE papers SET fulltext_status = 'fetched', fulltext_path = ?, access_method = ? WHERE paper_id = ?",
                        [path, method, paper_id],
                    )
                    success += 1
                    fetched = True
                    print(f"  Downloaded HTML via PMC ({method})")

            if not fetched:
                conn.execute("UPDATE papers SET fulltext_status = 'none' WHERE paper_id = ?", [paper_id])
                print("  Could not fetch full text")

        pmc_fetcher.stop()
        print(f"Successfully fetched {success}/{len(papers)} papers.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
