"""
PMC Utils: Handles DOI/PMID -> PMCID resolution and PMC fetching.
Uses NCBI ID Converter API and Playwright for proxied access.
"""

import requests
import json
import logging
import time
from typing import Optional, Dict, List, Tuple
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

NCBI_ID_CONV_URL = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"

def resolve_to_pmcid(ids: List[str], email: str = "user@example.com") -> Dict[str, str]:
    """
    Resolve a list of DOIs or PMIDs to PMCIDs using NCBI ID Converter.
    Returns a mapping of input_id -> pmcid.
    """
    if not ids:
        return {}
    
    results = {}
    # NCBI limits to 200 IDs per request
    batch_size = 100
    for i in range(0, len(ids), batch_size):
        batch = ids[i:i+batch_size]
        params = {
            "tool": "ReviewOS",
            "email": email,
            "ids": ",".join(batch),
            "format": "json"
        }
        
        try:
            response = requests.get(NCBI_ID_CONV_URL, params=params, timeout=15)
            if response.status_code == 200:
                data = response.json()
                for record in data.get("records", []):
                    # The record contains original id in 'doi' or 'pmid'
                    original_id = record.get("doi") or record.get("pmid")
                    pmcid = record.get("pmcid")
                    if original_id and pmcid:
                        results[original_id] = pmcid
            else:
                logger.error(f"NCBI API Error: {response.status_code}")
        except Exception as e:
            logger.error(f"Failed to resolve IDs: {e}")
        
        # Respect NCBI rate limits (3 requests per second)
        time.sleep(0.4)
        
    return results

def get_pmc_oa_url(pmcid: str) -> str:
    """Return the direct URL for a PMC article."""
    # Ensure it starts with PMC
    if not pmcid.startswith("PMC"):
        pmcid = f"PMC{pmcid}"
    return f"https://www.ncbi.nlm.nih.gov/pmc/articles/{pmcid}/"

def get_pmc_proxied_url(pmcid: str) -> str:
    """Return the UVA proxied URL for a PMC article."""
    if not pmcid.startswith("PMC"):
        pmcid = f"PMC{pmcid}"
    # UVA rewritten format: https://www-ncbi-nlm-nih-gov.proxy1.library.virginia.edu/pmc/articles/PMCXXXXX/
    return f"https://www-ncbi-nlm-nih-gov.proxy1.library.virginia.edu/pmc/articles/{pmcid}/"

def is_valid_pmc_content(html: str) -> bool:
    """Verify that the HTML is a valid PMC article page."""
    if not html:
        return False
    html_lower = html.lower()
    # Check for core PMC markers
    markers = ["<article", "pmc-sidebar", "abstract", "id=\"pmc-art-view\"", "ncbi.nlm.nih.gov"]
    matches = sum(1 for m in markers if m in html_lower)
    return matches >= 2 and len(html) > 10000

# Utility to extract text content from PMC HTML will be in parse_pmc.py
