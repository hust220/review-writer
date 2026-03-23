"""
Database Manager for Review-OS Agentic Plugin.
Handles DuckDB connections and basic operations.
"""

import os
import duckdb
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DatabaseManager:
    def __init__(self, db_path: Optional[str] = None, schema_path: Optional[str] = None):
        # Default paths relative to CURRENT WORKING DIRECTORY
        cwd = os.getcwd()
        self.db_path = db_path or os.path.join(cwd, "db/review_os_v2.duckdb")
        # Schema path remains relative to the script location (part of the skill)
        if schema_path is None:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            self.schema_path = os.path.join(script_dir, "schema.sql")
        else:
            self.schema_path = schema_path
            
        self._initialize_db()
    
    def _initialize_db(self):
        """Initialize the database with the schema."""
        db_dir = Path(self.db_path).parent
        db_dir.mkdir(parents=True, exist_ok=True)
        
        conn = duckdb.connect(self.db_path)
        try:
            with open(self.schema_path, 'r') as f:
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
                
            logger.info("Database initialized successfully.")
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
            # Simple upsert logic for DuckDB
            exists = conn.execute("SELECT 1 FROM papers WHERE paper_id = ?", [paper_data['paper_id']]).fetchone()
            
            if exists:
                # Build update statement
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
                # Build insert statement
                fields = list(paper_data.keys())
                placeholders = ["?"] * len(fields)
                values = list(paper_data.values())
                query = f"INSERT INTO papers ({', '.join(fields)}) VALUES ({', '.join(placeholders)})"
                conn.execute(query, values)
        finally:
            conn.close()

    def get_papers_needing_pmcid(self, limit: int = 100) -> List[Dict]:
        """Get papers that have DOI/PMID but no PMCID."""
        conn = self.get_connection()
        try:
            return conn.execute("""
                SELECT paper_id, doi, pmid 
                FROM papers 
                WHERE pmcid IS NULL 
                AND (doi IS NOT NULL OR pmid IS NOT NULL)
                LIMIT ?
            """, [limit]).fetchdf().to_dict('records')
        finally:
            conn.close()
    
    def update_pmcids(self, mapping: Dict[str, str]):
        """Update PMCIDs in the database based on DOI/PMID mapping."""
        conn = self.get_connection()
        try:
            for original_id, pmcid in mapping.items():
                # Try updating by DOI then PMID
                conn.execute("UPDATE papers SET pmcid = ? WHERE doi = ? OR pmid = ?", [pmcid, original_id, original_id])
            logger.info(f"Updated {len(mapping)} PMCIDs.")
        finally:
            conn.close()
