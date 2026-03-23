"""
Chapter Writer: Sequential chapter generation with knowledge blocks and chaining.
Prepares mega-prompts for the orchestrating agent's Task calls.
"""

import os, json, sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import duckdb

def get_chapter_knowledge(db_path: str, chapter_tag: str) -> str:
    """Get all knowledge points tagged for a specific chapter."""
    conn = duckdb.connect(db_path)
    rows = conn.execute("""
        SELECT k.knowledge_text, k.knowledge_type, k.source_type, 
               k.original_reference_id, k.paper_id, p.title, p.year
        FROM knowledge k
        JOIN papers p ON k.paper_id = p.paper_id
        JOIN knowledge_chapter_links kcl ON k.knowledge_id = kcl.knowledge_id
        WHERE kcl.chapter_tag = ?
        ORDER BY kcl.relevance_score DESC, p.citation_count DESC NULLS LAST
    """, [chapter_tag]).fetchall()
    conn.close()
    
    if not rows:
        return "No knowledge points tagged for this chapter yet."
    
    lines = [f"## Knowledge for Chapter: {chapter_tag} ({len(rows)} points)\n"]
    for text, kt, st, orig_ref, pid, title, year in rows:
        lines.append(f"[{kt}|{st}] {text}")
        lines.append(f"  Source: {orig_ref} | {title[:80]} ({year})\n")
    return "\n".join(lines)


def get_chapter_knowledge_by_ids(db_path: str, knowledge_ids: list) -> str:
    """Get specific knowledge points by IDs."""
    if not knowledge_ids:
        return "No knowledge points provided."
    
    conn = duckdb.connect(db_path)
    placeholders = ','.join(['?'] * len(knowledge_ids))
    rows = conn.execute(f"""
        SELECT k.knowledge_text, k.knowledge_type, k.source_type, 
               k.original_reference_id, k.paper_id, p.title, p.year
        FROM knowledge k
        JOIN papers p ON k.paper_id = p.paper_id
        WHERE k.knowledge_id IN ({placeholders})
        ORDER BY p.citation_count DESC NULLS LAST
    """, knowledge_ids).fetchall()
    conn.close()
    
    lines = [f"## Knowledge Block ({len(rows)} points)\n"]
    for text, kt, st, orig_ref, pid, title, year in rows:
        lines.append(f"[{kt}|{st}] {text}")
        lines.append(f"  Source: {orig_ref} | {title[:80]} ({year})\n")
    return "\n".join(lines)


def build_writing_prompt(chapter_number: int, chapter_title: str, themes: list, 
                         knowledge_block: str, previous_summaries: str, 
                         global_outline: str, total_chapters: int) -> str:
    """Build the mega-prompt for writing a chapter."""
    
    themes_text = "\n".join([f"  {i+1}. {t['title']}: {t['description']}" for i, t in enumerate(themes)])
    
    prompt = f"""Write Chapter {chapter_number} of a Nature Reviews-style paper.
TITLE: "{chapter_title}"
Position: Chapter {chapter_number} of {total_chapters}.

GLOBAL OUTLINE:
{global_outline}

CONTEXT FROM PREVIOUS CHAPTERS:
{previous_summaries if previous_summaries else "This is Chapter 1. Establish the foundational concepts."}

THEMES TO COVER:
{themes_text}

KNOWLEDGE BLOCK (cite as \\cite{{W...}}):
{knowledge_block}

REQUIREMENTS:
- LaTeX format: \\section{{}} for the chapter title, \\subsection{{}} for themes
- Minimum 2000 words (the more the better, aim for 2500-3000)
- Flowing narrative paragraphs, NOT bullet lists
- Each subsection: 3-5 substantial paragraphs that build on each other
- Cite knowledge points as \\cite{{paper_id}} where paper_id is the original_reference_id
- Connect themes with transitional sentences
- Reference concepts from previous chapters where relevant
- Do NOT include \\documentclass or \\begin{{document}}

Begin writing now. Write the complete chapter."""
    
    return prompt


def get_previous_summaries(sections_dir: str, up_to_chapter: int) -> str:
    """Generate a summary of all chapters written so far."""
    summaries = []
    for i in range(1, up_to_chapter):
        sec_path = os.path.join(sections_dir, f"sec{i}.tex")
        if os.path.exists(sec_path):
            with open(sec_path) as f:
                content = f.read()
                # Extract section title
                title_match = re.search(r'\\section\{([^}]+)\}', content)
                title = title_match.group(1) if title_match else f"Chapter {i}"
                
                # Get first 500 chars as summary
                text = re.sub(r'\\[a-zA-Z]+\{[^}]*\}', '', content)
                text = re.sub(r'\\[a-zA-Z]+', '', text)
                summary = text[:800].strip()
                summaries.append(f"Chapter {i} ({title}):\n{summary}...")
    
    return "\n\n".join(summaries)


def get_all_cited_ids(sections_dir: str) -> set:
    """Get all paper IDs already cited in existing chapters."""
    import re
    cited = set()
    if not os.path.exists(sections_dir):
        return cited
    for f in os.listdir(sections_dir):
        if f.endswith('.tex'):
            with open(os.path.join(sections_dir, f)) as file:
                for m in re.findall(r'\\cite\{([^}]+)\}', file.read()):
                    cited.update([i.strip() for i in m.split(',')])
    return cited


def get_uncited_knowledge(db_path: str, cited_ids: set) -> str:
    """Get knowledge points from papers not yet cited."""
    conn = duckdb.connect(db_path)
    placeholders = ','.join(['?'] * len(cited_ids)) if cited_ids else "''"
    if cited_ids:
        rows = conn.execute(f"""
            SELECT k.knowledge_text, k.knowledge_type, k.source_type, 
                   k.original_reference_id, k.paper_id, p.title
            FROM knowledge k
            JOIN papers p ON k.paper_id = p.paper_id
            WHERE k.original_reference_id NOT IN ({placeholders})
            ORDER BY p.citation_count DESC NULLS LAST
            LIMIT 50
        """, list(cited_ids)).fetchall()
    else:
        rows = conn.execute("""
            SELECT k.knowledge_text, k.knowledge_type, k.source_type, 
                   k.original_reference_id, k.paper_id, p.title
            FROM knowledge k
            JOIN papers p ON k.paper_id = p.paper_id
            ORDER BY p.citation_count DESC NULLS LAST
            LIMIT 50
        """).fetchall()
    conn.close()
    
    lines = [f"## Uncited Knowledge ({len(rows)} points available)\n"]
    for text, kt, st, orig_ref, pid, title in rows:
        lines.append(f"[{kt}|{st}] {text[:200]}")
        lines.append(f"  Source: {orig_ref} | {title[:60]}\n")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--sections-dir", required=True)
    parser.add_argument("--chapter", type=int, default=1)
    parser.add_argument("--title", default="")
    parser.add_argument("--tag", default="")
    parser.add_argument("--list-uncited", action="store_true")
    args = parser.parse_args()
    
    if args.list_uncited:
        cited = get_all_cited_ids(args.sections_dir)
        print(get_uncited_knowledge(args.db_path, cited))
    else:
        if args.tag:
            print(get_chapter_knowledge(args.db_path, args.tag))
        else:
            print("Use --tag <chapter_tag> to get knowledge for a chapter, or --list-uncited to see available knowledge.")
