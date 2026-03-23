"""
Pipeline Runner (v14.3) - Master orchestrator for full-auto review generation.
Single entry point: python3 pipeline_runner.py --prompt "topic" --full-auto

Features:
- Environment check (tectonic, playwright, cookies)
- Auth pre-flight (auto-trigger UVA login if cookies expired)
- Batch processor integration (bundle prompts for minimal agent intervention)
- Pipeline state tracking (todo.json) for resume capability

Usage:
    python3 pipeline_runner.py --prompt "topic" --full-auto   # Run everything
    python3 pipeline_runner.py --next                          # Continue after agent work
    python3 pipeline_runner.py --status                        # Show pipeline status
    python3 pipeline_runner.py --reset                         # Reset pipeline
"""

import os, json, sys, re, subprocess, time, argparse
from pathlib import Path
from typing import Optional, List, Dict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db_manager import DatabaseManager
from fetch_metadata import OpenAlexSearcher, get_oa_link, download_file
from extract_knowledge import get_papers_for_extraction, build_extraction_prompt, save_knowledge, parse_extraction_output
from write_chapters import get_chapter_knowledge, get_previous_summaries, build_writing_prompt, get_all_cited_ids
from design_architecture import get_knowledge_summary

# ─── Configuration ───────────────────────────────────────────────

PIPELINE_STEPS = [
    "init",              # 0: Initialize workspace
    "acquire",           # 1: Search OpenAlex for papers
    "snowball",          # 2: Expand to 150 papers
    "extract_knowledge", # 3: Extract knowledge from all papers
    "design_architecture",# 4: Design chapter structure
    "write_chapters",    # 5: Write all chapters sequentially
    "render",            # 6: Generate PDF
]


def slugify(text: str) -> str:
    text = text.lower()
    return re.sub(r'[^a-z0-9]+', '-', text).strip('-')


