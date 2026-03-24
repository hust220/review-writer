"""
Core Database Module for UniversalReviewer.
Handles DuckDB connections and operations.
"""

import os
import duckdb
import logging
import sys
from typing import List, Dict, Any, Optional
from pathlib import Path

# Fix UTF-8 encoding for Windows
if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self, db_path: Optional[str] = None):
        """
        Initialize the database manager.
        If db_path is not provided, it defaults to 'db/review.duckdb' in the CURRENT directory.
        """
        self.db_path = db_path or os.path.join(os.getcwd(), "db/review.duckdb")
        
        # Schema path is relative to THIS script
        script_dir = os.path.dirname(os.path.abspath(__file__))
        self.schema_path = os.path.join(os.path.dirname(script_dir), "schema.sql")
            
        self._initialize_db()
    
    def _initialize_db(self):
        """Initialize the database with the schema."""
        db_dir = Path(self.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)
        
        conn = duckdb.connect(self.db_path)
        try:
            if not os.path.exists(self.schema_path):
                logger.error(f"Schema file not found at {self.schema_path}")
                return

            with open(self.schema_path, 'r', encoding='utf-8') as f:
                schema = f.read()
            
            # Split by semicolon but ignore comments
            statements = []
            for stmt in schema.split(';'):
                clean_stmt = ""
                for line in stmt.split('\n'):
                    if not line.strip().startswith('--'):
                        clean_stmt += line + '\n'
                if clean_stmt.strip():
                    statements.append(clean_stmt.strip())
            
            for stmt in statements:
                conn.execute(stmt)
                
            logger.info(f"Database initialized at {self.db_path}")
        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
        finally:
            conn.close()
            
    def get_connection(self):
        """Get a connection to the database."""
        return duckdb.connect(self.db_path)
    
    def upsert_paper(self, paper_data: Dict[str, Any]):
        """Upsert paper metadata."""
        conn = self.get_connection()
        try:
            exists = conn.execute("SELECT 1 FROM papers WHERE paper_id = ?", [paper_data['paper_id']]).fetchone()
            
            if exists:
                fields = []
                values = []
                for k, v in paper_data.items():
                    if k != 'paper_id':
                        fields.append(f"{k} = ?")
                        values.append(v)
                values.append(paper_data['paper_id'])
                query = f"UPDATE papers SET {', '.join(fields)}, updated_at = CURRENT_TIMESTAMP WHERE paper_id = ?"
                conn.execute(query, values)
            else:
                fields = list(paper_data.keys())
                placeholders = ["?"] * len(fields)
                values = list(paper_data.values())
                query = f"INSERT INTO papers ({', '.join(fields)}) VALUES ({', '.join(placeholders)})"
                conn.execute(query, values)
        finally:
            conn.close()

    def update_screening_decision(self, paper_id: str, decision: str, reason: str, needs_fulltext: bool):
        """Update screening status and reason for a paper."""
        conn = self.get_connection()
        try:
            conn.execute("""
                UPDATE papers 
                SET screening_status = ?, screening_reason = ?, needs_fulltext = ?, updated_at = CURRENT_TIMESTAMP
                WHERE paper_id = ?
            """, [decision, reason, needs_fulltext, paper_id])
        finally:
            conn.close()

    def get_papers_needing_pmcid(self, limit: int = 100) -> List[Dict]:
        """Get papers that have DOI/PMID but no PMCID."""
        conn = self.get_connection()
        try:
            # Note: fetchdf requires pandas, using fetchall for portability
            rows = conn.execute("""
                SELECT paper_id, doi, pmid 
                FROM papers 
                WHERE pmcid IS NULL 
                AND (doi IS NOT NULL OR pmid IS NOT NULL)
                LIMIT ?
            """, [limit]).fetchall()
            return [{"paper_id": r[0], "doi": r[1], "pmid": r[2]} for r in rows]
        finally:
            conn.close()
    
    def upsert_knowledge(self, knowledge_data: Dict[str, Any]):
        """Upsert knowledge point."""
        conn = self.get_connection()
        try:
            # Check if this precise knowledge point exists for this paper
            query = "SELECT 1 FROM knowledge WHERE paper_id = ? AND knowledge_text = ?"
            exists = conn.execute(query, [knowledge_data['paper_id'], knowledge_data['knowledge_text']]).fetchone()
            
            if not exists:
                fields = list(knowledge_data.keys())
                placeholders = ["?"] * len(fields)
                values = list(knowledge_data.values())
                query = f"INSERT INTO knowledge ({', '.join(fields)}) VALUES ({', '.join(placeholders)})"
                conn.execute(query, values)
        finally:
            conn.close()

    def link_paper_to_chapter(self, paper_id: str, chapter_tag: str, relevance: float = 0.8):
        """Link a paper to a chapter."""
        conn = self.get_connection()
        try:
            conn.execute("""
                INSERT OR REPLACE INTO paper_chapter_links (paper_id, chapter_tag, relevance_score)
                VALUES (?, ?, ?)
            """, [paper_id, chapter_tag, relevance])
        finally:
            conn.close()
