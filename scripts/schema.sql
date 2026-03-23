-- Review-OS Agentic Plugin Schema (v14.0 - Knowledge-Driven)

-- Papers Table: Tracks all identified papers and their PMCID
CREATE TABLE IF NOT EXISTS papers (
    paper_id VARCHAR PRIMARY KEY,  -- OpenAlex ID or DOI
    doi VARCHAR,
    pmid VARCHAR,
    pmcid VARCHAR,
    title VARCHAR,
    abstract TEXT,
    year INTEGER,
    journal VARCHAR,
    volume VARCHAR, -- New: Journal volume
    issue VARCHAR,  -- New: Journal issue
    pages VARCHAR,  -- New: Page numbers
    authors_json TEXT,
    referenced_works_json TEXT, -- New: List of OpenAlex IDs cited by this paper
    citation_count INTEGER,
    oa_status VARCHAR DEFAULT 'unknown',
    fulltext_status VARCHAR DEFAULT 'none', -- none, fetched, parsed
    fulltext_path VARCHAR,
    access_method VARCHAR, -- pmc_oa, pmc_proxy, publisher_oa, publisher_proxy
    screening_status VARCHAR DEFAULT 'pending', -- pending, include, maybe, exclude
    relevance_score FLOAT,
    screening_reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Claims Table: Stores extracted molecular mechanisms and evidence
CREATE TABLE IF NOT EXISTS claims (
    claim_id VARCHAR PRIMARY KEY,
    paper_id VARCHAR,
    claim_text TEXT,
    claim_type VARCHAR, -- mechanism, association, therapeutic, contradiction, other
    evidence_span TEXT,
    page_or_section VARCHAR,
    directness_score FLOAT,
    confidence_score FLOAT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Topics Table: Defines the research directions identified by the Researcher Agent
CREATE TABLE IF NOT EXISTS topics (
    topic_id VARCHAR PRIMARY KEY,
    name VARCHAR,
    description TEXT,
    parent_topic_id VARCHAR,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Claim-Topic Links: M:N relationship
CREATE TABLE IF NOT EXISTS claim_topic_links (
    claim_id VARCHAR,
    topic_id VARCHAR,
    relevance_score FLOAT,
    PRIMARY KEY (claim_id, topic_id)
);

-- Draft Units Table: Stores the generated content for each topic
CREATE TABLE IF NOT EXISTS draft_units (
    unit_id VARCHAR PRIMARY KEY,
    topic_id VARCHAR,
    content TEXT,
    evidence_table_json TEXT,
    status VARCHAR DEFAULT 'draft', -- draft, final
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Knowledge Table: Stores extracted knowledge points with citation fidelity
CREATE TABLE IF NOT EXISTS knowledge (
    knowledge_id VARCHAR PRIMARY KEY,
    paper_id VARCHAR,            -- The paper where this knowledge was observed
    original_reference_id VARCHAR, -- The true source: either paper_id itself (new finding) or the cited reference
    source_type VARCHAR,         -- 'original' (from paper_id), 'referenced' (cited in paper), 'unknown'
    knowledge_text TEXT,
    knowledge_type VARCHAR,      -- mechanism, result, method, limitation, structural, design, interaction, finding, hypothesis, comparison
    evidence_span VARCHAR,       -- abstract, fulltext
    page_or_section VARCHAR,     -- which section of the paper (e.g., "Results", "Discussion")
    confidence_score FLOAT,      -- 0.0-1.0, how confident the extraction is
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Reference Stubs Table: Papers cited by primary papers but not in the main collection
CREATE TABLE IF NOT EXISTS reference_stubs (
    stub_id VARCHAR PRIMARY KEY,
    openalex_id VARCHAR,
    doi VARCHAR,
    title VARCHAR,
    year INTEGER,
    journal VARCHAR,
    abstract TEXT,
    cited_by_paper VARCHAR,      -- Which primary paper references this stub
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Knowledge-Chapter Links: Maps knowledge points to chapter themes
CREATE TABLE IF NOT EXISTS knowledge_chapter_links (
    knowledge_id VARCHAR,
    chapter_tag VARCHAR,         -- Agent-generated theme tag (e.g., "structural_motifs", "dynamics")
    relevance_score FLOAT,
    PRIMARY KEY (knowledge_id, chapter_tag)
);
