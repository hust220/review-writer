"""
Review-OS Orchestrator (v14.1 - Master Integration): Full auto-pilot review generation.
Pipeline: Search → Snowball (relevance-scored) → Knowledge Extraction → Architecture Design → Chapter Writing → Render
"""

import os, json, logging, argparse, sys, re, subprocess, time
from pathlib import Path
from typing import Optional, List, Dict, Tuple

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from db_manager import DatabaseManager
from pmc_utils import resolve_to_pmcid
from fetch_pmc import PMCFetcher
from universal_parser import parse_everything
from fetch_metadata import get_oa_link, download_file, OpenAlexSearcher
from bib_generator import generate_hardened_bib
from latex_cleaner import clean_file

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return text.strip('-')

def compute_relevance(text: str, keywords: List[str]) -> float:
    """Simple keyword-based relevance scoring."""
    if not text or not keywords:
        return 0.0
    text_lower = text.lower()
    matches = sum(1 for kw in keywords if kw.lower() in text_lower)
    return matches / len(keywords)

class PipelineState:
    """Tracks pipeline progress for resume capability."""
    def __init__(self, path: str):
        self.path = path
        self.state = self._load()
    
    def _load(self):
        if os.path.exists(self.path):
            with open(self.path, 'r') as f:
                return json.load(f)
        return {"step": "init", "completed_steps": [], "config": {}}
    
    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, 'w') as f:
            json.dump(self.state, f, indent=2)
    
    def mark_step(self, step: str, data: dict = None):
        self.state["step"] = step
        if step not in self.state["completed_steps"]:
            self.state["completed_steps"].append(step)
        if data:
            self.state[f"step_{step}"] = data
        self.save()
    
    def is_complete(self, step: str) -> bool:
        return step in self.state["completed_steps"]
    
    def get_step_data(self, step: str) -> dict:
        return self.state.get(f"step_{step}", {})


