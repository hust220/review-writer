"""
Step 3b: Fetch Suggested References (v18.0.0)
Fetches references suggested by LLM during knowledge extraction.
These references are marked as 'citation' role and used for citation purposes only.
"""

import os
import json
import argparse
import sys
import time
import re

# Add core to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from core.db import DatabaseManager
from core.search import OpenAlexSearcher


def normalize_title(title: str) -> str:
    title = (title or "").lower()
    title = re.sub(r"<[^>]+>", " ", title)
    title = re.sub(r"[^a-z0-9\s]", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def looks_like_title(text: str) -> bool:
    candidate = text.strip()
    if len(candidate) < 20 or len(candidate) > 220:
        return False
    if candidate.count(" ") < 3:
        return False
    if re.search(r"\\cite|paper id|source:|chapter|background", candidate.lower()):
        return False
    alpha_count = sum(ch.isalpha() for ch in candidate)
    return alpha_count >= 12


def extract_parenthetical_titles(summary: str) -> list[dict]:
    refs = []
    for match in re.findall(r"\(([^()]+)\)", summary or ""):
        candidate = match.strip()
        if looks_like_title(candidate):
            refs.append({"title": candidate, "reason": "Referenced by title in background summary"})
    return refs


def authors_payload(result: dict) -> str:
    authors = []
    for authorship in result.get("authorships", []) or []:
        author = authorship.get("author") or {}
        name = author.get("display_name")
        if name:
            authors.append({"name": name})
    return json.dumps(authors)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--limit", type=int, default=100, help="Maximum references to fetch")
    args = parser.parse_args()

    # Fix UTF-8 encoding for Windows
    if sys.stdout.encoding != 'utf-8':
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except AttributeError:
            pass

    db = DatabaseManager(db_path=args.db_path)
    conn = db.get_connection()
    searcher = OpenAlexSearcher()
    
    # 1. Collect all suggested references from summaries table
    has_summaries = conn.execute("""
        SELECT name FROM sqlite_master WHERE type='table' AND name='summaries'
    """).fetchone()
    
    if not has_summaries:
        print("❌ Summaries table not found. Run step5_extract.py first.")
        conn.close()
        return
    
    rows = conn.execute(
        """
        SELECT background_summary, found_references
        FROM summaries
        WHERE found_references IS NOT NULL OR background_summary IS NOT NULL
        """
    ).fetchall()
    
    all_refs = []
    for row in rows:
        background_summary, found_references = row
        if found_references:
            try:
                refs = json.loads(found_references)
                for ref in refs:
                    if isinstance(ref, dict) and 'title' in ref:
                        all_refs.append(ref)
            except json.JSONDecodeError:
                continue
        if background_summary:
            all_refs.extend(extract_parenthetical_titles(background_summary))
    
    if not all_refs:
        print("✅ No suggested references found in extraction results.")
        conn.close()
        return
    
    print(f"📚 Found {len(all_refs)} suggested references from LLM extractions")
    
    # 2. Deduplicate by title
    seen_titles = set()
    unique_refs = []
    for ref in all_refs:
        title = ref.get('title', '').lower().strip()
        if title and title not in seen_titles:
            seen_titles.add(title)
            unique_refs.append(ref)
    
    print(f"   {len(unique_refs)} unique references after deduplication")
    
    # 3. Get existing papers to avoid duplicates
    existing_ids = {r[0] for r in conn.execute("SELECT paper_id FROM papers").fetchall()}
    existing_titles = {
        normalize_title(r[0]) for r in conn.execute("SELECT title FROM papers WHERE title IS NOT NULL").fetchall() if r[0]
    }
    
    # 4. Search for each reference on OpenAlex
    fetched = 0
    skipped = 0
    
    for ref in unique_refs[:args.limit]:
        title = ref.get('title', '')
        if not title:
            continue
        
        # Skip if we already have this paper
        normalized_title = normalize_title(title)
        if normalized_title in existing_titles:
            skipped += 1
            continue
        
        print(f"  Searching: {title[:60]}...")
        results = searcher.search(title, limit=1)
        
        if not results:
            # Try a simpler search
            simple_title = ' '.join(title.split()[:10])  # First 10 words
            results = searcher.search(simple_title, limit=1)
        
        for r in results:
            paper_id = r.get('id', '').split('/')[-1]
            if not paper_id or paper_id in existing_ids:
                continue
            
            abstract = searcher.reconstruct_abstract(r.get('abstract_inverted_index'))
            
            db.upsert_paper({
                'paper_id': paper_id,
                'doi': (r.get('doi') or '').replace('https://doi.org/', ''),
                'title': r.get('title'),
                'abstract': abstract,
                'year': r.get('publication_year'),
                'journal': ((r.get('primary_location') or {}).get('source') or {}).get('display_name', 'Unknown'),
                'authors_json': authors_payload(r),
                'referenced_works_json': json.dumps(r.get('referenced_works', [])),
                'citation_count': r.get('cited_by_count'),
                'paper_role': 'citation',
                'screening_status': 'exclude',
                'screening_reason': 'citation-only support paper'
            })
            
            existing_ids.add(paper_id)
            existing_titles.add(normalize_title(r.get('title', '')))
            fetched += 1
            
            print(f"    ✓ Added: {r.get('title', '')[:50]}...")
            break
        
        time.sleep(0.3)  # Rate limiting
    
    conn.close()
    print(f"\n✅ Fetching complete!")
    print(f"   Fetched: {fetched} new citation papers")
    print(f"   Skipped: {skipped} (already in database)")
    print(f"\nThese papers are marked as 'citation' role and will be used as citation support corpus.")

if __name__ == "__main__":
    main()
