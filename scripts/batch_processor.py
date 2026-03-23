"""
Batch Processor (v14.3) - Bundles extraction/writing tasks for minimal agent intervention.
Creates "bundles" of prompts that the agent processes with a single Task call per bundle.

Usage:
    python3 batch_processor.py --db-path <db> --create-extraction-bundles --bundle-size 15
    python3 batch_processor.py --db-path <db> --create-writing-bundles --blueprint <path>
    python3 batch_processor.py --db-path <db> --process-bundle <bundle_file>
    python3 batch_processor.py --db-path <db> --generate-commands
"""

import os, json, re, sys, hashlib
from pathlib import Path
from typing import List, Dict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duckdb

def create_extraction_bundles(db_path: str, output_dir: str, bundle_size: int = 15):
    """Bundle extraction prompts into groups of bundle_size for batch processing."""
    os.makedirs(output_dir, exist_ok=True)
    
    conn = duckdb.connect(db_path)
    papers = conn.execute("""
        SELECT p.paper_id, p.title, p.abstract, p.year, p.journal, p.referenced_works_json
        FROM papers p
        WHERE p.screening_status = 'include' 
        AND p.abstract IS NOT NULL AND p.abstract != ''
        AND p.paper_id NOT IN (SELECT DISTINCT paper_id FROM knowledge)
        ORDER BY p.citation_count DESC NULLS LAST
    """).fetchall()
    conn.close()
    
    if not papers:
        print("  No papers need extraction.")
        return 0
    
    bundles = []
    for i in range(0, len(papers), bundle_size):
        batch = papers[i:i+bundle_size]
        bundle_papers = []
        for pid, title, abstract, year, journal, ref_json in batch:
            refs = json.loads(ref_json) if ref_json else []
            ref_list = "\n".join([f"  - {r}" for r in refs[:10]])
            bundle_papers.append({
                "paper_id": pid,
                "title": title or "",
                "abstract": abstract or "",
                "year": year,
                "journal": journal or "",
                "references": ref_list
            })
        
        bundle_id = f"bundle_{i//bundle_size + 1:03d}"
        bundle_path = os.path.join(output_dir, f"{bundle_id}.json")
        
        with open(bundle_path, 'w') as f:
            json.dump(bundle_papers, f, indent=2)
        
        bundles.append({
            "id": bundle_id,
            "path": bundle_path,
            "papers": len(batch),
            "first_paper": batch[0][1][:60] if batch[0][1] else "unknown"
        })
    
    # Write manifest
    manifest = {
        "total_papers": len(papers),
        "total_bundles": len(bundles),
        "bundle_size": bundle_size,
        "bundles": bundles
    }
    manifest_path = os.path.join(output_dir, "manifest.json")
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)
    
    print(f"  Created {len(bundles)} extraction bundles ({len(papers)} papers, ~{bundle_size} per bundle)")
    print(f"  Manifest: {manifest_path}")
    return len(bundles)


def create_single_extraction_prompt(papers_bundle: List[Dict]) -> str:
    """Create a single prompt for extracting knowledge from a bundle of papers."""
    papers_text = ""
    for i, p in enumerate(papers_bundle, 1):
        papers_text += f"""
--- PAPER {i} ---
ID: {p['paper_id']}
TITLE: {p['title']}
YEAR: {p['year']}
JOURNAL: {p['journal']}
ABSTRACT:
{p['abstract']}

REFERENCES:
{p['references']}
"""
    
    prompt = f"""You are a scientific knowledge extraction agent. Extract ALL distinct scientific knowledge points from the following {len(papers_bundle)} papers.

For EACH paper, extract 3-10 knowledge points. For EACH knowledge point, output a JSON object with these fields:
- paper_id: The paper's ID
- knowledge_text: The precise scientific statement
- knowledge_type: One of [mechanism, result, method, limitation, structural, design, interaction, finding, hypothesis, comparison]
- source_type: "original" (this paper's new finding) | "referenced" (reporting someone else's work) | "unknown"
- original_reference_id: paper_id if original; reference if referenced; "unknown" if unclear
- confidence_score: 0.0-1.0

Output as a JSON array of objects. Be precise, technical, and avoid redundancy.

PAPERS:
{papers_text}

Output the JSON array now:"""
    return prompt