class PipelineRunner:
    def __init__(self, prompt: str):
        self.prompt = prompt
        self.slug = slugify(prompt)
        self.project_root = self._find_project_root()
        self.workspace = os.path.join(self.project_root, "workspaces", self.slug)
        self.todo_path = os.path.join(self.workspace, "todo.json")
        self.db_path = os.path.join(self.workspace, "db", "review.duckdb")
        
        os.makedirs(self.workspace, exist_ok=True)
        os.makedirs(os.path.join(self.workspace, "db"), exist_ok=True)
        os.makedirs(os.path.join(self.workspace, "data"), exist_ok=True)
        os.makedirs(os.path.join(self.workspace, "outputs", "sections"), exist_ok=True)
        
        self.db = DatabaseManager(db_path=self.db_path)
        self.searcher = OpenAlexSearcher()
        self.todo = self._load_todo()
    
    def _find_project_root(self):
        curr = Path(__file__).resolve()
        for parent in curr.parents:
            if (parent / ".opencode").exists() or (parent / "workspaces").exists():
                return parent
        return Path.cwd()
    
    def check_environment(self) -> dict:
        """Check all required tools and return status."""
        checks = {}
        
        # Check tectonic
        try:
            r = subprocess.run(["tectonic", "--version"], capture_output=True, text=True, timeout=10)
            checks["tectonic"] = {"ok": True, "version": r.stdout.strip().split('\n')[0]}
        except (FileNotFoundError, subprocess.TimeoutExpired):
            checks["tectonic"] = {"ok": False, "fix": "pip install tectonic or brew install tectonic"}
        
        # Check playwright
        try:
            from playwright.sync_api import sync_playwright
            checks["playwright"] = {"ok": True}
        except ImportError:
            checks["playwright"] = {"ok": False, "fix": "pip install playwright && playwright install chromium"}
        
        # Check UVA cookies
        cookie_path = os.path.expanduser("~/.opencode/data/.uva_cookies.json")
        if os.path.exists(cookie_path):
            size = os.path.getsize(cookie_path)
            checks["uva_cookies"] = {"ok": size > 100, "size": size, 
                                      "fix": "Run UVA NetBadge login if full text is needed"}
        else:
            checks["uva_cookies"] = {"ok": False, "fix": "Run UVA NetBadge login if full text is needed"}
        
        # Check duckdb
        try:
            import duckdb
            checks["duckdb"] = {"ok": True}
        except ImportError:
            checks["duckdb"] = {"ok": False, "fix": "pip install duckdb"}
        
        return checks
    
    def trigger_uva_login(self):
        """Auto-trigger UVA NetBadge login in browser."""
        print("\n🔐 UVA Authentication Required")
        print("  Opening browser for NetBadge login...")
        print("  Please complete Duo authentication in the browser window.\n")
        
        script_dir = os.path.dirname(os.path.abspath(__file__))
        login_script = os.path.join(script_dir, "fetch_pmc.py")
        
        # Launch login in a subprocess with visible browser
        subprocess.run([
            sys.executable, "-c",
            f"from fetch_pmc import PMCFetcher; "
            f"f = PMCFetcher(headless=False); "
            f"f.login_netbadge()"
        ], cwd=script_dir)
        
        # Verify cookie was saved
        cookie_path = os.path.expanduser("~/.opencode/data/.uva_cookies.json")
        if os.path.exists(cookie_path) and os.path.getsize(cookie_path) > 100:
            print("  ✅ UVA cookies saved successfully!")
            return True
        else:
            print("  ⚠️  Cookies may not have been saved. Full-text acquisition may fail.")
            return False
    
    def _load_todo(self):
        if os.path.exists(self.todo_path):
            with open(self.todo_path) as f:
                return json.load(f)
        return {"current_step": 0, "steps": {}, "prompt": self.prompt}
    
    def _save_todo(self):
        with open(self.todo_path, 'w') as f:
            json.dump(self.todo, f, indent=2)
    
    def _extract_keywords(self) -> List[str]:
        stop = {'the','a','an','of','in','for','and','or','to','on','with','by','from',
                'is','are','was','were','be','been','being','have','has','had','do','does',
                'did','will','would','could','should','may','might','can','shall','that',
                'this','these','those','it','its','based','review','design','using','structure'}
        words = re.findall(r'[a-z]+', self.prompt.lower())
        return [w for w in words if w not in stop and len(w) > 2]

    # ─── STEP 0: INIT ────────────────────────────────────────
    def step_init(self):
        print("🔧 STEP 0: Initializing workspace & environment check...")
        print(f"  Workspace: {self.workspace}")
        print(f"  Database:  {self.db_path}")
        
        # Environment check
        checks = self.check_environment()
        all_ok = True
        for tool, status in checks.items():
            if status["ok"]:
                print(f"  ✅ {tool}: {'installed' if 'version' not in status else status.get('version', 'ok')}")
            else:
                print(f"  ❌ {tool}: {status.get('fix', 'not available')}")
                all_ok = False
        
        if not checks.get("tectonic", {}).get("ok"):
            print("\n  ⚠️  tectonic is required for PDF rendering. Install it first.")
        
        conn = self.db.get_connection()
        paper_count = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        conn.close()
        
        self.todo["current_step"] = 1 if paper_count == 0 else self._detect_resume_step()
        self.todo["env_checks"] = checks
        self._save_todo()
        print(f"  Papers in DB: {paper_count}")
        print(f"  Next step: {PIPELINE_STEPS[self.todo['current_step']]}")
    
    def _detect_resume_step(self) -> int:
        """Detect where to resume from based on database state."""
        conn = self.db.get_connection()
        papers = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        included = conn.execute("SELECT COUNT(*) FROM papers WHERE screening_status='include' AND abstract IS NOT NULL AND abstract != ''").fetchone()[0]
        knowledge = conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]
        
        sections_dir = os.path.join(self.workspace, "outputs", "sections")
        chapters = len([f for f in os.listdir(sections_dir) 
                       if re.match(r'sec\d+\.tex', f)]) if os.path.exists(sections_dir) else 0
        
        # Check if blueprint exists
        blueprint_path = os.path.join(self.workspace, "outputs", "blueprint.json")
        has_blueprint = os.path.exists(blueprint_path)
        n_blueprint_chapters = 0
        if has_blueprint:
            try:
                with open(blueprint_path) as f:
                    bl = json.load(f)
                n_blueprint_chapters = len(bl.get("chapters", []))
            except: pass
        
        conn.close()
        
        # Decision tree (ordered by priority)
        if papers == 0:
            return 1  # acquire
        elif included < 150:
            return 2  # snowball
        elif knowledge < included * 2:  # Need at least ~2 knowledge per paper
            return 3  # extract
        elif not has_blueprint:
            return 4  # architecture
        elif chapters < n_blueprint_chapters:
            return 5  # write
        else:
            return 6  # render

    # ─── STEP 1: ACQUIRE ─────────────────────────────────────
    def step_acquire(self):
        print("📥 STEP 1: Acquiring papers from OpenAlex...")
        keywords = self._extract_keywords()
        
        # Search with multiple queries for breadth
        queries = [
            self.prompt,
            ' '.join(keywords[:4]) if len(keywords) > 4 else self.prompt,
            ' '.join(keywords[2:6]) if len(keywords) > 6 else self.prompt,
        ]
        seen = set()
        total_ingested = 0
        
        for q in queries:
            if q in seen: continue
            seen.add(q)
            print(f"  Searching: '{q}'")
            results = self.searcher.search(q, limit=50)
            for r in results:
                raw_id = r.get('id')
                if not raw_id: continue
                paper_id = raw_id.split('/')[-1]
                doi = (r.get('doi', '') or '').replace("https://doi.org/", "")
                abstract = self.searcher.reconstruct_abstract(r.get('abstract_inverted_index'))
                bib = self.searcher.extract_biblio(r)
                
                primary_loc = r.get('primary_location') or {}
                source = primary_loc.get('source') or {}
                journal = source.get('display_name')
                if not journal:
                    for loc in r.get('locations', []):
                        s = loc.get('source') or {}
                        if s.get('display_name'):
                            journal = s.get('display_name'); break
                if not journal:
                    journal = r.get('host_venue', {}).get('display_name', 'Unknown')
                
                self.db.upsert_paper({
                    'paper_id': paper_id, 'doi': doi, 'title': r.get('title'),
                    'abstract': abstract, 'year': r.get('publication_year'), 'journal': journal,
                    'volume': bib['volume'], 'issue': bib['issue'], 'pages': bib['pages'],
                    'authors_json': json.dumps([{'name': a.get('author', {}).get('display_name')} for a in r.get('authorships', [])]),
                    'referenced_works_json': json.dumps(r.get('referenced_works', [])),
                    'citation_count': r.get('cited_by_count'),
                    'oa_status': r.get('open_access', {}).get('status')
                })
                total_ingested += 1
            time.sleep(0.3)
        
        conn = self.db.get_connection()
        total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        with_abs = conn.execute("SELECT COUNT(*) FROM papers WHERE abstract IS NOT NULL AND abstract != ''").fetchone()[0]
        conn.close()
        
        print(f"  Ingested: {total_ingested} new | Total: {total} | With abstracts: {with_abs}")
        self.todo["current_step"] = 2
        self.todo["steps"]["acquire"] = {"total": total, "with_abstract": with_abs}
        self._save_todo()

    # ─── STEP 2: SNOWBALL ────────────────────────────────────
    def step_snowball(self, target: int = 150):
        print(f"❄️  STEP 2: Snowballing to {target} included papers...")
        keywords = self._extract_keywords()
        conn = self.db.get_connection()
        
        # First, mark initial search results as include
        conn.execute(f"""
            UPDATE papers SET screening_status = 'include'
            WHERE paper_id IN (
                SELECT paper_id FROM papers 
                WHERE screening_status = 'pending' AND abstract IS NOT NULL AND abstract != ''
                ORDER BY citation_count DESC NULLS LAST
                LIMIT {target}
            )
        """)
        
        iteration = 0
        while True:
            iteration += 1
            included = conn.execute(
                "SELECT COUNT(*) FROM papers WHERE screening_status='include' AND abstract IS NOT NULL AND abstract != ''"
            ).fetchone()[0]
            
            if included >= target:
                break
            if iteration > 10:
                break
            
            # Collect refs from included papers
            rows = conn.execute("""
                SELECT referenced_works_json FROM papers 
                WHERE screening_status='include' AND referenced_works_json IS NOT NULL AND referenced_works_json != '[]'
            """).fetchall()
            
            all_refs = set()
            for row in rows:
                for ref in json.loads(row[0]):
                    all_refs.add(ref.split('/')[-1] if '/' in ref else ref)
            
            existing = {r[0] for r in conn.execute("SELECT paper_id FROM papers").fetchall()}
            new_refs = list(all_refs - existing)
            
            if new_refs:
                for i in range(0, len(new_refs), 50):
                    batch = new_refs[i:i+50]
                    results = self.searcher.get_works_by_ids(batch)
                    for r in results:
                        raw_id = r.get('id')
                        if not raw_id: continue
                        paper_id = raw_id.split('/')[-1]
                        doi = (r.get('doi', '') or '').replace("https://doi.org/", "")
                        abstract = self.searcher.reconstruct_abstract(r.get('abstract_inverted_index'))
                        bib = self.searcher.extract_biblio(r)
                        primary_loc = r.get('primary_location') or {}
                        source = primary_loc.get('source') or {}
                        journal = source.get('display_name') or 'Unknown'
                        
                        self.db.upsert_paper({
                            'paper_id': paper_id, 'doi': doi, 'title': r.get('title'),
                            'abstract': abstract, 'year': r.get('publication_year'), 'journal': journal,
                            'volume': bib['volume'], 'issue': bib['issue'], 'pages': bib['pages'],
                            'authors_json': json.dumps([]),
                            'referenced_works_json': json.dumps(r.get('referenced_works', [])),
                            'citation_count': r.get('cited_by_count'),
                            'oa_status': r.get('open_access', {}).get('status')
                        })
                    time.sleep(0.3)
                conn.close()
                conn = self.db.get_connection()
            
            # Relevance-scored auto-include
            needed = target - conn.execute(
                "SELECT COUNT(*) FROM papers WHERE screening_status='include' AND abstract IS NOT NULL AND abstract != ''"
            ).fetchone()[0]
            if needed <= 0:
                break
            
            pending = conn.execute("""
                SELECT paper_id, title, abstract, citation_count 
                FROM papers WHERE screening_status='pending' AND abstract IS NOT NULL AND abstract != ''
            """).fetchall()
            
            scored = []
            for pid, title, abstract, cc in pending:
                title_score = sum(1 for kw in keywords if kw.lower() in (title or '').lower()) / max(len(keywords), 1)
                abstract_score = sum(1 for kw in keywords if kw.lower() in (abstract or '').lower()) / max(len(keywords), 1)
                citation_bonus = min((cc or 0) / 10000, 0.3)
                scored.append((pid, title_score * 2 + abstract_score + citation_bonus))
            
            scored.sort(key=lambda x: x[1], reverse=True)
            for pid, _ in scored[:needed]:
                conn.execute("UPDATE papers SET screening_status='include' WHERE paper_id=?", [pid])
        
        included = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE screening_status='include' AND abstract IS NOT NULL AND abstract != ''"
        ).fetchone()[0]
        conn.close()
        
        print(f"  Included: {included} papers with abstracts")
        self.todo["current_step"] = 3
        self.todo["steps"]["snowball"] = {"included": included}
        self._save_todo()

    # ─── STEP 3: EXTRACT KNOWLEDGE ───────────────────────────
    def step_extract_knowledge(self):
        print("🧠 STEP 3: Extracting knowledge from papers...")
        conn = self.db.get_connection()
        
        papers = conn.execute("""
            SELECT COUNT(*) FROM papers p
            WHERE p.screening_status = 'include' 
            AND p.abstract IS NOT NULL AND p.abstract != ''
            AND p.paper_id NOT IN (SELECT DISTINCT paper_id FROM knowledge)
        """).fetchone()[0]
        conn.close()
        
        if papers == 0:
            print("  All papers already have knowledge extracted.")
            self.todo["current_step"] = 4
            self._save_todo()
            return
        
        print(f"  {papers} papers need knowledge extraction.")
        
        # Create extraction bundles using batch_processor
        from batch_processor import create_extraction_bundles
        bundles_dir = os.path.join(self.workspace, "data", "extraction_bundles")
        n_bundles = create_extraction_bundles(self.db_path, bundles_dir, bundle_size=10)
        
        # Generate agent commands
        from batch_processor import generate_commands
        generate_commands(self.workspace)
        
        self.todo["current_step"] = 3
        self.todo["steps"]["extract_knowledge"] = {
            "papers_to_process": papers,
            "bundles_dir": bundles_dir,
            "n_bundles": n_bundles,
            "status": "pending_agent_processing"
        }
        self._save_todo()

    # ─── STEP 4: DESIGN ARCHITECTURE ─────────────────────────
    def step_design_architecture(self):
        print("🏗️  STEP 4: Designing chapter architecture...")
        
        dump_path = os.path.join(self.workspace, "data", "knowledge_dump.md")
        summary = get_knowledge_summary(self.db_path)
        with open(dump_path, 'w') as f:
            f.write(summary)
        
        conn = self.db.get_connection()
        kn_count = conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]
        by_type = conn.execute("SELECT knowledge_type, COUNT(*) FROM knowledge GROUP BY knowledge_type ORDER BY COUNT(*) DESC").fetchall()
        conn.close()
        
        print(f"  Knowledge points: {kn_count}")
        for kt, cnt in by_type:
            print(f"    {kt}: {cnt}")
        print(f"  Dump saved: {dump_path}")
        print(f"  The orchestrating agent should read this dump and design the chapter structure.")
        
        self.todo["current_step"] = 4
        self.todo["steps"]["design_architecture"] = {
            "knowledge_count": kn_count,
            "dump_path": dump_path,
            "status": "pending_agent_design"
        }
        self._save_todo()

    # ─── STEP 5: WRITE CHAPTERS ──────────────────────────────
    def step_write_chapters(self):
        print("✍️  STEP 5: Preparing chapter writing...")
        
        blueprint_path = os.path.join(self.workspace, "outputs", "blueprint.json")
        if not os.path.exists(blueprint_path):
            print("  No blueprint found. The agent should design architecture first (step 4).")
            self.todo["current_step"] = 4
            self._save_todo()
            return
        
        with open(blueprint_path) as f:
            blueprint = json.load(f)
        
        chapters = blueprint.get("chapters", [])
        sections_dir = os.path.join(self.workspace, "outputs", "sections")
        os.makedirs(sections_dir, exist_ok=True)
        
        written = set()
        for f in os.listdir(sections_dir):
            m = re.match(r'sec(\d+)\.tex', f)
            if m:
                with open(os.path.join(sections_dir, f)) as fh:
                    if len(fh.read()) > 200:
                        written.add(int(m.group(1)))
        
        remaining = [ch for ch in chapters if ch["number"] not in written]
        
        if not remaining:
            print(f"  All {len(chapters)} chapters already written.")
            self.todo["current_step"] = 6
            self._save_todo()
            return
        
        print(f"  {len(remaining)} chapters remaining to write.")
        
        # Create writing bundles using batch_processor
        from batch_processor import create_writing_bundles
        writing_dir = os.path.join(self.workspace, "data", "writing_bundles")
        n_bundles = create_writing_bundles(self.db_path, blueprint_path, sections_dir, writing_dir)
        
        # Generate agent commands
        from batch_processor import generate_commands
        generate_commands(self.workspace)
        
        self.todo["current_step"] = 5
        self.todo["steps"]["write_chapters"] = {
            "total": len(chapters),
            "remaining": len(remaining),
            "written": list(written),
            "bundles_dir": writing_dir,
            "n_bundles": n_bundles,
            "status": "pending_agent_writing"
        }
        self._save_todo()

    # ─── STEP 6: RENDER ──────────────────────────────────────
    def step_render(self, force: bool = True):
        print("🎨 STEP 6: Rendering PDF...")
        
        # Sync citations
        sections_dir = os.path.join(self.workspace, "outputs", "sections")
        if os.path.exists(sections_dir):
            cite_pattern = re.compile(r'\\cite\{([^}]+)\}')
            cited_ids = set()
            for f in os.listdir(sections_dir):
                if f.endswith('.tex'):
                    with open(os.path.join(sections_dir, f)) as file:
                        for m in cite_pattern.findall(file.read()):
                            cited_ids.update([i.strip() for i in m.split(',')])
            conn = self.db.get_connection()
            for pid in cited_ids:
                conn.execute("UPDATE papers SET screening_status='include' WHERE paper_id=?", [pid])
            conn.close()
            print(f"  Synced {len(cited_ids)} citations.")
        
        # Generate bibliography
        from bib_generator import generate_hardened_bib
        bib_path = os.path.join(self.workspace, "outputs", "references.bib")
        generate_hardened_bib(self.db_path, sections_dir, bib_path)
        
        # Clean files
        from latex_cleaner import clean_file
        clean_file(bib_path)
        if os.path.exists(sections_dir):
            for f in os.listdir(sections_dir):
                if f.endswith('.tex'):
                    clean_file(os.path.join(sections_dir, f))
        main_tex = os.path.join(self.workspace, "outputs", "main.tex")
        if os.path.exists(main_tex):
            clean_file(main_tex)
        
        # Render
        result = subprocess.run(
            ["tectonic", "main.tex"],
            cwd=os.path.join(self.workspace, "outputs"),
            capture_output=True, text=True
        )
        
        pdf_path = os.path.join(self.workspace, "outputs", "main.pdf")
        if os.path.exists(pdf_path):
            size = os.path.getsize(pdf_path)
            print(f"  🏁 PDF: {pdf_path} ({size/1024:.0f} KB)")
        else:
            print(f"  ❌ PDF generation failed:")
            print(result.stderr[-500:] if result.stderr else result.stdout[-500:])
        
        self.todo["current_step"] = 7
        self.todo["steps"]["render"] = {"done": True}
        self._save_todo()

    # ─── NEXT: Auto-detect and run next step ─────────────────
    def run_next(self):
        """Auto-detect the current state and run the next step."""
        step = self.todo.get("current_step", 0)
        
        if step >= 7:
            print("✅ Pipeline complete!")
            return
        
        step_name = PIPELINE_STEPS[step]
        print(f"\n{'='*50}")
        print(f"▶ Running: {step_name} (step {step}/{len(PIPELINE_STEPS)-1})")
        print(f"{'='*50}\n")
        
        if step_name == "init":
            self.step_init()
        elif step_name == "acquire":
            self.step_acquire()
        elif step_name == "snowball":
            self.step_snowball()
        elif step_name == "extract_knowledge":
            self.step_extract_knowledge()
        elif step_name == "design_architecture":
            self.step_design_architecture()
        elif step_name == "write_chapters":
            self.step_write_chapters()
        elif step_name == "render":
            self.step_render()
    
    def run_full_auto(self):
        """Run the entire pipeline automatically, stopping at first agent-dependent step."""
        print(f"\n{'='*60}")
        print(f"🚀 FULL AUTO-PILOT: '{self.prompt}'")
        print(f"{'='*60}\n")
        
        # Run init
        self.step_init()
        step = self.todo.get("current_step", 0)
        
        # Run mechanical steps sequentially
        while step < len(PIPELINE_STEPS):
            step_name = PIPELINE_STEPS[step]
            
            if step_name == "init":
                step = self.todo.get("current_step", 1)
                continue
            
            print(f"\n{'='*50}")
            print(f"▶ Running: {step_name} (step {step}/{len(PIPELINE_STEPS)-1})")
            print(f"{'='*50}\n")
            
            if step_name == "acquire":
                self.step_acquire()
            elif step_name == "snowball":
                self.step_snowball()
            elif step_name == "extract_knowledge":
                self.step_extract_knowledge()
                self._print_checkpoint("extract_knowledge")
                return  # Agent intervention needed
            elif step_name == "design_architecture":
                self.step_design_architecture()
                self._print_checkpoint("design_architecture")
                return  # Agent intervention needed
            elif step_name == "write_chapters":
                self.step_write_chapters()
                remaining = self.todo.get("steps", {}).get("write_chapters", {}).get("remaining", 0)
                if remaining > 0:
                    self._print_checkpoint("write_chapters")
                    return  # Agent intervention needed
            elif step_name == "render":
                self.step_render()
                print("\n✅ PIPELINE COMPLETE!")
                return
            
            step = self.todo.get("current_step", step + 1)
        
        print("\n✅ PIPELINE COMPLETE!")

    def _print_checkpoint(self, step_name: str):
        conn = self.db.get_connection()
        kn = conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]
        inc = conn.execute("SELECT COUNT(*) FROM papers WHERE screening_status='include' AND abstract IS NOT NULL AND abstract != ''").fetchone()[0]
        conn.close()
        
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        
        print(f"\n{'='*60}")
        print(f"📋 CHECKPOINT: {step_name}")
        print(f"  Papers: {inc} included | Knowledge: {kn} points")
        print(f"  AGENT_MODE: zero_touch")
        print(f"  DB_PATH: {self.db_path}")
        print(f"  SCRIPTS_DIR: {scripts_dir}")
        print(f"  WORKSPACE: {self.workspace}")
        
        if step_name == "extract_knowledge":
            bundles_dir = os.path.join(self.workspace, "data", "extraction_bundles")
            manifest_path = os.path.join(bundles_dir, "manifest.json")
            if os.path.exists(manifest_path):
                with open(manifest_path) as f:
                    manifest = json.load(f)
                n_bundles = manifest["total_bundles"]
                n_papers = manifest["total_papers"]
                print(f"  EXTRACTION_BUNDLES_DIR: {bundles_dir}")
                print(f"  TOTAL_BUNDLES: {n_bundles}")
                print(f"  TOTAL_PAPERS: {n_papers}")
                print(f"")
                # List bundle files as machine-parseable commands
                bundle_files = sorted([f for f in os.listdir(bundles_dir) if f.startswith("bundle_") and f.endswith(".json")])
                for bf in bundle_files:
                    bpath = os.path.join(bundles_dir, bf)
                    output_path = os.path.join(bundles_dir, bf.replace(".json", "_result.json"))
                    print(f"  AGENT_COMMAND_GET_PROMPT: python3 {os.path.join(scripts_dir, 'batch_processor.py')} --db-path {self.db_path} --type extract --bundle {bpath} --output-dir {bundles_dir}")
                    print(f"  AGENT_COMMAND_SAVE_RESULT: python3 {os.path.join(scripts_dir, 'extract_knowledge.py')} --db-path {self.db_path} --save-json {output_path}")
                    print(f"  AGENT_RESULT_PATH: {output_path}")
                    print(f"")
                print(f"  AGENT_COMMAND_NEXT: python3 {os.path.join(scripts_dir, 'pipeline_runner.py')} --prompt \"{self.prompt}\" --next")
        
        elif step_name == "design_architecture":
            dump_path = os.path.join(self.workspace, "data", "knowledge_dump.md")
            outputs_dir = os.path.join(self.workspace, "outputs")
            blueprint_path = os.path.join(outputs_dir, "blueprint.json")
            print(f"  KNOWLEDGE_DUMP: {dump_path}")
            print(f"  AGENT_COMMAND_SUMMARY: python3 {os.path.join(scripts_dir, 'design_architecture.py')} --db-path {self.db_path} --output-dir {outputs_dir} --summary-only")
            print(f"  AGENT_COMMAND_SAVE_BLUEPRINT: python3 {os.path.join(scripts_dir, 'design_architecture.py')} --db-path {self.db_path} --output-dir {outputs_dir} --save-blueprint {blueprint_path}")
            print(f"  AGENT_BLUEPRINT_PATH: {blueprint_path}")
            print(f"  AGENT_COMMAND_NEXT: python3 {os.path.join(scripts_dir, 'pipeline_runner.py')} --prompt \"{self.prompt}\" --next")
        
        elif step_name == "write_chapters":
            writing_dir = os.path.join(self.workspace, "data", "writing_bundles")
            manifest_path = os.path.join(writing_dir, "manifest.json")
            sections_dir = os.path.join(self.workspace, "outputs", "sections")
            if os.path.exists(manifest_path):
                with open(manifest_path) as f:
                    manifest = json.load(f)
                n_chapters = manifest["total_chapters"]
                written = len([f for f in os.listdir(sections_dir) if re.match(r'sec\d+\.tex', f)]) if os.path.exists(sections_dir) else 0
                remaining = [b for b in manifest["bundles"] if b["number"] > written]
                print(f"  TOTAL_CHAPTERS: {n_chapters}")
                print(f"  WRITTEN: {written}")
                print(f"  REMAINING: {len(remaining)}")
                print(f"  WRITING_BUNDLES_DIR: {writing_dir}")
                print(f"")
                for b in remaining:
                    bpath = b["path"]
                    ch_num = b["number"]
                    sec_path = os.path.join(sections_dir, f"sec{ch_num}.tex")
                    print(f"  AGENT_COMMAND_GET_PROMPT: python3 {os.path.join(scripts_dir, 'batch_processor.py')} --db-path {self.db_path} --type write --bundle {bpath} --output-dir {writing_dir}")
                    print(f"  AGENT_CHAPTER_FILE: {sec_path}")
                    print(f"")
                print(f"  AGENT_COMMAND_NEXT: python3 {os.path.join(scripts_dir, 'pipeline_runner.py')} --prompt \"{self.prompt}\" --next")
        
        print(f"{'='*60}\n")

    def status(self):
        """Show pipeline status."""
        conn = self.db.get_connection()
        papers = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        included = conn.execute("SELECT COUNT(*) FROM papers WHERE screening_status='include'").fetchone()[0]
        with_abs = conn.execute("SELECT COUNT(*) FROM papers WHERE screening_status='include' AND abstract IS NOT NULL AND abstract != ''").fetchone()[0]
        knowledge = conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]
        conn.close()
        
        sections_dir = os.path.join(self.workspace, "outputs", "sections")
        chapters = len([f for f in os.listdir(sections_dir) if re.match(r'sec\d+\.tex', f)]) if os.path.exists(sections_dir) else 0
        
        step = self.todo.get("current_step", 0)
        step_name = PIPELINE_STEPS[step] if step < len(PIPELINE_STEPS) else "complete"
        
        print(f"\n📊 Pipeline Status: {self.slug}")
        print(f"  Current step: {step} ({step_name})")
        print(f"  Papers: {papers} total, {included} included, {with_abs} with abstracts")
        print(f"  Knowledge: {knowledge} points")
        print(f"  Chapters: {chapters} written")


