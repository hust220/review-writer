"""
Core PMC Module for UniversalReviewer.
Handles DOI/PMID to PMCID resolution, HTML fetching (via OA or Proxy), and high-fidelity parsing.
"""

import os
import json
import time
import logging
import requests
import re
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Any
from dataclasses import dataclass, asdict
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

NCBI_ID_CONV_URL = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"

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

def resolve_to_pmcid(ids: List[str], email: str = "jianopt@gmail.com") -> Dict[str, str]:
    """Resolve DOIs or PMIDs to PMCIDs."""
    if not ids: return {}
    results = {}
    batch_size = 100
    for i in range(0, len(ids), batch_size):
        batch = ids[i:i+batch_size]
        params = {"tool": "ReviewOS", "email": email, "ids": ",".join(batch), "format": "json"}
        try:
            response = requests.get(NCBI_ID_CONV_URL, params=params, timeout=15)
            if response.status_code == 200:
                data = response.json()
                for record in data.get("records", []):
                    original_id = record.get("doi") or record.get("pmid")
                    pmcid = record.get("pmcid")
                    if original_id and pmcid:
                        results[original_id] = pmcid
        except Exception as e:
            logger.error(f"Failed to resolve IDs: {e}")
        time.sleep(0.4)
    return results

def is_valid_pmc_content(html: str) -> bool:
    """Verify that the HTML is a valid PMC article page."""
    if not html: return False
    html_lower = html.lower()
    markers = ["<article", "pmc-sidebar", "abstract", "id=\"pmc-art-view\"", "ncbi.nlm.nih.gov"]
    matches = sum(1 for m in markers if m in html_lower)
    return matches >= 2 and len(html) > 10000

class PMCFetcher:
    COOKIE_PATH = os.path.expanduser("~/.opencode/data/.uva_cookies.json")
    LOGIN_URL = "https://proxy1.library.virginia.edu/login?url=https://www.ncbi.nlm.nih.gov/pmc/"
    
    def __init__(self, data_dir: Optional[str] = None, headless: bool = True):
        self.data_dir = Path(data_dir or os.path.join(os.getcwd(), "data/fulltext"))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    def start(self):
        if self.browser: return
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=self.headless)
        if os.path.exists(self.COOKIE_PATH):
            try:
                with open(self.COOKIE_PATH, 'r') as f:
                    storage_state = json.load(f)
                self.context = self.browser.new_context(storage_state=storage_state)
            except:
                self.context = self.browser.new_context()
        else:
            self.context = self.browser.new_context()
        self.page = self.context.new_page()
        Stealth().apply_stealth_sync(self.page)

    def stop(self):
        if self.context: self.context.close()
        if self.browser: self.browser.close()
        if self.playwright: self.playwright.stop()
        self.browser = None

    def fetch_article(self, pmcid: str) -> Tuple[Optional[str], str]:
        if not self.page: self.start()
        pmcid_full = pmcid if pmcid.startswith("PMC") else f"PMC{pmcid}"
        output_path = self.data_dir / f"{pmcid_full}.html"
        
        # Try OA first
        oa_url = f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid_full}/"
        try:
            self.page.goto(oa_url, timeout=30000)
            html = self.page.content()
            if is_valid_pmc_content(html):
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(html)
                return str(output_path), "pmc_oa"
        except: pass

        # Try Proxy
        proxied_url = f"https://www-ncbi-nlm-nih-gov.proxy1.library.virginia.edu/pmc/articles/{pmcid_full}/"
        try:
            self.page.goto(proxied_url, timeout=45000)
            self.page.wait_for_selector("#pmc-art-view", timeout=15000)
            html = self.page.content()
            if is_valid_pmc_content(html):
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(html)
                return str(output_path), "pmc_proxy"
        except: pass
        return None, "none"

class PMCParser:
    def parse(self, html_path: str, paper_id: str, pmcid: str) -> ParsedPMC:
        with open(html_path, 'r', encoding='utf-8', errors='ignore') as f:
            soup = BeautifulSoup(f, 'html.parser')
        
        title_elem = soup.find('h1', class_='content-title') or soup.find('title')
        title = title_elem.get_text(strip=True) if title_elem else "Unknown Title"
        
        abstract_elem = soup.find('div', class_='abstract') or soup.find('div', id='abs')
        abstract = abstract_elem.get_text(strip=True) if abstract_elem else ""
        
        sections = []
        sec_divs = soup.find_all('div', class_=['tsec', 'sec'])
        for div in sec_divs:
            heading = div.find(['h2', 'h3', 'h4'])
            if heading:
                sec_name = heading.get_text(strip=True)
                p_texts = [p.get_text(strip=True) for p in div.find_all('p')]
                sec_text = "\n".join(p_texts)
                if sec_text:
                    sections.append(PMCSection(name=sec_name, text=sec_text, level=int(heading.name[1]) - 1))
        
        art_view = soup.find('div', id='pmc-art-view')
        raw_text = art_view.get_text(separator='\n', strip=True) if art_view else soup.get_text(separator='\n', strip=True)
        
        return ParsedPMC(paper_id=paper_id, pmcid=pmcid, title=title, abstract=abstract, sections=sections, raw_text=raw_text)