class ReviewOrchestrator:
    def __init__(self, prompt: str, workspace_root: str = "workspaces"):
        self.prompt = prompt
        self.slug = slugify(prompt)
        self.project_root = self._find_project_root()
        self.workspace = os.path.join(self.project_root, workspace_root, self.slug)
        os.makedirs(self.workspace, exist_ok=True)
        
        self.db_dir = os.path.join(self.workspace, "db")
        self.data_dir = os.path.join(self.workspace, "data")
        self.output_dir = os.path.join(self.workspace, "outputs")
        for d in [self.db_dir, self.data_dir, self.output_dir]:
            os.makedirs(d, exist_ok=True)
        
        db_path = os.path.join(self.db_dir, "review.duckdb")
        self.db = DatabaseManager(db_path=db_path)
        self.pipeline = PipelineState(os.path.join(self.workspace, "pipeline_state.json"))
        
        self.pmc_fetcher = PMCFetcher(data_dir=os.path.join(self.data_dir, "fulltext"), headless=True)
        self.searcher = OpenAlexSearcher()

    def _find_project_root(self):
        curr = Path(__file__).resolve()
        for parent in curr.parents:
            if (parent / ".opencode").exists() or (parent / "workspaces").exists():
                return parent
        return Path.cwd()

    def _extract_keywords(self) -> List[str]:
        """Extract search keywords from the prompt."""
        # Remove common stop words and extract meaningful terms
        stop = {'the', 'a', 'an', 'of', 'in', 'for', 'and', 'or', 'to', 'on', 'with', 'by', 'from', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could', 'should', 'may', 'might', 'can', 'shall', 'that', 'this', 'these', 'those', 'it', 'its', 'based', 'review'}
        words = re.findall(r'[a-z]+', self.prompt.lower())
        return [w for w in words if w not in stop and len(w) > 2]

    # ═══════════════════════════════════════════════════════════
    # STEP 1: ACQUISITION
    # ═══════════════════════════════════════════════════════════
    def run_acquisition(self, queries: List[str]):
        """Search OpenAlex and ingest results."""
        print(f"📥 STEP 1: Acquisition for '{self.slug}'...")
        for q in queries:
            results = self.searcher.search(q, limit=50)
            self.ingest_results(results)
            print(f"  Searched: '{q}' → {len(results)} results")
        
        count = self.db.get_connection().execute("SELECT COUNT(*) FROM papers").fetchone()[0]
        with_abs = self.db.get_connection().execute(
            "SELECT COUNT(*) FROM papers WHERE abstract IS NOT NULL AND abstract != ''"
        ).fetchone()[0]
        print(f"📥 Total: {count} papers, {with_abs} with abstracts.")
        self.pipeline.mark_step("acquisition", {"queries": queries, "total": count, "with_abstract": with_abs})

    def ingest_results(self, results):
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
                locs = r.get('locations', [])
                for loc in locs:
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

    # ═══════════════════════════════════════════════════════════
    # STEP 2: SNOWBALL
    # ═══════════════════════════════════════════════════════════
    def run_snowball(self, target_count: int = 150):
        """Expand paper set via referenced_works with relevance-based auto-include."""
        print(f"❄️  STEP 2: Snowballing to {target_count} included papers...")
        keywords = self._extract_keywords()
        conn = self.db.get_connection()
        
        iteration = 0
        while True:
            iteration += 1
            include_count = conn.execute(
                "SELECT COUNT(*) FROM papers WHERE screening_status = 'include' AND abstract IS NOT NULL AND abstract != ''"
            ).fetchone()[0]
            print(f"  Round {iteration}: {include_count} included papers with abstracts.")
            
            if include_count >= target_count:
                break
            if iteration > 15:
                print("⚠️  Max rounds reached.")
                break
            
            # Collect referenced_works from included papers
            rows = conn.execute("""
                SELECT referenced_works_json FROM papers 
                WHERE screening_status = 'include' AND referenced_works_json IS NOT NULL AND referenced_works_json != '[]'
            """).fetchall()
            
            all_refs = set()
            for row in rows:
                for ref in json.loads(row[0]):
                    all_refs.add(ref.split('/')[-1] if '/' in ref else ref)
            
            existing = {r[0] for r in conn.execute("SELECT paper_id FROM papers").fetchall()}
            new_refs = list(all_refs - existing)
            
            # Fetch new references
            if new_refs:
                for i in range(0, len(new_refs), 50):
                    batch = new_refs[i:i+50]
                    print(f"  Fetching {len(batch)} new references...")
                    results = self.searcher.get_works_by_ids(batch)
                    self.ingest_results(results)
                    time.sleep(0.3)
                conn.close()
                conn = self.db.get_connection()
            
            # Relevance-scored auto-include
            needed = target_count - conn.execute(
                "SELECT COUNT(*) FROM papers WHERE screening_status = 'include' AND abstract IS NOT NULL AND abstract != ''"
            ).fetchone()[0]
            
            if needed <= 0:
                break
            
            # Score pending papers by relevance to prompt
            pending = conn.execute("""
                SELECT paper_id, title, abstract, citation_count 
                FROM papers 
                WHERE screening_status = 'pending' AND abstract IS NOT NULL AND abstract != ''
            """).fetchall()
            
            scored = []
            for pid, title, abstract, cc in pending:
                title_score = compute_relevance(title or '', keywords)
                abstract_score = compute_relevance(abstract or '', keywords)
                relevance = title_score * 2 + abstract_score  # Title weighted higher
                citation_bonus = min((cc or 0) / 10000, 0.5)  # Cap citation bonus
                scored.append((pid, relevance + citation_bonus))
            
            scored.sort(key=lambda x: x[1], reverse=True)
            to_include = [pid for pid, score in scored[:needed]]
            
            for pid in to_include:
                conn.execute("UPDATE papers SET screening_status = 'include' WHERE paper_id = ?", [pid])
            
            print(f"  Auto-included {len(to_include)} papers by relevance score.")
            if not to_include and not new_refs:
                break
        
        conn.close()
        final = self.db.get_connection().execute(
            "SELECT COUNT(*) FROM papers WHERE screening_status = 'include' AND abstract IS NOT NULL AND abstract != ''"
        ).fetchone()[0]
        print(f"❄️  Snowball complete: {final} included papers with abstracts.")
        self.pipeline.mark_step("snowball", {"target": target_count, "achieved": final})

    # ═══════════════════════════════════════════════════════════
    # STEP 3: KNOWLEDGE EXTRACTION
    # ═══════════════════════════════════════════════════════════
    def run_knowledge_extraction(self):
        """Prepare extraction prompts for all included papers without knowledge."""
        print("🧠 STEP 3: Preparing knowledge extraction...")
        conn = self.db.get_connection()
        
        papers = conn.execute("""
            SELECT p.paper_id, p.title, p.abstract, p.year, p.journal, p.referenced_works_json
            FROM papers p
            WHERE p.screening_status = 'include' 
            AND p.abstract IS NOT NULL AND p.abstract != ''
            AND p.paper_id NOT IN (SELECT DISTINCT paper_id FROM knowledge)
            ORDER BY p.citation_count DESC NULLS LAST
        """).fetchall()
        
        conn.close()
        
        if not papers:
            print("  All included papers already have knowledge extracted.")
            self.pipeline.mark_step("extraction", {"papers_processed": 0})
            return
        
        # Write extraction prompts to a batch file for the agent
        prompts_dir = os.path.join(self.data_dir, "extraction_prompts")
        os.makedirs(prompts_dir, exist_ok=True)
        
        for i, (pid, title, abstract, year, journal, ref_json) in enumerate(papers):
            refs = json.loads(ref_json) if ref_json else []
            ref_list = "\n".join([f"  - {r}" for r in refs[:15]])
            
            prompt = f"""Extract ALL scientific knowledge points from this paper.

PAPER: "{title}" ({year}, {journal})
ABSTRACT:
{abstract}

REFERENCES (first 15):
{ref_list if ref_list else '  (none)'}

For EACH knowledge point, output JSON:
{{
  "knowledge_text": "The scientific statement",
  "knowledge_type": "mechanism|result|method|limitation|structural|design|interaction|finding|hypothesis|comparison",
  "source_type": "original (this paper's finding) | referenced (cited work) | unknown",
  "original_reference_id": "paper_id if original, or reference_id if referenced, or 'unknown'",
  "confidence_score": 0.0-1.0
}}

Output as JSON array. Extract 3-15 points."""
            
            with open(os.path.join(prompts_dir, f"{pid}.txt"), 'w') as f:
                f.write(prompt)
        
        print(f"  Generated {len(papers)} extraction prompts in {prompts_dir}")
        print(f"  The orchestrating agent should process these via Task calls.")
        self.pipeline.mark_step("extraction", {"papers_to_process": len(papers), "prompts_dir": prompts_dir})

    # ═══════════════════════════════════════════════════════════
    # STEP 4: DUMP KNOWLEDGE FOR ARCHITECTURE DESIGN
    # ═══════════════════════════════════════════════════════════
    def dump_knowledge_for_agent(self) -> str:
        """Dump all knowledge as a formatted text for the architecture design agent."""
        conn = self.db.get_connection()
        rows = conn.execute("""
            SELECT k.knowledge_id, k.knowledge_text, k.knowledge_type, 
                   k.source_type, k.original_reference_id, k.paper_id,
                   p.title, p.year
            FROM knowledge k
            JOIN papers p ON k.paper_id = p.paper_id
            ORDER BY k.knowledge_type, p.citation_count DESC NULLS LAST
        """).fetchall()
        conn.close()
        
        output = []
        for kid, text, ktype, stype, orig_ref, pid, title, year in rows:
            output.append(f"[{ktype.upper()}|{stype}] {text}")
            output.append(f"  → Source: {orig_ref} | Paper: {title} ({year})\n")
        
        dump = f"Total knowledge points: {len(rows)}\n\n" + "\n".join(output)
        
        dump_path = os.path.join(self.data_dir, "knowledge_dump.md")
        with open(dump_path, 'w') as f:
            f.write(dump)
        
        print(f"  Knowledge dump: {len(rows)} points → {dump_path}")
        return dump_path

    # ═══════════════════════════════════════════════════════════
    # STEP 5: FULLTEXT ACQUISITION
    # ═══════════════════════════════════════════════════════════
    def run_fulltext_acquisition(self, max_papers: int = 30):
        """Acquire full text. Priority: OpenAlex OA > UVA PMC Proxy."""
        print(f"📥 STEP 5: Full-text acquisition (max {max_papers})...")
        conn = self.db.get_connection()
        papers = conn.execute("""
            SELECT paper_id, doi, pmcid FROM papers 
            WHERE screening_status = 'include' AND fulltext_status = 'none'
            ORDER BY citation_count DESC NULLS LAST LIMIT ?
        """, [max_papers]).fetchall()
        
        fulltext_dir = os.path.join(self.data_dir, "fulltext")
        os.makedirs(fulltext_dir, exist_ok=True)
        success = 0
        
        for paper_id, doi, pmcid in papers:
            fetched = False
            if doi:
                oa = get_oa_link(doi)
                if oa:
                    path = os.path.join(fulltext_dir, f"{paper_id}.{oa.format}")
                    if download_file(oa.url, path):
                        conn.execute("UPDATE papers SET fulltext_status='fetched', fulltext_path=?, access_method=? WHERE paper_id=?",
                                   [path, oa.source, paper_id])
                        fetched = True; success += 1
                        print(f"  ✅ {paper_id} via {oa.source}")
            
            if not fetched and pmcid:
                try:
                    self.pmc_fetcher.start()
                    path, method = self.pmc_fetcher.fetch_article(pmcid)
                    if path:
                        conn.execute("UPDATE papers SET fulltext_status='fetched', fulltext_path=?, access_method=? WHERE paper_id=?",
                                   [path, method, paper_id])
                        fetched = True; success += 1
                        print(f"  ✅ {paper_id} via {method}")
                except: pass
            
            if not fetched:
                print(f"  ⏭ {paper_id} - skipped")
        
        conn.close()
        self.pmc_fetcher.stop()
        print(f"📥 Full-text: {success}/{len(papers)} fetched.")
        self.pipeline.mark_step("fulltext", {"attempted": len(papers), "success": success})

    # ═══════════════════════════════════════════════════════════
    # CITATION SYNC & RENDERING
    # ═══════════════════════════════════════════════════════════
    def auto_sync_citations(self):
        sections_dir = os.path.join(self.workspace, "outputs/sections")
        if not os.path.exists(sections_dir): return
        cite_pattern = re.compile(r'\\cite\{([^}]+)\}')
        cited_ids = set()
        for f in os.listdir(sections_dir):
            if f.endswith('.tex'):
                with open(os.path.join(sections_dir, f), 'r') as file:
                    for m in cite_pattern.findall(file.read()):
                        cited_ids.update([i.strip() for i in m.split(',')])
        conn = self.db.get_connection()
        for pid in cited_ids:
            conn.execute("UPDATE papers SET screening_status = 'include' WHERE paper_id = ?", [pid])
        conn.close()
        print(f"🔄 Synced {len(cited_ids)} citations.")

    def verify_density(self):
        print("🕵️  Verifying Density...")
        sections_dir = os.path.join(self.workspace, "outputs/sections")
        if not os.path.exists(sections_dir): return True
        failures, word_counts = [], {}
        sec_files = sorted([f for f in os.listdir(sections_dir) if re.match(r'sec\d+\.tex', f)],
                          key=lambda x: int(re.search(r'\d+', x).group()))
        for fname in sec_files:
            ch = int(re.search(r'\d+', fname).group())
            with open(os.path.join(sections_dir, fname)) as f:
                content = f.read()
                wc = len(content.split())
                word_counts[ch] = wc
                subs = re.split(r'\\subsection\{', content)
                for sub in subs[1:]:
                    paras = [p for p in re.split(r'\\par|\n\s*\n', sub) if len(p.strip()) > 50]
                    if len(paras) < 2:
                        failures.append(f"Chapter {ch}: {len(paras)} paragraphs")
        total = sum(word_counts.values())
        print(f"  Total: {total} words across {len(sec_files)} chapters.")
        for ch, wc in sorted(word_counts.items()):
            print(f"    Ch{ch}: {wc} words")
        if failures:
            print("❌ Density issues:", failures)
            return False
        print("✨ Density OK.")
        return True

    def run_render(self, force=False):
        self.auto_sync_citations()
        if not self.verify_density() and not force:
            print("🛑 Density audit failed. Use --force.")
            return
        print(f"🎨 Rendering {self.slug}...")
        sections_dir = os.path.join(self.workspace, "outputs/sections")
        bib_path = os.path.join(self.workspace, "outputs/references.bib")
        generate_hardened_bib(self.db.db_path, sections_dir, bib_path)
        clean_file(bib_path)
        if os.path.exists(sections_dir):
            for f in os.listdir(sections_dir):
                if f.endswith('.tex'): clean_file(os.path.join(sections_dir, f))
        clean_file(os.path.join(self.workspace, "outputs/main.tex"))
        subprocess.run(["tectonic", "main.tex"], cwd=os.path.join(self.workspace, "outputs"))
        pdf_path = os.path.join(self.workspace, "outputs", "main.pdf")
        if os.path.exists(pdf_path):
            size = os.path.getsize(pdf_path)
            print(f"🏁 PDF: {pdf_path} ({size/1024:.0f} KB)")
        else:
            print("❌ PDF generation failed. Check logs.")

    # ═══════════════════════════════════════════════════════════
    # AUTO-PILOT: FULL PIPELINE
    # ═══════════════════════════════════════════════════════════
    def run_auto_pilot(self, queries: List[str], target_count: int = 150):
        """Execute the full automated pipeline with agent checkpoints."""
        print(f"\n{'='*60}")
        print(f"🚀 AUTO-PILOT: '{self.prompt}'")
        print(f"{'='*60}\n")
        
        # Step 1: Acquisition
        if not self.pipeline.is_complete("acquisition"):
            self.run_acquisition(queries)
        else:
            print("⏭  Acquisition already complete.")
        
        # Step 2: Snowball
        if not self.pipeline.is_complete("snowball"):
            self.run_snowball(target_count=target_count)
        else:
            print("⏭  Snowball already complete.")
        
        # Step 3: Knowledge Extraction (prepare prompts)
        if not self.pipeline.is_complete("extraction"):
            self.run_knowledge_extraction()
        else:
            print("⏭  Extraction already prepared.")
        
        # Step 4: Dump knowledge for architecture
        dump_path = self.dump_knowledge_for_agent()
        
        # Print checkpoint for agent
        conn = self.db.get_connection()
        kn_count = conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]
        papers_count = conn.execute("SELECT COUNT(*) FROM papers WHERE screening_status = 'include' AND abstract IS NOT NULL AND abstract != ''").fetchone()[0]
        conn.close()
        
        print(f"\n{'='*60}")
        print(f"📋 PIPELINE CHECKPOINT")
        print(f"{'='*60}")
        print(f"  Papers included: {papers_count}")
        print(f"  Knowledge points: {kn_count}")
        print(f"  Knowledge dump: {dump_path}")
        print(f"  Prompts dir: {os.path.join(self.data_dir, 'extraction_prompts')}")
        print(f"\n  Next steps for the orchestrating agent:")
        print(f"  1. Process extraction prompts (if kn_count < papers_count * 3)")
        print(f"  2. Read knowledge dump and design chapter architecture")
        print(f"  3. Write chapters sequentially with knowledge blocks")
        print(f"  4. Run: python3 manager.py --prompt '{self.prompt}' --render")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--queries", nargs='+')
    parser.add_argument("--auto-pilot", action="store_true", help="Full automated pipeline")
    parser.add_argument("--snowball", action="store_true")
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--fetch-fulltext", action="store_true")
    parser.add_argument("--extract-knowledge", action="store_true")
    parser.add_argument("--dump-knowledge", action="store_true")
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--ingest-selected")
    parser.add_argument("--target-count", type=int, default=150)
    parser.add_argument("--max-fulltext", type=int, default=30)
    args = parser.parse_args()
    
    orch = ReviewOrchestrator(prompt=args.prompt)
    
    if args.auto_pilot:
        orch.run_auto_pilot(queries=args.queries or [], target_count=args.target_count)
    elif args.ingest_selected:
        ids = re.split(r'[,\s]+', args.ingest_selected.strip())
        orch.ingest_results(orch.searcher.get_works_by_ids(ids))
        conn = orch.db.get_connection()
        for sid in ids:
            conn.execute("UPDATE papers SET screening_status = 'include' WHERE paper_id = ?", [sid])
        conn.close()
    elif args.snowball:
        orch.run_snowball(target_count=args.target_count)
    elif args.fetch_fulltext:
        orch.run_fulltext_acquisition(max_papers=args.max_fulltext)
    elif args.extract_knowledge:
        orch.run_knowledge_extraction()
    elif args.dump_knowledge:
        orch.dump_knowledge_for_agent()
    elif args.fetch:
        orch.run_acquisition(args.queries or [])
    elif args.render:
        orch.run_render(force=args.force)