def create_writing_bundles(db_path: str, blueprint_path: str, sections_dir: str, output_dir: str):
    """Create writing bundles for each chapter."""
    os.makedirs(output_dir, exist_ok=True)
    
    with open(blueprint_path) as f:
        blueprint = json.load(f)
    
    chapters = blueprint.get("chapters", [])
    conn = duckdb.connect(db_path)
    
    bundles = []
    for ch in chapters:
        ch_num = ch["number"]
        ch_title = ch["title"]
        ch_tag = ch.get("tag", "")
        themes = ch.get("themes", [])
        
        # Get knowledge for this chapter
        knowledge_ids = ch.get("knowledge_ids", [])
        
        # Also get knowledge via chapter_tag links
        if ch_tag:
            tagged = conn.execute("""
                SELECT k.knowledge_id, k.knowledge_text, k.knowledge_type, 
                       k.source_type, k.original_reference_id, k.paper_id, p.title, p.year
                FROM knowledge k
                JOIN papers p ON k.paper_id = p.paper_id
                JOIN knowledge_chapter_links kcl ON k.knowledge_id = kcl.knowledge_id
                WHERE kcl.chapter_tag = ?
                ORDER BY kcl.relevance_score DESC, p.citation_count DESC NULLS LAST
            """, [ch_tag]).fetchall()
            for row in tagged:
                if row[0] not in knowledge_ids:
                    knowledge_ids.append(row[0])
        
        # Get knowledge texts
        if knowledge_ids:
            placeholders = ','.join(['?'] * len(knowledge_ids))
            kn_rows = conn.execute(f"""
                SELECT k.knowledge_text, k.knowledge_type, k.source_type, 
                       k.original_reference_id, p.title, p.year
                FROM knowledge k
                JOIN papers p ON k.paper_id = p.paper_id
                WHERE k.knowledge_id IN ({placeholders})
                ORDER BY p.citation_count DESC NULLS LAST
            """, knowledge_ids).fetchall()
        else:
            kn_rows = []
        
        knowledge_block = "\n".join([
            f"[{kt}|{st}] {text}\n  Source: {orig_ref} | {title[:60]} ({year})"
            for text, kt, st, orig_ref, title, year in kn_rows
        ])
        
        # Get previous chapter summaries
        prev_summaries = []
        for prev_num in range(1, ch_num):
            sec_path = os.path.join(sections_dir, f"sec{prev_num}.tex")
            if os.path.exists(sec_path):
                with open(sec_path) as f:
                    content = f.read()
                    title_match = re.search(r'\\section\{([^}]+)\}', content)
                    prev_title = title_match.group(1) if title_match else f"Chapter {prev_num}"
                    clean = re.sub(r'\\[a-zA-Z]+\{[^}]*\}', '', content)
                    clean = re.sub(r'\\[a-zA-Z]+', '', clean)
                    prev_summaries.append(f"Chapter {prev_num} ({prev_title}): {clean[:500]}...")
        
        themes_text = "\n".join([
            f"  {i+1}. {t.get('title', 'Untitled')}: {t.get('description', '')}"
            for i, t in enumerate(themes)
        ])
        
        global_outline = "\n".join([
            f"  Ch{c['number']}: {c['title']}" for c in chapters
        ])
        
        bundle = {
            "chapter_number": ch_num,
            "chapter_title": ch_title,
            "chapter_tag": ch_tag,
            "themes": themes_text,
            "knowledge_block": knowledge_block,
            "knowledge_count": len(kn_rows),
            "previous_summaries": "\n\n".join(prev_summaries),
            "global_outline": global_outline,
            "total_chapters": len(chapters)
        }
        
        bundle_path = os.path.join(output_dir, f"chapter_{ch_num}.json")
        with open(bundle_path, 'w') as f:
            json.dump(bundle, f, indent=2)
        
        bundles.append({"number": ch_num, "title": ch_title, "path": bundle_path})
    
    conn.close()
    
    # Write manifest
    manifest = {
        "total_chapters": len(chapters),
        "bundles": bundles
    }
    with open(os.path.join(output_dir, "manifest.json"), 'w') as f:
        json.dump(manifest, f, indent=2)
    
    print(f"  Created {len(bundles)} writing bundles")
    return len(bundles)


def generate_writing_prompt(bundle: Dict) -> str:
    """Generate the mega-prompt for writing a chapter."""
    return f"""Write Chapter {bundle['chapter_number']} of a Nature Reviews-style paper.

TITLE: "{bundle['chapter_title']}"
Position: Chapter {bundle['chapter_number']} of {bundle['total_chapters']}.

GLOBAL OUTLINE:
{bundle['global_outline']}

CONTEXT FROM PREVIOUS CHAPTERS:
{bundle['previous_summaries'] if bundle['previous_summaries'] else "This is Chapter 1. Establish the foundational concepts."}

THEMES TO COVER:
{bundle['themes']}

KNOWLEDGE BLOCK ({bundle['knowledge_count']} points - cite as \\cite{{W...}} or \\cite{{paper_id}}):
{bundle['knowledge_block']}

REQUIREMENTS:
- LaTeX format: \\section{{}} for the chapter, \\subsection{{}} for each theme
- MINIMUM 2000 words (aim for 2500-3000)
- Flowing narrative paragraphs, NOT bullet lists or numbered lists
- Each subsection: 3-5 substantial paragraphs that build logically
- Cite knowledge points using \\cite{{original_reference_id}}
- Connect themes with transitional sentences
- Reference concepts from previous chapters where relevant
- Do NOT include \\documentclass or \\begin{{document}}

Write the COMPLETE chapter now."""


