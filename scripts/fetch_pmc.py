"""
PMC Fetcher (Enhanced): Fetches fulltext HTML from PubMed Central (PMC).
Features: Robust UVA NetBadge authentication, session verification, and content validation.
"""

import os
import json
import time
import logging
import random
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
from pmc_utils import get_pmc_oa_url, get_pmc_proxied_url, is_valid_pmc_content

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class PMCFetcher:
    # Use global path for cookies (shared across projects)
    COOKIE_PATH = os.path.expanduser("~/.opencode/data/.uva_cookies.json")
    # URL to test proxy authentication
    TEST_URL = "https://www-ncbi-nlm-nih-gov.proxy1.library.virginia.edu/pmc/"
    # Login entry point
    LOGIN_URL = "https://proxy1.library.virginia.edu/login?url=https://www.ncbi.nlm.nih.gov/pmc/"
    
    def __init__(self, data_dir: Optional[str] = None, headless: bool = True):
        if data_dir is None:
            self.data_dir = Path(os.getcwd()) / "data/fulltext"
        else:
            self.data_dir = Path(data_dir)
            
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self._authenticated = False
    
    def start(self):
        """Start the browser session."""
        if self.browser: return
        self.playwright = sync_playwright().start()
        # Launch with specific headless setting
        self.browser = self.playwright.chromium.launch(headless=self.headless)
        
        if os.path.exists(self.COOKIE_PATH):
            try:
                with open(self.COOKIE_PATH, 'r') as f:
                    storage_state = json.load(f)
                self.context = self.browser.new_context(storage_state=storage_state)
                logger.info("Loaded UVA proxy cookies.")
            except Exception as e:
                logger.warning(f"Failed to load cookies: {e}")
                self.context = self.browser.new_context()
        else:
            self.context = self.browser.new_context()
            
        self.page = self.context.new_page()
        Stealth().apply_stealth_sync(self.page)
    
    def stop(self):
        """Close the browser session."""
        if self.context: self.context.close()
        if self.browser: self.browser.close()
        if self.playwright: self.playwright.stop()
        self.browser = None
        self._authenticated = False

    def verify_auth(self) -> bool:
        """Check if the proxy session is still active."""
        if not self.page: self.start()
        if not self.page: return False
        try:
            logger.info("Verifying proxy authentication...")
            self.page.goto(self.TEST_URL, timeout=30000)
            time.sleep(3)
            html = self.page.content().lower()
            current_url = self.page.url
            
            if "not authorized" in html or "shibidp" in current_url or "netbadge" in current_url:
                logger.info("-> Auth invalid or redirected to login.")
                return False
            
            if "ncbi.nlm.nih.gov" in current_url or "pubmed" in html:
                logger.info("-> Authenticated! Proxy session active.")
                return True
            return True
        except Exception as e:
            logger.error(f"Auth verification failed: {e}")
            return False

    def login_netbadge(self, wait_time: int = 120):
        """Perform manual NetBadge login."""
        # Ensure browser is running and visible
        self.stop()
        self.headless = False
        self.start()
        if not self.page: return

        logger.info("Opening NetBadge login...")
        self.page.goto(self.LOGIN_URL)
        
        print("\n" + "="*60 + "\n  ACTION REQUIRED: Complete NetBadge + Duo login in the browser.\n" + "="*60 + "\n")
        
        start = time.time()
        while time.time() - start < wait_time:
            try:
                if "ncbi.nlm.nih.gov" in self.page.url and "shibidp" not in self.page.url:
                    logger.info("Login detected!")
                    break
            except: pass
            time.sleep(3)
            
        self.save_cookies()
        self._authenticated = True
        
        # Restore headless
        self.stop()
        self.headless = True
        self.start()

    def save_cookies(self):
        if self.context:
            state = self.context.storage_state()
            os.makedirs(os.path.dirname(self.COOKIE_PATH), exist_ok=True)
            with open(self.COOKIE_PATH, 'w') as f:
                json.dump(state, f)
            logger.info("Cookies saved.")

    def ensure_authenticated(self):
        if self._authenticated: return
        if not self.verify_auth():
            self.login_netbadge()
        self._authenticated = True

    def fetch_article(self, pmcid: str) -> Tuple[Optional[str], str]:
        """Fetch a PMC article with prioritized OA then Proxy."""
        if not self.page: self.start()
        if not self.page: return None, "none"
        
        safe_id = pmcid.replace("/", "_")
        output_path = self.data_dir / f"{safe_id}.html"
        
        # 1. Try OA URL first (doesn't need authentication)
        oa_url = get_pmc_oa_url(pmcid)
        try:
            logger.info(f"Trying PMC OA: {pmcid}")
            self.page.goto(oa_url, timeout=30000)
            html = self.page.content()
            if is_valid_pmc_content(html):
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(html)
                return str(output_path), "pmc_oa"
        except Exception as e:
            logger.warning(f"OA fetch failed for {pmcid}: {e}")

        # 2. Try Proxied URL (needs authentication)
        self.ensure_authenticated()
        if not self.page: return None, "none"
        
        proxied_url = get_pmc_proxied_url(pmcid)
        try:
            logger.info(f"Trying PMC Proxy: {pmcid}")
            self.page.goto(proxied_url, timeout=45000)
            # Wait longer for proxied content
            self.page.wait_for_selector("#pmc-art-view", timeout=15000)
            html = self.page.content()
            if is_valid_pmc_content(html):
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(html)
                return str(output_path), "pmc_proxy"
        except Exception as e:
            logger.error(f"Proxy fetch failed for {pmcid}: {e}")
            
        return None, "none"
