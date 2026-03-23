"""
Extract structured claims from paper abstracts into the claims table.
This runs entirely on the abstract data already in the database.
"""

import duckdb
import json
import uuid
import os

DB_PATH = "/Users/juw1179/Codes/review-os/workspaces/rna-design-based-on-tertiary-structures/db/review.duckdb"

def extract_claims_from_abstracts():
    """Extract key claims from all included papers' abstracts."""
    conn = duckdb.connect(DB_PATH)
    
    papers = conn.execute("""
        SELECT paper_id, title, abstract, year, journal 
        FROM papers 
        WHERE screening_status = 'include' AND abstract IS NOT NULL AND abstract != ''
    """).fetchall()
    
    print(f"Found {len(papers)} papers with abstracts to process.")
    
    claims_added = 0
    for paper_id, title, abstract, year, journal in papers:
        if not abstract or len(abstract.strip()) < 50:
            continue
        
        # Split abstract into sentences as individual claims
        sentences = _split_into_claims(abstract, title)
        
        for i, (claim_text, claim_type) in enumerate(sentences):
            claim_id = f"{paper_id}_c{i+1}"
            conn.execute("""
                INSERT OR IGNORE INTO claims (claim_id, paper_id, claim_text, claim_type, evidence_span, directness_score, confidence_score)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, [claim_id, paper_id, claim_text, claim_type, 'abstract', 0.7, 0.8])
            claims_added += 1
    
    conn.close()
    print(f"Extracted {claims_added} claims from {len(papers)} papers.")
    return claims_added


def _split_into_claims(abstract, title):
    """Split an abstract into individual claims with types."""
    import re
    
    claims = []
    
    # Split by sentence boundaries
    sentences = re.split(r'(?<=[.!?])\s+', abstract.strip())
    
    for sent in sentences:
        sent = sent.strip()
        if len(sent) < 20:
            continue
        
        # Classify claim type based on keywords
        sent_lower = sent.lower()
        
        if any(w in sent_lower for w in ['we propose', 'we present', 'we introduce', 'we develop', 'here we', 'we designed', 'we created', 'our method', 'our approach', 'we report']):
            claim_type = 'method'
        elif any(w in sent_lower for w in ['we show', 'we demonstrate', 'we find', 'results show', 'our results', 'we reveal', 'we discovered', 'we observe', 'we identify', 'we achieved', 'accuracy', 'performance', 'outperform']):
            claim_type = 'result'
        elif any(w in sent_lower for w in ['we suggest', 'we hypothesize', 'could be', 'may be', 'might', 'potentially', 'future', 'we speculate', 'promising']):
            claim_type = 'hypothesis'
        elif any(w in sent_lower for w in ['however', 'although', 'despite', 'limitation', 'challenge', 'difficult', 'remains', 'problem', 'bottleneck']):
            claim_type = 'limitation'
        elif any(w in sent_lower for w in ['compared to', 'in contrast', 'unlike', 'superior', 'better than', 'outperform', 'benchmark']):
            claim_type = 'comparison'
        elif any(w in sent_lower for w in ['structure', 'fold', 'conformation', 'motif', 'helix', 'tertiary', 'three-dimensional', '3d', 'architecture', 'geometr']):
            claim_type = 'structural'
        elif any(w in sent_lower for w in ['design', 'engineer', 'synthetic', 'de novo', 'rational', 'computational', 'algorithm', 'model', 'predict']):
            claim_type = 'design'
        elif any(w in sent_lower for w in ['bind', 'interaction', 'affinity', 'recognize', 'receptor', 'ligand', 'aptamer', 'protein-rna']):
            claim_type = 'interaction'
        else:
            claim_type = 'finding'
        
        # Truncate very long sentences
        if len(sent) > 500:
            sent = sent[:497] + "..."
        
        claims.append((sent, claim_type))
    
    return claims


def dump_claims_for_prompt():
    """Dump all claims organized by paper for use in prompts."""
    conn = duckdb.connect(DB_PATH)
    
    papers = conn.execute("""
        SELECT p.paper_id, p.title, p.year, p.journal
        FROM papers p
        WHERE p.screening_status = 'include'
        ORDER BY p.citation_count DESC NULLS LAST
    """).fetchall()
    
    output = []
    for paper_id, title, year, journal in papers:
        claims = conn.execute("""
            SELECT claim_text, claim_type 
            FROM claims 
            WHERE paper_id = ?
            ORDER BY claim_type
        """, [paper_id]).fetchall()
        
        if claims:
            output.append(f"### [{paper_id}] {title} ({year}, {journal or 'Unknown'})")
            for claim_text, claim_type in claims:
                output.append(f"  - [{claim_type.upper()}] {claim_text}")
            output.append("")
    
    conn.close()
    return "\n".join(output)


if __name__ == "__main__":
    extract_claims_from_abstracts()
    
    # Dump to file for inspection
    dump = dump_claims_for_prompt()
    out_path = "/Users/juw1179/Codes/review-os/workspaces/rna-design-based-on-tertiary-structures/data/claims_dump.md"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        f.write(dump)
    print(f"Claims dump written to {out_path}")
