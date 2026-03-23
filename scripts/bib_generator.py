"""
BibTeX Generator (Iron-Clad v9.0): Auto-reconciles citations from source text.
Ensures stability by stripping newlines, control chars, and non-ASCII from metadata.
"""

import duckdb
import os
import re
import json
import logging
import requests
from typing import List, Set, Dict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def clean_for_bibtex(text: str) -> str:
    """Rigorous cleaning for BibTeX value fields."""
    if not text: return ""
    # 1. Remove literal and escaped control chars
    text = text.replace('\\n', ' ').replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
    # 2. Escape LaTeX special chars
    replacements = {'&': '\\&', '%': '\\%', '$': '\\$', '#': '\\#', '_': '\\_'}
    for char, rep in replacements.items():
        text = text.replace(char, rep)
    # 3. Strip non-ASCII
    return "".join([c if ord(c) < 128 else "" for c in text])

def extract_citations_from_tex(sections_dir: str) -> Set[str]:
    citations = set()
    cite_pattern = re.compile(r'\\cite\{([^}]+)\}')
    if not os.path.exists(sections_dir): return citations
    for root, _, files in os.walk(sections_dir):
        for file in files:
            if file.endswith('.tex'):
                with open(os.path.join(root, file), 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                    matches = cite_pattern.findall(content)
                    for match in matches:
                        ids = [i.strip() for i in match.split(',')]
                        citations.update(ids)
    return citations

def generate_hardened_bib(db_path: str, sections_dir: str, output_path: str):
    cited_ids = extract_citations_from_tex(sections_dir)
    if not cited_ids: return

    conn = duckdb.connect(db_path)
    bib_entries = []
    
    for paper_id in cited_ids:
        paper = conn.execute("SELECT * FROM papers WHERE paper_id = ?", [paper_id]).fetchdf().to_dict('records')
        data = paper[0] if paper else None
        
        if not data:
            # Emergency API fetch
            try:
                url = f"https://api.openalex.org/works/{paper_id}?mailto=jianopt@gmail.com"
                res = requests.get(url, timeout=10).json()
                bib = res.get('biblio', {})
                # Multi-path fallback for journal name (OpenAlex v2 compatibility)
                primary_loc = res.get('primary_location') or {}
                source = primary_loc.get('source') or {}
                journal_name = source.get('display_name')
                
                if not journal_name:
                    # Fallback 2: Check locations array
                    locs = res.get('locations', [])
                    for loc in locs:
                        s = loc.get('source') or {}
                        if s.get('display_name'):
                            journal_name = s.get('display_name')
                            break
                
                if not journal_name:
                    # Fallback 3: Old host_venue compatibility
                    journal_name = res.get('host_venue', {}).get('display_name', 'Unknown')

                data = {
                    'paper_id': paper_id,
                    'doi': (res.get('doi') or '').replace("https://doi.org/", ""),
                    'title': res.get('title', 'Unknown'),
                    'year': res.get('publication_year', ''),
                    'journal': journal_name,
                    'volume': bib.get('volume', ''),
                    'issue': bib.get('issue', ''),
                    'pages': f"{bib.get('first_page','')}-{bib.get('last_page','')}",
                    'authors_json': json.dumps([{'name': a.get('author',{}).get('display_name')} for a in res.get('authorships', [])])
                }
            except: continue

        if data:
            title = clean_for_bibtex(data['title'])
            journal = clean_for_bibtex(data.get('journal', 'Unknown'))
            author_str = "Unknown"
            if data.get('authors_json'):
                try:
                    authors = json.loads(data['authors_json'])
                    author_str = " and ".join([clean_for_bibtex(a.get('name', '')) for a in authors if a.get('name')])
                except: pass

            entry = f"@article{{{data['paper_id']},\n"
            entry += f"  author = {{{author_str}}},\n"
            entry += f"  title = {{{{{title}}}}},\n"
            entry += f"  journal = {{{journal}}},\n"
            entry += f"  year = {{{data['year']}}},\n"
            if data.get('volume'): entry += f"  volume = {{{data['volume']}}},\n"
            if data.get('pages'): entry += f"  pages = {{{data['pages']}}},\n"
            if data.get('doi'): entry += f"  doi = {{{data['doi']}}},\n"
            entry += f"  url = {{https://doi.org/{data.get('doi','')}}}\n"
            entry += "}"
            bib_entries.append(entry)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("\n\n".join(bib_entries))
    conn.close()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--sections", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    generate_hardened_bib(args.db, args.sections, args.output)