# ─── CLI ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", help="The review topic")
    parser.add_argument("--init", action="store_true", help="Initialize pipeline")
    parser.add_argument("--next", action="store_true", help="Run next step")
    parser.add_argument("--full-auto", action="store_true", help="Run until agent intervention needed")
    parser.add_argument("--status", action="store_true", help="Show pipeline status")
    parser.add_argument("--reset", action="store_true", help="Reset pipeline state")
    parser.add_argument("--force", action="store_true", help="Force render")
    args = parser.parse_args()
    
    if not args.prompt:
        # Try to find existing workspace
        workspaces = os.path.join(Path(__file__).resolve().parents[3], "workspaces")
        if os.path.exists(workspaces):
            dirs = [d for d in os.listdir(workspaces) if os.path.isdir(os.path.join(workspaces, d))]
            if len(dirs) == 1:
                args.prompt = dirs[0].replace('-', ' ')
                print(f"  Auto-detected workspace: {dirs[0]}")
    
    if not args.prompt:
        parser.error("--prompt is required (or run from a workspace directory)")
    
    runner = PipelineRunner(prompt=args.prompt)
    
    if args.reset:
        if os.path.exists(runner.todo_path):
            os.remove(runner.todo_path)
        runner.todo = {"current_step": 0, "steps": {}, "prompt": args.prompt}
        runner._save_todo()
        print("🔄 Pipeline reset.")
    elif args.status:
        runner.status()
    elif args.init:
        runner.step_init()
    elif args.full_auto:
        runner.run_full_auto()
    elif args.next:
        runner.run_next()
    else:
        parser.print_help()
