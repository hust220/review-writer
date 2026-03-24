"""
Core Search and Metadata Module for UniversalReviewer.
Handles OpenAlex search and Unpaywall metadata retrieval.
"""

import requests
import logging
import time
import os
import sys
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

# Fix UTF-8 encoding for Windows
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@dataclass
class OAResult:
    url: str
    format: str # 'pdf' or 'html'
    source: str # 'unpaywall' or 'openalex'

class OpenAlexSearcher:
    BASE_URL = "https://api.openalex.org/works"
    
    def __init__(self, email: str = "jianopt@gmail.com"):
        self.email = email

    def search(self, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Search OpenAlex using the filter syntax."""
        params = {
            "filter": f"title_and_abstract.search:{query},type:article|preprint",
            "per_page": limit,
            "sort": "cited_by_count:desc",
            "mailto": self.email
        }
        try:
            resp = requests.get(self.BASE_URL, params=params, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                return data.get('results', [])
            else:
                logger.error(f"OpenAlex search failed with status {resp.status_code}")
        except Exception as e:
            logger.error(f"OpenAlex request error: {e}")
        return []

    def reconstruct_abstract(self, inverted_index: Optional[Dict]) -> str:
        """Reconstruct abstract from inverted index."""
        if not inverted_index:
            return ""
        try:
            word_positions = []
            for word, positions in inverted_index.items():
                for pos in positions:
                    word_positions.append((pos, word))
            word_positions.sort()
            return ' '.join([w[1] for w in word_positions])
        except Exception:
            return ""

    def get_works_by_ids(self, ids: List[str]) -> List[Dict[str, Any]]:
        """Fetch metadata for multiple OpenAlex IDs."""
        if not ids: return []
        
        all_results = []
        batch_size = 50
        for i in range(0, len(ids), batch_size):
            batch = ids[i:i+batch_size]
            id_query = "|".join(batch)
            params = {
                "filter": f"openalex:{id_query}",
                "per_page": len(batch),
                "mailto": self.email
            }
            try:
                time.sleep(0.5)
                resp = requests.get(self.BASE_URL, params=params, timeout=30)
                if resp.status_code == 200:
                    all_results.extend(resp.json().get('results', []))
            except Exception as e:
                logger.error(f"Error fetching batch of IDs: {e}")
        return all_results

    def extract_biblio(self, work: Dict) -> Dict[str, str]:
        """Extract bibliographic details."""
        bib = work.get('biblio', {})
        return {
            'volume': bib.get('volume', ''),
            'issue': bib.get('issue', ''),
            'pages': f"{bib.get('first_page', '')}-{bib.get('last_page', '')}" if bib.get('first_page') else ''
        }

def get_oa_link(doi: str, email: str = "jianopt@gmail.com") -> Optional[OAResult]:
    """Check Unpaywall for the best OA link."""
    if not doi: return None
    
    url = f"https://api.unpaywall.org/v2/{doi}?email={email}"
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("is_oa"):
                best_loc = data.get("best_oa_location", {})
                pdf_url = best_loc.get("url_for_pdf")
                html_url = best_loc.get("url_for_landing_page")
                
                if pdf_url:
                    return OAResult(url=pdf_url, format='pdf', source='unpaywall')
                elif html_url:
                    return OAResult(url=html_url, format='html', source='unpaywall')
    except Exception as e:
        logger.error(f"Unpaywall lookup failed for {doi}: {e}")
    
    return None

def download_file(url: str, output_path: str) -> bool:
    """Download a file from a URL."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
        resp = requests.get(url, headers=headers, timeout=30, stream=True)
        if resp.status_code == 200:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            with open(output_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            return True
    except Exception as e:
        logger.error(f"Download failed for {url}: {e}")
    return False