def generate_commands(workspace: str):
    """Generate the agent_commands.sh file with step-by-step instructions."""
    db_path = os.path.join(workspace, "db", "review.duckdb")
    data_dir = os.path.join(workspace, "data")
    outputs_dir = os.path.join(workspace, "outputs")
    sections_dir = os.path.join(outputs_dir, "sections")
    extraction_dir = os.path.join(data_dir, "extraction_bundles")
    writing_dir = os.path.join(data_dir, "writing_bundles")
    
    conn = duckdb.connect(db_path)
    papers = conn.execute("SELECT COUNT(*) FROM papers WHERE screening_status='include' AND abstract IS NOT NULL AND abstract != ''").fetchone()[0]
    knowledge = conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]
    chapters = len([f for f in os.listdir(sections_dir) if re.match(r'sec\d+\.tex', f)]) if os.path.exists(sections_dir) else 0
    conn.close()
    
    blueprint_path = os.path.join(outputs_dir, "blueprint.json")
    has_blueprint = os.path.exists(blueprint_path)
    
    lines = ["#!/bin/bash", "# Auto-generated agent commands for review pipeline", f"# Workspace: {workspace}", ""]
    
    # Determine current state
    if knowledge < papers * 2:
        # Need extraction
        n_bundles = create_extraction_bundles(db_path, extraction_dir, bundle_size=10)
        lines.append(f"# STEP 3: Extract knowledge ({papers - knowledge//3} papers remaining, {n_bundles} bundles)")
        lines.append(f"# Process each bundle by reading the JSON and sending to Task agent")
        lines.append(f"echo 'Extraction bundles in: {extraction_dir}'")
        lines.append(f"echo 'Process each bundle_X.json file with a Task call'")
        lines.append("")
    elif not has_blueprint:
        # Need architecture
        lines.append("# STEP 4: Design architecture")
        lines.append(f"python3 scripts/design_architecture.py --db-path {db_path} --output-dir {outputs_dir} --summary-only")
        lines.append(f"echo 'Read the knowledge summary above and design chapter structure'")
        lines.append(f"echo 'Save blueprint to {blueprint_path}'")
        lines.append("")
    elif chapters < len(json.load(open(blueprint_path)).get("chapters", [])):
        # Need writing
        remaining = [ch for ch in json.load(open(blueprint_path)).get("chapters", []) 
                     if ch["number"] > chapters]
        n_bundles = create_writing_bundles(db_path, blueprint_path, sections_dir, writing_dir)
        lines.append(f"# STEP 5: Write chapters ({len(remaining)} remaining, {n_bundles} bundles)")
        lines.append(f"echo 'Writing bundles in: {writing_dir}'")
        lines.append(f"echo 'Process each chapter_X.json file with a Task call'")
        lines.append("")
    else:
        # Need render
        lines.append("# STEP 6: Render")
        lines.append(f"python3 scripts/pipeline_runner.py --next")
        lines.append("")
    
    cmd_path = os.path.join(workspace, "agent_commands.sh")
    with open(cmd_path, 'w') as f:
        f.write("\n".join(lines))
    os.chmod(cmd_path, 0o755)
    
    print(f"  Generated: {cmd_path}")
    return cmd_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--type", choices=["extract", "write"], help="Type of batch operation")
    parser.add_argument("--bundle", help="Process a specific bundle file and output the prompt")
    parser.add_argument("--create-extraction-bundles", action="store_true")
    parser.add_argument("--create-writing-bundles", action="store_true")
    parser.add_argument("--blueprint", help="Path to blueprint.json")
    parser.add_argument("--sections-dir", help="Path to sections directory")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--bundle-size", type=int, default=10)
    parser.add_argument("--generate-commands", action="store_true")
    parser.add_argument("--workspace", help="Workspace path for generate-commands")
    args = parser.parse_args()
    
    if args.type == "extract" and args.bundle:
        # Read bundle and output the extraction prompt
        with open(args.bundle) as f:
            papers = json.load(f)
        prompt = create_single_extraction_prompt(papers)
        print(prompt)
    
    elif args.type == "write" and args.bundle:
        # Read writing bundle and output the writing prompt
        with open(args.bundle) as f:
            bundle = json.load(f)
        prompt = generate_writing_prompt(bundle)
        print(prompt)
    
    elif args.create_extraction_bundles:
        create_extraction_bundles(args.db_path, args.output_dir, args.bundle_size)
    
    elif args.create_writing_bundles:
        create_writing_bundles(args.db_path, args.blueprint, args.sections_dir, args.output_dir)
    
    elif args.generate_commands:
        generate_commands(args.workspace)
    
    else:
        parser.print_help()
        print("\nExamples:")
        print("  Create extraction bundles:")
        print("    python3 batch_processor.py --db-path <db> --create-extraction-bundles --output-dir <dir>")
        print("  Process a bundle (output prompt):")
        print("    python3 batch_processor.py --db-path <db> --type extract --bundle <file> --output-dir <dir>")
        print("  Create writing bundles:")
        print("    python3 batch_processor.py --db-path <db> --create-writing-bundles --blueprint <file> --sections-dir <dir> --output-dir <dir>")
