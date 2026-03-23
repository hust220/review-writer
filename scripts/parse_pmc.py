"""
PMC Parser: Specifically designed for high-fidelity extraction from PMC HTML.
Identifies sections, paragraphs, and handles references.
"""

import json
import logging
from bs4 import BeautifulSoup
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class PMCSection:
    name: str
    text: str
    level: int = 1

@dataclass
class ParsedPMC:
    paper_id: str
    pmcid: str
    title: str
    abstract: str
    sections: List[PMCSection]
    raw_text: str

    def to_json(self) -> str:
        return json.dumps({
            "paper_id": self.paper_id,
            "pmcid": self.pmcid,
            "title": self.title,
            "abstract": self.abstract,
            "sections": [asdict(s) for s in self.sections],
            "raw_text": self.raw_text
        }, ensure_ascii=False, indent=2)

class PMCParser:
    def parse(self, html_path: str, paper_id: str, pmcid: str) -> ParsedPMC:
        with open(html_path, 'r', encoding='utf-8', errors='ignore') as f:
            soup = BeautifulSoup(f, 'html.parser')
            
        # 1. Basic Metadata
        title_elem = soup.find('h1', class_='content-title') or soup.find('title')
        title = title_elem.get_text(strip=True) if title_elem else "Unknown Title"
        
        # 2. Abstract
        abstract_elem = soup.find('div', class_='abstract') or soup.find('div', id='abs')
        abstract = abstract_elem.get_text(strip=True) if abstract_elem else ""
        
        # 3. Sections
        # PMC uses <div class="tsec"> or <div class="sec"> for sections
        sections = []
        sec_divs = soup.find_all('div', class_=['tsec', 'sec'])
        
        for div in sec_divs:
            heading = div.find(['h2', 'h3', 'h4'])
            if heading:
                sec_name = heading.get_text(strip=True)
                # Filter out the heading text from the div to get just paragraph text
                p_texts = [p.get_text(strip=True) for p in div.find_all('p')]
                sec_text = "\n".join(p_texts)
                
                if sec_text:
                    sections.append(PMCSection(
                        name=sec_name,
                        text=sec_text,
                        level=int(heading.name[1]) - 1
                    ))
        
        # 4. Raw Text fallback
        art_view = soup.find('div', id='pmc-art-view')
        raw_text = art_view.get_text(separator='\n', strip=True) if art_view else soup.get_text(separator='\n', strip=True)
        
        return ParsedPMC(
            paper_id=paper_id,
            pmcid=pmcid,
            title=title,
            abstract=abstract,
            sections=sections,
            raw_text=raw_text
        )

def parse_all_fetched(db_path: Optional[str] = None, output_dir: Optional[str] = None):
    """Parse all papers that have been fetched but not yet parsed."""
    import duckdb
    import os
    
    cwd = os.getcwd()
    db_path = db_path or os.path.join(cwd, "db/review_os_v2.duckdb")
    output_dir = output_dir or os.path.join(cwd, "data/parsed")
    
    conn = duckdb.connect(db_path)
    # Filter for papers fetched but not parsed
    papers = conn.execute("""
        SELECT paper_id, pmcid, fulltext_path 
        FROM papers 
        WHERE fulltext_status = 'fetched' 
        AND fulltext_path LIKE '%.html'
    """).fetchdf().to_dict('records')
    
    parser = PMCParser()
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    success = 0
    for p in papers:
        try:
            parsed = parser.parse(p['fulltext_path'], p['paper_id'], p['pmcid'])
            json_path = out_path / f"{p['paper_id']}.json"
            with open(json_path, 'w', encoding='utf-8') as f:
                f.write(parsed.to_json())
                
            conn.execute("""
                UPDATE papers 
                SET fulltext_path = ?, fulltext_status = 'parsed' 
                WHERE paper_id = ?
            """, [str(json_path), p['paper_id']])
            success += 1
        except Exception as e:
            logger.error(f"Failed to parse {p['paper_id']}: {e}")
            
    conn.close()
    return success

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="db/review_os_v2.duckdb")
    args = parser.parse_args()
    count = parse_all_fetched(args.db)
    print(f"Parsed {count} papers.")
