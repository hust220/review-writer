"""
Core PubMed Module for UniversalReviewer.
Handles title-based search and PMID to PMCID conversion.
"""

import requests
import logging
import time
import re
from typing import Optional, Dict, List
from xml.etree import ElementTree

logger = logging.getLogger(__name__)

NCBI_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
NCBI_ELINKS_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi"
NCBI_IDCONV_URL = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"

def search_pubmed_by_title(title: str, email: str = "jianopt@gmail.com") -> Optional[str]:
    """
    Search PubMed by title and return the PMID of the best match.
    
    Args:
        title: The paper title to search for
        email: Email for NCBI API
        
    Returns:
        PMID string if found, None otherwise
    """
    if not title or len(title) < 10:
        return None
    
    # Clean title for search
    clean_title = re.sub(r'[^\w\s]', ' ', title)
    clean_title = ' '.join(clean_title.split())  # normalize whitespace
    
    params = {
        "db": "pubmed",
        "term": f'"{clean_title}"[Title]',
        "retmax": 5,
        "retmode": "json",
        "email": email,
        "tool": "UniversalReviewer"
    }
    
    try:
        response = requests.get(NCBI_ESEARCH_URL, params=params, timeout=15)
        if response.status_code == 200:
            data = response.json()
            id_list = data.get("esearchresult", {}).get("idlist", [])
            
            if id_list:
                # Return the first (most relevant) result
                return id_list[0]
    except Exception as e:
        logger.error(f"PubMed search failed for title '{title[:50]}...': {e}")
    
    time.sleep(0.4)  # Rate limiting
    return None


def pmid_to_pmcid(pmid: str, email: str = "jianopt@gmail.com") -> Optional[str]:
    """
    Convert PMID to PMCID using NCBI ID Converter.
    
    Args:
        pmid: PubMed ID
        email: Email for NCBI API
        
    Returns:
        PMCID string if found, None otherwise
    """
    if not pmid:
        return None
    
    params = {
        "tool": "UniversalReviewer",
        "email": email,
        "ids": pmid,
        "format": "json"
    }
    
    try:
        response = requests.get(NCBI_IDCONV_URL, params=params, timeout=15)
        if response.status_code == 200:
            data = response.json()
            records = data.get("records", [])
            for record in records:
                pmcid = record.get("pmcid")
                if pmcid:
                    return pmcid
    except Exception as e:
        logger.error(f"ID conversion failed for PMID {pmid}: {e}")
    
    time.sleep(0.4)  # Rate limiting
    return None


def title_to_pmcid(title: str, email: str = "jianopt@gmail.com") -> Optional[str]:
    """
    Convert a paper title to PMCID via PubMed search.
    
    Args:
        title: The paper title
        email: Email for NCBI API
        
    Returns:
        PMCID string if found, None otherwise
    """
    pmid = search_pubmed_by_title(title, email)
    if pmid:
        return pmid_to_pmcid(pmid, email)
    return None


def batch_resolve_titles(titles: List[str], email: str = "jianopt@gmail.com") -> Dict[str, str]:
    """
    Batch resolve multiple titles to PMCIDs.
    
    Args:
        titles: List of paper titles
        email: Email for NCBI API
        
    Returns:
        Dictionary mapping title to PMCID
    """
    results = {}
    for title in titles:
        pmcid = title_to_pmcid(title, email)
        if pmcid:
            results[title] = pmcid
        time.sleep(0.4)  # Rate limiting
    return results
