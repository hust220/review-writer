"""
Knowledge Extraction Pipeline (v14.0)
Extracts structured knowledge points from paper abstracts/fulltexts with citation fidelity.

Usage:
    python3 extract_knowledge.py --db-path /path/to/review.duckdb --mode abstract
    python3 extract_knowledge.py --db-path /path/to/review.duckdb --mode fulltext

The actual AI extraction is done by the Review-OS orchestrator using Task agents.
This script provides the data pipeline: querying, batching, and persisting.
"""

import duckdb
import json
import re
import os
import sys
import hashlib
from typing import List, Dict, Tuple, Optional

def get_papers_for_extraction(db_path: str, mode: str = "abstract", limit: int = 50) -> List[Dict]:
    """Get papers that need knowledge extraction."""
    conn = duckdb.connect(db_path)
    
    if mode == "abstract":
        rows = conn.execute("""
            SELECT p.paper_id, p.title, p.abstract, p.year, p.journal, p.referenced_works_json
            FROM papers p
            WHERE p.screening_status = 'include' 
            AND p.abstract IS NOT NULL AND p.abstract != ''
            AND p.paper_id NOT IN (SELECT DISTINCT paper_id FROM knowledge)
            ORDER BY p.citation_count DESC NULLS LAST
            LIMIT ?
        """, [limit]).fetchall()
    else:  # fulltext mode
        rows = conn.execute("""
            SELECT p.paper_id, p.title, p.abstract, p.year, p.journal, p.referenced_works_json
            FROM papers p
            WHERE p.screening_status = 'include' 
            AND p.fulltext_status = 'fetched'
            AND p.paper_id NOT IN (SELECT DISTINCT paper_id FROM knowledge WHERE evidence_span = 'fulltext')
            ORDER BY p.citation_count DESC NULLS LAST
            LIMIT ?
        """, [limit]).fetchall()
    
    conn.close()
    
    papers = []
    for row in rows:
        paper_id, title, abstract, year, journal, ref_json = row
        refs = json.loads(ref_json) if ref_json else []
        papers.append({
            'paper_id': paper_id,
            'title': title or '',
            'abstract': abstract or '',
            'year': year,
            'journal': journal or '',
            'referenced_works': refs
        })
    return papers


def build_extraction_prompt(paper: Dict) -> str:
    """Build the extraction prompt for the agent."""
    ref_list = "\n".join([f"  - {ref}" for ref in paper['referenced_works'][:20]])
    
    prompt = f"""You are a scientific knowledge extraction agent. Extract ALL distinct scientific knowledge points from the following paper.

PAPER: "{paper['title']}" ({paper['year']}, {paper['journal']})
ABSTRACT:
{paper['abstract']}

REFERENCED WORKS (first 20):
{ref_list if ref_list else '  (none)'}

For EACH knowledge point, output a JSON object with these fields:
- knowledge_text: The precise scientific statement (paraphrase if needed, keep technical detail)
- knowledge_type: One of [mechanism, result, method, limitation, structural, design, interaction, finding, hypothesis, comparison]
- source_type: 
  - "original" if this is a NEW finding/method from THIS paper (words like "we found", "we propose", "our results", "we developed")
  - "referenced" if this paper is REPORTING someone else's finding (words like "X et al. showed", "previous studies demonstrated", "it has been shown")
  - "unknown" if source cannot be determined
- original_reference_id: 
  - If source_type is "original": use the paper's ID "{paper['paper_id']}"
  - If source_type is "referenced": try to match to one of the referenced works above, or write "UNRESOLVED: [description]"
  - If source_type is "unknown": write "unknown"
- confidence_score: 0.0-1.0, your confidence in the extraction quality

Output as a JSON array. Extract at least 3 and at most 15 knowledge points. Be precise and avoid redundancy.
"""
    return prompt


def build_refinement_prompt(paper: Dict, raw_output: str) -> str:
    """Build a refinement prompt if the first extraction needs improvement."""
    return f"""Review and refine the following knowledge extraction for "{paper['title']}".
Ensure each point has correct source_type and original_reference_id.

RAW OUTPUT:
{raw_output}

Rules:
1. If a finding is from THIS paper's own experiments, source_type must be "original"
2. If citing previous work, source_type must be "referenced" with the cited reference
3. No duplicate knowledge points
4. Each knowledge_text must be a complete, self-contained scientific statement

Output the refined JSON array."""


