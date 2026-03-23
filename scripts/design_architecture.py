"""
Architecture Designer: Generates chapter structure from knowledge points.
Reads the knowledge dump and prepares a blueprint for the writing agent.
"""

import os, json, re, sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import duckdb

def get_knowledge_summary(db_path: str) -> str:
    """Generate a knowledge summary grouped by type for the agent."""
    conn = duckdb.connect(db_path)
    
    # Group by knowledge_type
    by_type = conn.execute("""
        SELECT knowledge_type, COUNT(*) as cnt
        FROM knowledge 
        GROUP BY knowledge_type 
        ORDER BY cnt DESC
    """).fetchall()
    
    # Get all knowledge with paper info
    all_kn = conn.execute("""
        SELECT k.knowledge_text, k.knowledge_type, k.source_type, 
               k.original_reference_id, k.paper_id, p.title, p.year, p.citation_count
        FROM knowledge k
        JOIN papers p ON k.paper_id = p.paper_id
        ORDER BY p.citation_count DESC NULLS LAST
    """).fetchall()
    
    conn.close()
    
    lines = ["# Knowledge Base Summary\n"]
    lines.append(f"Total knowledge points: {len(all_kn)}\n")
    lines.append("## Distribution by type:")
    for kt, cnt in by_type:
        lines.append(f"  - {kt}: {cnt}")
    
    lines.append("\n## All Knowledge Points:\n")
    for text, kt, st, orig_ref, pid, title, year, cc in all_kn:
        lines.append(f"[{kt}|{st}] {text[:300]}")
        lines.append(f"  Source: {orig_ref} | {title[:60]} ({year}) | citations: {cc}\n")
    
    return "\n".join(lines)


def save_blueprint(db_path: str, output_dir: str, chapters: list):
    """Save the agent-designed blueprint and update knowledge_chapter_links."""
    os.makedirs(output_dir, exist_ok=True)
    
    blueprint = {
        "chapters": chapters,
        "generated_at": "auto"
    }
    
    with open(os.path.join(output_dir, "blueprint.json"), 'w') as f:
        json.dump(blueprint, f, indent=2)
    
    # Update knowledge_chapter_links
    conn = duckdb.connect(db_path)
    conn.execute("DELETE FROM knowledge_chapter_links")
    
    for ch in chapters:
        ch_num = ch["number"]
        ch_tag = ch["tag"]
        for kid in ch.get("knowledge_ids", []):
            conn.execute("""
                INSERT OR IGNORE INTO knowledge_chapter_links (knowledge_id, chapter_tag, relevance_score)
                VALUES (?, ?, ?)
            """, [kid, ch_tag, 0.8])
    
    conn.close()
    print(f"✅ Blueprint saved: {len(chapters)} chapters → {output_dir}/blueprint.json")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument("--save-blueprint", help="Save blueprint from a JSON file to outputs/blueprint.json and update knowledge_chapter_links")
    args = parser.parse_args()
    
    if args.summary_only:
        summary = get_knowledge_summary(args.db_path)
        print(summary)
    
    elif args.save_blueprint:
        with open(args.save_blueprint) as f:
            data = json.load(f)
        
        # Support both {chapters: [...]} and raw list
        chapters = data.get("chapters", data) if isinstance(data, dict) else data
        
        save_blueprint(args.db_path, args.output_dir, chapters)
        print(f"✅ Blueprint saved with {len(chapters)} chapters")
        for ch in chapters:
            print(f"  Ch{ch['number']}: {ch['title']} ({len(ch.get('knowledge_ids', []))} knowledge points)")
    
    else:
        print("Usage:")
        print("  --summary-only         Dump knowledge summary")
        print("  --save-blueprint FILE  Save blueprint from JSON file")
        print("  --output-dir DIR       Output directory for blueprint.json")
