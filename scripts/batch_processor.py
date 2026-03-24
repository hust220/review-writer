"""
Batch Processor (v16.1) - Bundles extraction/writing tasks for minimal agent intervention.
Creates "bundles" of prompts that the agent processes with a single Task call per bundle.

Usage:
    python3 batch_processor.py --db-path <db> --create-extraction-bundles --bundle-size 15
    python3 batch_processor.py --db-path <db> --create-writing-bundles --blueprint <path>
    python3 batch_processor.py --db-path <db> --process-bundle <bundle_file>
    python3 batch_processor.py --db-path <db> --generate-commands
"""

import os, json, re, sys, hashlib

# Fix UTF-8 encoding for Windows
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass
from pathlib import Path
from typing import List, Dict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duckdb
from pipeline_state import get_next_action, load_workspace_state, recommended_command

def create_extraction_bundles(db_path: str, output_dir: str, bundle_size: int = 15):
    """Bundle extraction prompts into groups of bundle_size for batch processing."""
    os.makedirs(output_dir, exist_ok=True)
    
    conn = duckdb.connect(db_path)
    papers = conn.execute("""
        SELECT p.paper_id, p.title, p.abstract, p.year, p.journal, 
               p.referenced_works_json, p.fulltext_status, p.fulltext_path
        FROM papers p
        WHERE p.screening_status = 'include' 
        AND (p.abstract IS NOT NULL AND p.abstract != '' OR p.fulltext_status = 'fetched')
        AND p.paper_id NOT IN (SELECT DISTINCT paper_id FROM summaries)
        ORDER BY p.fulltext_status DESC, p.citation_count DESC NULLS LAST
    """).fetchall()
    conn.close()
    
    if not papers:
        print("  No papers need extraction.")
        return 0
    
    bundles = []
    for i in range(0, len(papers), bundle_size):
        batch = papers[i:i+bundle_size]
        bundle_papers = []
        for pid, title, abstract, year, journal, ref_json, ft_status, ft_path in batch:
            refs = json.loads(ref_json) if ref_json else []
            ref_list = "\n".join([f"  - {r}" for r in refs[:10]])
            
            # Get full text excerpt if available
            fulltext_excerpt = ""
            if ft_status == 'fetched' and ft_path and os.path.exists(ft_path):
                try:
                    if ft_path.endswith('.html'):
                        with open(ft_path, 'r', encoding='utf-8') as f:
                            raw_html = f.read()
                        clean = re.sub(r'<[^>]+>', ' ', raw_html)
                        clean = re.sub(r'\s+', ' ', clean)
                        fulltext_excerpt = clean[:8000]  # First 8000 chars
                    elif ft_path.endswith('.pdf'):
                        try:
                            from universal_parser import parse_everything
                            parsed = parse_everything(ft_path)
                            fulltext_excerpt = parsed.get('text', '')[:8000]
                        except:
                            pass  # PDF parsing failed
                except:
                    pass  # Full text read failed
            
            bundle_papers.append({
                "paper_id": pid,
                "title": title or "",
                "abstract": abstract or "",
                "year": year,
                "journal": journal or "",
                "references": ref_list,
                "has_fulltext": ft_status == 'fetched',
                "fulltext_excerpt": fulltext_excerpt
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
    """Create a single prompt for extracting structured summaries from a bundle of papers."""
    papers_text = ""
    for i, p in enumerate(papers_bundle, 1):
        content_section = ""
        if p.get('fulltext_excerpt'):
            content_section = f"""
FULL TEXT EXCERPT (first 8000 chars):
{p['fulltext_excerpt']}

ABSTRACT (for reference):
{p['abstract']}"""
        else:
            content_section = f"""
ABSTRACT:
{p['abstract']}"""
        
        papers_text += f"""
--- PAPER {i} ---
ID: {p['paper_id']}
TITLE: {p['title']}
YEAR: {p['year']}
JOURNAL: {p['journal']}
{content_section}

REFERENCES:
{p['references']}
"""
    
    prompt = f"""You are a scientific research assistant. Extract structured summaries from the following {len(papers_bundle)} papers for use in writing a literature review.

For EACH paper, provide a summary consisting of three distinct parts:

1. BACKGROUND KNOWLEDGE summary: 
   - Focus on foundational concepts and existing research context mentioned in the Introduction. 
   - IMPORTANT: When mentioning previous work, indicate the citation by putting the title of the cited paper in parentheses, e.g., (Original Title of Cited Work).

2. PAPER CONTRIBUTION summary:
   - Clearly explain what THIS specific paper did.
   - Include: key experiments, important parameters, major results, significant data points, and the overall significance/impact of the findings.

3. IMPORTANT REFERENCES:
   - Identify 2-5 papers cited in this work that seem foundational or highly relevant to the review topic.
   - Provide the EXACT TITLE of the cited paper and a brief reason why it should be reviewed.

Output as a JSON array of objects, one for each paper:
- paper_id: The paper's ID
- background_summary: The background knowledge summary string.
- contribution_summary: The paper contribution summary string.
- important_references: A list of objects with "title" and "reason"

Be precise, technical, and ensure all important scientific data and context are captured.

PAPERS:
{papers_text}

Output the JSON array now:"""
    return prompt
    return prompt


def create_screening_prompt(bundle: List[Dict]) -> str:
    """Generate a prompt for LLM-based abstract screening."""
    prompt = f"""You are a senior scientific editor. Review the following {len(bundle)} papers and decide if they should be included in a systematic review.

The review topic is: {bundle[0].get('topic', 'Search Results')}

For EACH paper, output:
1. "decision": "include" (highly relevant and high quality), "exclude" (out of scope, low quality, or duplicate), or "maybe" (potentially relevant but abstract is vague).
2. "reason": A one-sentence explanation for the decision.
3. "needs_fulltext": true/false. Set to true if this paper likely contains key experimental data, protocols, or foundational theory that requires deep reading of the full text.

JSON output format:
[
  {{
    "paper_id": "...",
    "title": "...",
    "decision": "include",
    "reason": "...",
    "needs_fulltext": true
  }}
]

PAPERS TO SCREEN:
"""
    for p in bundle:
        prompt += f"\nID: {p['paper_id']}\nTITLE: {p['title']}\nABSTRACT: {p.get('abstract', 'No abstract available')}\n---\n"
    
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
        
        # Get summaries for this chapter via paper_chapter_links
        if ch_tag:
            summary_rows = conn.execute("""
                SELECT s.background_summary, s.contribution_summary, p.title, p.year, p.paper_id, p.abstract
                FROM summaries s
                JOIN papers p ON s.paper_id = p.paper_id
                JOIN paper_chapter_links pcl ON s.paper_id = pcl.paper_id
                WHERE pcl.chapter_tag = ?
                ORDER BY pcl.relevance_score DESC, p.citation_count DESC NULLS LAST
            """, [ch_tag]).fetchall()
        else:
            summary_rows = []
        
        summary_block = ""
        for idx, row in enumerate(summary_rows, 1):
            bg, contrib, title, year, pid, abstract = row
            summary_block += (
                f"\n--- PAPER {idx}: {title} ({year}) [ID: {pid}] ---\n"
                f"ABSTRACT: {abstract[:500]}...\n"
                f"BACKGROUND KNOWLEDGE: {bg}\n"
                f"CONTRIBUTION: {contrib}\n"
                f"CITE_AS: \\cite{{{pid}}}\n"
            )
        
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
            "summary_block": summary_block,
            "paper_count": len(summary_rows),
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
    """Generate the mega-prompt for writing a chapter based on paper summaries."""
    paper_count = int(bundle['paper_count'])
    summary_block = bundle['summary_block']
    
    source_instruction = ""
    if paper_count > 0:
        source_instruction = summary_block
    else:
        source_instruction = (
            "⚠️ NO PAPERS linked to this chapter in the database. "
            "You MUST perform a search for relevant papers in the workspace "
            "BEFORE writing."
        )

    return f"""Write Chapter {bundle['chapter_number']} of a high-impact Nature Reviews-style paper.

TITLE: "{bundle['chapter_title']}"
POSITION: Chapter {bundle['chapter_number']} of {bundle['total_chapters']}.

GLOBAL OUTLINE:
{bundle['global_outline']}

CONTEXT FROM PREVIOUS CHAPTERS:
{bundle['previous_summaries'] if bundle['previous_summaries'] else "This is Chapter 1. Establish the foundational concepts and the scope of the review."}

PAPER SUMMARIES AND ABSTRACTS ({paper_count} papers - SYNTHESIZE THESE):
{source_instruction}

═══════════════════════════════════════════════
WRITING INSTRUCTIONS (FOLLOW STRICTLY):
═══════════════════════════════════════════════

1. STRUCTURE: Write ONE \\section{{}} with the chapter title. Then create 3-5 \\subsection{{}} 
   that THEMATICALLY GROUP the knowledge points. DO NOT create one subsection per knowledge point.
   YOU decide the subsection titles based on the themes you see in the knowledge block.

2. CITATION FORMAT (CRITICAL):
   Each paper in the block above shows a CITE_AS field (e.g., \\cite{{paper_id}}). 
   When you use information from a paper, you MUST include its CITE_AS in your sentence.
   Example: "Prior studies have established that... \\cite{{W1234567890}}."
   Every paragraph must contain at least 2-3 citations. NO unsourced claims.

3. DEPTH: Minimum 2500 words. Aim for 3000+ words.
   Each subsection: 5-8 substantial paragraphs.

4. NARRATIVE: Flowing academic prose. No bullet points or lists.
   Use transitional sentences between subsections.

5. SYNTHESIS: Compare and contrast findings from different papers.
   Show how knowledge points relate to each other.

6. LATEX: Only use \\section{{}}, \\subsection{{}}, \\cite{{}}. 
   No \\documentclass, \\begin{{document}}, or \\bibliography.

Begin writing the complete chapter now."""


def generate_commands(workspace: str):
    """Generate the agent_commands.sh file with step-by-step instructions."""
    db_path = os.path.join(workspace, "db", "review.duckdb")
    state = load_workspace_state(db_path)
    next_action = get_next_action(state)
    lines = ["#!/bin/bash", "# Auto-generated agent commands for review pipeline", f"# Workspace: {workspace}", ""]
    lines.append(f"# Next action: {next_action}")
    lines.append(recommended_command(next_action, state))
    
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
    parser.add_argument("--output-dir")
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
        if not args.output_dir:
            parser.error("--output-dir is required with --create-extraction-bundles")
        create_extraction_bundles(args.db_path, args.output_dir, args.bundle_size)
    
    elif args.create_writing_bundles:
        if not args.output_dir:
            parser.error("--output-dir is required with --create-writing-bundles")
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
