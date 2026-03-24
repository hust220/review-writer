"""
Pipeline state helpers for UniversalReviewer.
Centralizes workspace discovery, stage gating, and next-step guidance.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from core.db import DatabaseManager


TARGET_INCLUDED_DEFAULT = 80
TARGET_FULLTEXT_DEFAULT = 20


@dataclass
class WorkspaceState:
    workspace: str
    db_path: str
    prompt: str
    outputs_dir: str
    data_dir: str
    sections_dir: str
    queries_path: str
    blueprint_path: str
    workspace_initialized: bool
    query_count: int
    candidate_count: int
    pending_count: int
    included_count: int
    maybe_count: int
    excluded_count: int
    fetched_count: int
    citation_paper_count: int
    knowledge_count: int
    summaries_count: int
    blueprint_exists: bool
    chapter_count: int
    total_blueprint_chapters: int
    target_included: int
    target_fulltext: int


def infer_workspace_from_db(db_path: str) -> str:
    db_file = Path(db_path).resolve()
    return str(db_file.parent.parent)


def _load_workspace_prompt(workspace: str) -> str:
    info_path = Path(workspace) / "workspace_info.json"
    if not info_path.exists():
        return ""
    try:
        with open(info_path, "r", encoding="utf-8") as handle:
            return json.load(handle).get("prompt", "")
    except (OSError, json.JSONDecodeError):
        return ""


def _load_query_count(queries_path: str) -> int:
    if not os.path.exists(queries_path):
        return 0
    try:
        with open(queries_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return len(data) if isinstance(data, list) else 0
    except (OSError, json.JSONDecodeError):
        return 0


def _load_blueprint_meta(blueprint_path: str) -> Dict[str, int]:
    if not os.path.exists(blueprint_path):
        return {"exists": 0, "chapters": 0}
    try:
        with open(blueprint_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return {
            "exists": 1,
            "chapters": len(data.get("chapters", [])) if isinstance(data, dict) else 0,
        }
    except (OSError, json.JSONDecodeError):
        return {"exists": 0, "chapters": 0}


def load_workspace_state(
    db_path: str,
    target_included: int = TARGET_INCLUDED_DEFAULT,
    target_fulltext: int = TARGET_FULLTEXT_DEFAULT,
) -> WorkspaceState:
    workspace = infer_workspace_from_db(db_path)
    outputs_dir = os.path.join(workspace, "outputs")
    data_dir = os.path.join(workspace, "data")
    sections_dir = os.path.join(outputs_dir, "sections")
    queries_path = os.path.join(outputs_dir, "search_queries.json")
    blueprint_path = os.path.join(outputs_dir, "blueprint.json")

    db = DatabaseManager(db_path=db_path)
    conn = db.get_connection()
    try:
        counts = conn.execute(
            """
            SELECT
                COUNT(*) AS candidate_count,
                SUM(CASE WHEN screening_status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
                SUM(CASE WHEN screening_status = 'include' THEN 1 ELSE 0 END) AS included_count,
                SUM(CASE WHEN screening_status = 'maybe' THEN 1 ELSE 0 END) AS maybe_count,
                SUM(CASE WHEN screening_status = 'exclude' THEN 1 ELSE 0 END) AS excluded_count,
                SUM(CASE WHEN fulltext_status = 'fetched' THEN 1 ELSE 0 END) AS fetched_count,
                SUM(CASE WHEN paper_role = 'citation' THEN 1 ELSE 0 END) AS citation_paper_count
            FROM papers
            """
        ).fetchone()
        knowledge_count = conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0]
        summaries_count = conn.execute("SELECT COUNT(*) FROM summaries").fetchone()[0]
    finally:
        conn.close()

    chapter_count = 0
    if os.path.isdir(sections_dir):
        chapter_count = len(
            [
                name
                for name in os.listdir(sections_dir)
                if name.endswith(".tex") and name != "main.tex" and (name.startswith("ch") or name.startswith("sec"))
            ]
        )

    blueprint_meta = _load_blueprint_meta(blueprint_path)

    return WorkspaceState(
        workspace=workspace,
        db_path=os.path.abspath(db_path),
        prompt=_load_workspace_prompt(workspace),
        outputs_dir=outputs_dir,
        data_dir=data_dir,
        sections_dir=sections_dir,
        queries_path=queries_path,
        blueprint_path=blueprint_path,
        workspace_initialized=os.path.exists(os.path.join(workspace, "workspace_info.json")),
        query_count=_load_query_count(queries_path),
        candidate_count=counts[0] or 0,
        pending_count=counts[1] or 0,
        included_count=counts[2] or 0,
        maybe_count=counts[3] or 0,
        excluded_count=counts[4] or 0,
        fetched_count=counts[5] or 0,
        citation_paper_count=counts[6] or 0,
        knowledge_count=knowledge_count or 0,
        summaries_count=summaries_count or 0,
        blueprint_exists=bool(blueprint_meta["exists"]),
        chapter_count=chapter_count,
        total_blueprint_chapters=blueprint_meta["chapters"],
        target_included=target_included,
        target_fulltext=target_fulltext,
    )


def get_next_action(state: WorkspaceState) -> str:
    if not state.workspace_initialized:
        return "init"
    if state.query_count == 0:
        return "queries"
    if state.candidate_count == 0:
        return "acquire"
    if state.included_count == 0:
        return "screen"
    if state.fetched_count < min(state.included_count, state.target_fulltext):
        return "fetch"
    if state.knowledge_count == 0 and state.summaries_count == 0:
        return "extract"
    if state.summaries_count > 0 and state.citation_paper_count < 60:
        return "references"
    if not state.blueprint_exists:
        return "design"
    if state.chapter_count < max(1, state.total_blueprint_chapters):
        return "write"
    if state.pending_count > 0:
        return "screen"
    return "render"


def describe_blockers(action: str, state: WorkspaceState, chapter_tag: Optional[str] = None) -> List[str]:
    blockers: List[str] = []

    if action in {"acquire", "screen", "snowball", "fetch", "extract", "references", "design", "write", "render"} and state.query_count == 0:
        blockers.append(f"Missing search query file: {state.queries_path}")

    if action in {"screen", "snowball", "fetch", "extract", "references", "design", "write", "render"} and state.candidate_count == 0:
        blockers.append("No papers have been ingested. Run OpenAlex acquisition first.")

    if action in {"snowball", "fetch", "extract", "references", "design", "write", "render"} and state.included_count == 0:
        blockers.append("No papers are marked include. Complete abstract screening first.")

    if action == "snowball" and state.pending_count > 0:
        blockers.append("Pending screening decisions exist. Clear pending papers before snowballing.")

    if action == "extract" and state.fetched_count == 0:
        blockers.append("No included papers have fetched full text.")

    if action == "references" and state.summaries_count == 0:
        blockers.append("No extraction summaries are available for citation expansion.")

    if action in {"design", "write", "render"} and (state.knowledge_count + state.summaries_count == 0):
        blockers.append("No extracted knowledge or summaries are available.")

    if action in {"write", "render"} and not state.blueprint_exists:
        blockers.append(f"Missing blueprint file: {state.blueprint_path}")

    if action == "render" and state.chapter_count == 0:
        blockers.append("No chapter .tex files exist in outputs/sections.")

    if action == "write" and chapter_tag and state.blueprint_exists:
        blockers.extend(_check_write_sequence(state, chapter_tag))

    return blockers


def _check_write_sequence(state: WorkspaceState, chapter_tag: str) -> List[str]:
    try:
        with open(state.blueprint_path, "r", encoding="utf-8") as handle:
            blueprint = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return ["Blueprint exists but could not be parsed."]

    chapters = blueprint.get("chapters", [])
    current = None
    for index, chapter in enumerate(chapters):
        tag = chapter.get("tag") or chapter.get("chapter_id")
        if tag == chapter_tag:
            current = (index + 1, chapter)
            break

    if not current:
        return [f"Chapter tag '{chapter_tag}' was not found in blueprint."]

    number = current[0]
    if number <= 1:
        return []

    previous_a = os.path.join(state.sections_dir, f"ch{number - 1}.tex")
    previous_b = os.path.join(state.sections_dir, f"sec{number - 1}.tex")
    if not os.path.exists(previous_a) and not os.path.exists(previous_b):
        return [f"Previous chapter is missing. Write chapter {number - 1} before {chapter_tag}."]
    return []


def ensure_stage_ready(action: str, db_path: str, chapter_tag: Optional[str] = None) -> WorkspaceState:
    state = load_workspace_state(db_path)
    blockers = describe_blockers(action, state, chapter_tag=chapter_tag)
    if blockers:
        joined = "\n- ".join(blockers)
        raise RuntimeError(f"Stage '{action}' is blocked:\n- {joined}")
    return state


def recommended_command(action: str, state: WorkspaceState) -> str:
    scripts_dir = Path(__file__).resolve().parent
    if action == "queries":
        return (
            f'python "{scripts_dir / "step0b_queries.py"}" --workspace "{state.workspace}"'
        )
    if action == "acquire":
        return (
            f'python "{scripts_dir / "step1_acquire.py"}" --db-path "{state.db_path}" '
            f'--queries-json "{state.queries_path}"'
        )
    if action == "screen":
        return (
            f'python "{scripts_dir / "step2_screen.py"}" --db-path "{state.db_path}" '
            f'--create-bundles --output-dir "{Path(state.data_dir) / "screening_bundles"}" '
            f'--topic "{state.prompt or "Research topic"}"'
        )
    if action == "fetch":
        return (
            f'python "{scripts_dir / "step4_fetch.py"}" --db-path "{state.db_path}" '
            f'--output-dir "{Path(state.data_dir) / "fulltext"}"'
        )
    if action == "extract":
        return (
            f'python "{scripts_dir / "step5_extract.py"}" --db-path "{state.db_path}" '
            f'--prepare-prompts --output-dir "{Path(state.data_dir) / "extraction_prompts"}"'
        )
    if action == "references":
        return (
            f'python "{scripts_dir / "step3b_references.py"}" --db-path "{state.db_path}"'
        )
    if action == "design":
        return (
            f'python "{scripts_dir / "step6_design.py"}" --db-path "{state.db_path}" --dump'
        )
    if action == "write":
        return (
            f'python "{scripts_dir / "step7_write.py"}" --db-path "{state.db_path}" '
            f'--chapter-tag "<chapter_tag>" --blueprint "{state.blueprint_path}" '
            f'--sections-dir "{state.sections_dir}" --output "<context.md>"'
        )
    return (
        f'python "{scripts_dir / "step8_render.py"}" --db-path "{state.db_path}" '
        f'--sections-dir "{state.sections_dir}" --output-pdf "{Path(state.outputs_dir) / "review.pdf"}"'
    )