def parse_extraction_output(output: str) -> List[Dict]:
    """Parse the agent's JSON output into structured knowledge records."""
    # Try to find JSON array in the output
    json_match = re.search(r'\[.*\]', output, re.DOTALL)
    if not json_match:
        # Try to find individual JSON objects
        objects = re.findall(r'\{[^}]+\}', output)
        if objects:
            try:
                return [json.loads(obj) for obj in objects]
            except json.JSONDecodeError:
                pass
        return []
    
    try:
        return json.loads(json_match.group())
    except json.JSONDecodeError:
        return []


def generate_knowledge_id(paper_id: str, text: str, index: int) -> str:
    """Generate a unique knowledge ID."""
    hash_input = f"{paper_id}:{text[:100]}"
    short_hash = hashlib.md5(hash_input.encode()).hexdigest()[:8]
    return f"K_{paper_id}_{index}_{short_hash}"


def save_knowledge(db_path: str, knowledge_list: List[Dict], paper_id: str, evidence_span: str = "abstract"):
    """Save extracted knowledge to the database."""
    conn = duckdb.connect(db_path)
    
    for i, k in enumerate(knowledge_list):
        k_id = generate_knowledge_id(paper_id, k.get('knowledge_text', ''), i)
        
        conn.execute("""
            INSERT OR IGNORE INTO knowledge 
            (knowledge_id, paper_id, original_reference_id, source_type, 
             knowledge_text, knowledge_type, evidence_span, confidence_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            k_id,
            paper_id,
            k.get('original_reference_id', 'unknown'),
            k.get('source_type', 'unknown'),
            k.get('knowledge_text', ''),
            k.get('knowledge_type', 'finding'),
            evidence_span,
            k.get('confidence_score', 0.5)
        ])
    
    conn.close()
    print(f"  Saved {len(knowledge_list)} knowledge points for {paper_id}")


def create_reference_stub(db_path: str, ref_url: str, cited_by_paper: str):
    """Create a stub entry for a reference not in the main collection."""
    ref_id = ref_url.split('/')[-1] if '/' in ref_url else ref_url
    
    conn = duckdb.connect(db_path)
    exists = conn.execute("SELECT 1 FROM reference_stubs WHERE stub_id = ?", [ref_id]).fetchone()
    if not exists:
        conn.execute("""
            INSERT INTO reference_stubs (stub_id, openalex_id, cited_by_paper)
            VALUES (?, ?, ?)
        """, [ref_id, ref_url, cited_by_paper])
    conn.close()


def dump_knowledge_for_chapter(chapter_tag: str, db_path: str) -> str:
    """Dump all knowledge points tagged for a specific chapter as a formatted text block."""
    conn = duckdb.connect(db_path)
    
    rows = conn.execute("""
        SELECT k.knowledge_id, k.knowledge_text, k.knowledge_type, 
               k.source_type, k.original_reference_id, k.confidence_score,
               p.title, p.year
        FROM knowledge k
        JOIN papers p ON k.paper_id = p.paper_id
        JOIN knowledge_chapter_links kcl ON k.knowledge_id = kcl.knowledge_id
        WHERE kcl.chapter_tag = ?
        ORDER BY kcl.relevance_score DESC
    """, [chapter_tag]).fetchall()
    
    conn.close()
    
    output = [f"## Knowledge Block: {chapter_tag}"]
    output.append(f"Total points: {len(rows)}\n")
    
    for kid, text, ktype, stype, orig_ref, conf, title, year in rows:
        output.append(f"[{ktype.upper()}|{stype}] {text}")
        output.append(f"  Source: {orig_ref} (confidence: {conf:.1f})")
        output.append(f"  Paper: {title} ({year})\n")
    
    return "\n".join(output)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--mode", choices=["abstract", "fulltext"], default="abstract")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--dump-stats", action="store_true")
    parser.add_argument("--save-json", help="Save knowledge from a JSON file (agent output) to the database")
    parser.add_argument("--list-pending", action="store_true", help="List papers needing extraction")
    args = parser.parse_args()
    
    if args.dump_stats:
        conn = duckdb.connect(args.db_path)
        total = conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]
        by_type = conn.execute("SELECT knowledge_type, COUNT(*) FROM knowledge GROUP BY knowledge_type ORDER BY COUNT(*) DESC").fetchall()
        by_source = conn.execute("SELECT source_type, COUNT(*) FROM knowledge GROUP BY source_type").fetchall()
        conn.close()
        
        print(f"Total knowledge points: {total}")
        print("\nBy type:")
        for t, c in by_type:
            print(f"  {t}: {c}")
        print("\nBy source:")
        for s, c in by_source:
            print(f"  {s}: {c}")
    
    elif args.save_json:
        with open(args.save_json) as f:
            content = f.read()
        
        # Robust JSON parsing - handle various formats
        data = None
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code blocks
            code_block = re.search(r'```(?:json)?\s*\n(.*?)\n```', content, re.DOTALL)
            if code_block:
                try:
                    data = json.loads(code_block.group(1))
                except json.JSONDecodeError:
                    pass
            
            # Try to find JSON array
            if data is None:
                json_array = re.search(r'\[.*\]', content, re.DOTALL)
                if json_array:
                    try:
                        data = json.loads(json_array.group())
                    except json.JSONDecodeError:
                        pass
            
            # Try to find JSON object
            if data is None:
                json_obj = re.search(r'\{.*\}', content, re.DOTALL)
                if json_obj:
                    try:
                        data = json.loads(json_obj.group())
                    except json.JSONDecodeError:
                        pass
        
        if data is None:
            print(f"❌ Could not parse JSON from {args.save_json}")
            print(f"  File size: {len(content)} chars")
            print(f"  First 200 chars: {content[:200]}")
            sys.exit(1)
        
        # Support various formats:
        # 1. List of knowledge objects with paper_id
        # 2. Dict with {paper_id: ..., knowledge: [...]}
        # 3. List of batch results: [{paper_id: ..., knowledge: [...]}, ...]
        # 4. Single knowledge object
        
        if isinstance(data, list):
            saved_total = 0
            papers_processed = set()
            
            for item in data:
                if isinstance(item, dict):
                    # Check if it's a batch result (has paper_id and knowledge keys)
                    if "knowledge" in item and isinstance(item["knowledge"], list):
                        pid = item.get("paper_id", "unknown")
                        save_knowledge(args.db_path, item["knowledge"], pid, "abstract")
                        saved_total += len(item["knowledge"])
                        papers_processed.add(pid)
                    # Check if it's a single knowledge object with paper_id
                    elif "knowledge_text" in item or "text" in item:
                        pid = item.get("paper_id", "unknown")
                        # Normalize field names
                        if "text" in item and "knowledge_text" not in item:
                            item["knowledge_text"] = item.pop("text")
                        if "type" in item and "knowledge_type" not in item:
                            item["knowledge_type"] = item.pop("type")
                        save_knowledge(args.db_path, [item], pid, "abstract")
                        saved_total += 1
                        papers_processed.add(pid)
            
            print(f"✅ Saved {saved_total} knowledge points from {len(papers_processed)} papers ({args.save_json})")
        
        elif isinstance(data, dict):
            # Check if it's a batch result
            if "knowledge" in data and isinstance(data["knowledge"], list):
                pid = data.get("paper_id", "unknown")
                save_knowledge(args.db_path, data["knowledge"], pid, "abstract")
                print(f"✅ Saved {len(data['knowledge'])} knowledge points for {pid}")
            # Check if it's a single knowledge object
            elif "knowledge_text" in data or "text" in data:
                pid = data.get("paper_id", "unknown")
                if "text" in data and "knowledge_text" not in data:
                    data["knowledge_text"] = data.pop("text")
                if "type" in data and "knowledge_type" not in data:
                    data["knowledge_type"] = data.pop("type")
                save_knowledge(args.db_path, [data], pid, "abstract")
                print(f"✅ Saved 1 knowledge point for {pid}")
            else:
                print(f"❌ Unknown JSON format in {args.save_json}")
                print(f"  Keys: {list(data.keys())}")
                sys.exit(1)
    
    elif args.list_pending:
        papers = get_papers_for_extraction(args.db_path, args.mode, args.limit)
        print(f"Found {len(papers)} papers for extraction ({args.mode} mode)")
        for p in papers:
            print(f"  [{p['paper_id']}] {p['title'][:80]}...")
    
    else:
        papers = get_papers_for_extraction(args.db_path, args.mode, args.limit)
        print(f"Found {len(papers)} papers for extraction ({args.mode} mode)")
        for p in papers:
            print(f"  [{p['paper_id']}] {p['title'][:80]}...")
