"""
Universal Parser: Handles both PMC HTML and standard PDF files.
Standardizes output for the Extractor Agent.
"""

import os
import json
import logging
import fitz  # PyMuPDF
from bs4 import BeautifulSoup
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class Section:
    name: str
    text: str
    level: int = 1

@dataclass
class ParsedPaper:
    paper_id: str
    title: str
    abstract: str
    sections: List[Section]
    raw_text: str

    def to_json(self) -> str:
        return json.dumps({
            "paper_id": self.paper_id,
            "title": self.title,
            "abstract": self.abstract,
            "sections": [asdict(s) for s in self.sections],
            "raw_text": self.raw_text
        }, ensure_ascii=False, indent=2)

class HTMLParser:
    def parse(self, html_path: str, paper_id: str) -> ParsedPaper:
        with open(html_path, 'r', encoding='utf-8', errors='ignore') as f:
            soup = BeautifulSoup(f, 'html.parser')
        
        # PMC detection
        is_pmc = soup.find('div', id='pmc-art-view') is not None
        
        if is_pmc:
            title_elem = soup.find('h1', class_='content-title') or soup.find('title')
            abstract_elem = soup.find('div', class_='abstract') or soup.find('div', id='abs')
            sec_divs = soup.find_all('div', class_=['tsec', 'sec'])
            
            sections = []
            for div in sec_divs:
                heading = div.find(['h2', 'h3', 'h4'])
                if heading:
                    p_texts = [p.get_text(strip=True) for p in div.find_all('p')]
                    sections.append(Section(name=heading.get_text(strip=True), text="\n".join(p_texts)))
            
            return ParsedPaper(
                paper_id=paper_id,
                title=title_elem.get_text(strip=True) if title_elem else "",
                abstract=abstract_elem.get_text(strip=True) if abstract_elem else "",
                sections=sections,
                raw_text=soup.get_text(separator='\n', strip=True)
            )
        else:
            # Generic HTML
            return ParsedPaper(
                paper_id=paper_id,
                title=soup.title.string if soup.title else "",
                abstract="",
                sections=[],
                raw_text=soup.get_text(separator='\n', strip=True)
            )

class PDFParser:
    def parse(self, pdf_path: str, paper_id: str) -> ParsedPaper:
        doc = fitz.open(pdf_path)
        full_text = ""
        for page in doc:
            full_text += page.get_text() + "\n\n"
        
        # Simple heuristic for abstract
        abstract = ""
        if "Abstract" in full_text:
            try:
                abstract = full_text.split("Abstract")[1].split("Introduction")[0].strip()
            except: pass
            
        return ParsedPaper(
            paper_id=paper_id,
            title="", # Difficult to extract from PDF without ML
            abstract=abstract,
            sections=[], # Flat structure for PDF in this simple version
            raw_text=full_text
        )

def parse_everything(db_path: str, output_dir: str = "data/parsed"):
    import duckdb
    conn = duckdb.connect(db_path)
    papers = conn.execute("""
        SELECT paper_id, fulltext_path 
        FROM papers 
        WHERE fulltext_status = 'fetched'
    """).fetchdf().to_dict('records')
    
    html_parser = HTMLParser()
    pdf_parser = PDFParser()
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    success = 0
    for p in papers:
        file_path = p['fulltext_path']
        paper_id = p['paper_id']
        
        try:
            if file_path.endswith('.html'):
                parsed = html_parser.parse(file_path, paper_id)
            elif file_path.endswith('.pdf'):
                parsed = pdf_parser.parse(file_path, paper_id)
            else:
                continue
                
            json_path = out_path / f"{paper_id}.json"
            with open(json_path, 'w', encoding='utf-8') as f:
                f.write(parsed.to_json())
                
            conn.execute("UPDATE papers SET fulltext_path = ?, fulltext_status = 'parsed' WHERE paper_id = ?", 
                         [str(json_path), paper_id])
            success += 1
        except Exception as e:
            logger.error(f"Failed to parse {paper_id}: {e}")
            
    conn.close()
    return success
