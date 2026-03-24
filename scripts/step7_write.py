"""
Step 7: Chapter Writing Context.
Builds a writing context package for a single chapter from the database and prior sections.
"""

import argparse
import json
import os
import re
import sys
from typing import Iterable, List

# Add core to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from core.db import DatabaseManager
from pipeline_state import ensure_stage_ready


def sql_placeholders(count: int) -> str:
    return ", ".join(["?"] * count)


def normalize_title(title: str) -> str:
    title = (title or "").lower()
    title = re.sub(r"<[^>]+>", " ", title)
    title = re.sub(r"[^a-z0-9\s]", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def looks_like_title(text: str) -> bool:
    candidate = text.strip()
    if len(candidate) < 20 or len(candidate) > 220:
        return False
    if candidate.count(" ") < 3:
        return False
    if re.search(r"\\cite|paper id|source:|chapter|background", candidate.lower()):
        return False
    return sum(ch.isalpha() for ch in candidate) >= 12


def extract_parenthetical_titles(summary: str) -> list[str]:
    titles = []
    for match in re.findall(r"\(([^()]+)\)", summary or ""):
        candidate = match.strip()
        if looks_like_title(candidate):
            titles.append(candidate)
    return titles


def unique_preserve(items: Iterable[str]) -> List[str]:
    seen = set()
    ordered = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def get_chapter_context(db_path: str, chapter_tag: str, blueprint_path: str, sections_dir: str) -> str:
    db = DatabaseManager(db_path=db_path)
    conn = db.get_connection()

    try:
        with open(blueprint_path, "r", encoding="utf-8") as handle:
            blueprint = json.load(handle)

        chapters = blueprint.get("chapters", [])
        current = next(
            (chapter for chapter in chapters if chapter.get("tag") == chapter_tag or chapter.get("chapter_id") == chapter_tag),
            None,
        )
        if not current:
            return f"Error: chapter tag {chapter_tag} not found in blueprint."

        chapter_number = current.get("number", chapters.index(current) + 1)
        chapter_title = current.get("title", f"Chapter {chapter_number}")
        chapter_paper_ids = unique_preserve(current.get("paper_ids", []))

        if chapter_paper_ids:
            placeholders = sql_placeholders(len(chapter_paper_ids))
            primary_papers = conn.execute(
                f"""
                SELECT paper_id, title, abstract, year, citation_count
                FROM papers
                WHERE paper_id IN ({placeholders})
                ORDER BY citation_count DESC NULLS LAST
                """,
                chapter_paper_ids,
            ).fetchall()
            fulltext_papers = conn.execute(
                f"""
                SELECT paper_id, title, fulltext_path, access_method
                FROM papers
                WHERE paper_id IN ({placeholders})
                AND fulltext_status = 'fetched'
                ORDER BY citation_count DESC NULLS LAST
                """,
                chapter_paper_ids,
            ).fetchall()
            knowledge_rows = conn.execute(
                f"""
                SELECT k.knowledge_text, k.knowledge_type, k.source_type, k.paper_id, p.title, p.year
                FROM knowledge k
                JOIN papers p ON k.paper_id = p.paper_id
                WHERE k.paper_id IN ({placeholders})
                ORDER BY k.knowledge_type, p.citation_count DESC NULLS LAST
                """,
                chapter_paper_ids,
            ).fetchall()
            summaries = conn.execute(
                f"""
                SELECT s.paper_id, s.background_summary, s.found_references, p.title
                FROM summaries s
                JOIN papers p ON s.paper_id = p.paper_id
                WHERE s.paper_id IN ({placeholders})
                AND s.background_summary IS NOT NULL AND s.background_summary != ''
                """,
                chapter_paper_ids,
            ).fetchall()
            ref_rows = conn.execute(
                f"""
                SELECT paper_id, referenced_works_json
                FROM papers
                WHERE paper_id IN ({placeholders})
                """,
                chapter_paper_ids,
            ).fetchall()
        else:
            primary_papers = conn.execute(
                """
                SELECT paper_id, title, abstract, year, citation_count
                FROM papers
                WHERE screening_status = 'include'
                AND (paper_role = 'primary' OR paper_role IS NULL)
                ORDER BY citation_count DESC NULLS LAST
                """
            ).fetchall()
            fulltext_papers = conn.execute(
                """
                SELECT paper_id, title, fulltext_path, access_method
                FROM papers
                WHERE screening_status = 'include'
                AND fulltext_status = 'fetched'
                AND (paper_role = 'primary' OR paper_role IS NULL)
                ORDER BY citation_count DESC NULLS LAST
                """
            ).fetchall()
            knowledge_rows = conn.execute(
                """
                SELECT k.knowledge_text, k.knowledge_type, k.source_type, k.paper_id, p.title, p.year
                FROM knowledge k
                JOIN papers p ON k.paper_id = p.paper_id
                ORDER BY k.knowledge_type, p.citation_count DESC NULLS LAST
                """
            ).fetchall()
            summaries = conn.execute(
                """
                SELECT s.paper_id, s.background_summary, s.found_references, p.title
                FROM summaries s
                JOIN papers p ON s.paper_id = p.paper_id
                WHERE s.background_summary IS NOT NULL AND s.background_summary != ''
                """
            ).fetchall()
            ref_rows = []

        referenced_ids = []
        for _paper_id, referenced_works_json in ref_rows:
            if referenced_works_json:
                try:
                    for ref in json.loads(referenced_works_json):
                        ref_id = ref.split("/")[-1] if "/" in ref else ref
                        if ref_id:
                            referenced_ids.append(ref_id)
                except json.JSONDecodeError:
                    pass

        summary_reference_titles = []
        background_title_candidates = []
        for _paper_id, background_summary, found_references, _title in summaries:
            if found_references:
                try:
                    for ref in json.loads(found_references):
                        title = (ref or {}).get("title")
                        if title:
                            summary_reference_titles.append(normalize_title(title))
                except json.JSONDecodeError:
                    pass
            background_title_candidates.extend(normalize_title(title) for title in extract_parenthetical_titles(background_summary))

        summary_reference_titles = unique_preserve(summary_reference_titles + background_title_candidates)

        citation_papers = []
        citation_seen = set()
        if referenced_ids:
            ref_placeholders = sql_placeholders(len(referenced_ids))
            for row in conn.execute(
                f"""
                SELECT paper_id, title, year, citation_count
                FROM papers
                WHERE paper_role = 'citation'
                AND paper_id IN ({ref_placeholders})
                ORDER BY citation_count DESC NULLS LAST
                LIMIT 120
                """,
                referenced_ids,
            ).fetchall():
                citation_papers.append(row)
                citation_seen.add(row[0])

        if summary_reference_titles:
            title_placeholders = sql_placeholders(len(summary_reference_titles))
            for row in conn.execute(
                f"""
                SELECT paper_id, title, year, citation_count
                FROM papers
                WHERE paper_role = 'citation'
                AND lower(regexp_replace(title, '[^A-Za-z0-9 ]', ' ', 'g')) IN ({title_placeholders})
                ORDER BY citation_count DESC NULLS LAST
                LIMIT 120
                """,
                summary_reference_titles,
            ).fetchall():
                if row[0] not in citation_seen:
                    citation_papers.append(row)
                    citation_seen.add(row[0])

        if not citation_papers:
            citation_papers = conn.execute(
                """
                SELECT paper_id, title, year, citation_count
                FROM papers
                WHERE paper_role = 'citation'
                ORDER BY citation_count DESC NULLS LAST
                LIMIT 40
                """
            ).fetchall()
    finally:
        conn.close()

    context_parts = [
        f"# Writing Task: {chapter_title}",
        f"Chapter Number: {chapter_number}",
        f"Tag: {chapter_tag}",
    ]

    if current.get("objective"):
        context_parts.append(f"\n## Chapter Objective\n{current['objective']}")
    if current.get("key_themes"):
        context_parts.append("\n## Key Themes\n" + "\n".join([f"- {theme}" for theme in current.get("key_themes", [])]))

    previous_context = []
    for number in range(1, chapter_number):
        for prefix in ("ch", "sec"):
            path = os.path.join(sections_dir, f"{prefix}{number}.tex")
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as handle:
                    content = handle.read()
                title_match = re.search(r"\\chapter\{([^}]+)\}|\\section\{([^}]+)\}", content)
                title = title_match.group(1) or title_match.group(2) if title_match else f"Chapter {number}"
                text = re.sub(r"\\[a-zA-Z]+\{[^}]*\}", "", content)
                text = re.sub(r"\\[a-zA-Z]+", "", text)
                previous_context.append(f"Chapter {number} ({title}):\n{text[:800].strip()}...")
                break

    if previous_context:
        context_parts.append("\n## Previous Context\n" + "\n\n".join(previous_context))

    if knowledge_rows:
        context_parts.append(f"\n## Extracted Knowledge Points ({len(knowledge_rows)})")
        for text, knowledge_type, source_type, paper_id, title, year in knowledge_rows:
            context_parts.append(f"[{knowledge_type.upper()}|{source_type}] {text}")
            context_parts.append(f"  Source: {title} ({year}) | CITE AS: \\cite{{{paper_id}}}\n")

    if summaries:
        context_parts.append(f"\n## Background Summaries ({len(summaries)})")
        for paper_id, summary, _found_references, title in summaries:
            context_parts.append(f"### {title} [\\cite{{{paper_id}}}]")
            context_parts.append(f"{summary}\n")

    context_parts.append(f"\n## Primary Papers - Abstracts ({len(primary_papers)})")
    context_parts.append("Use these abstracts as context and cite using \\cite{paper_id}.")
    for paper_id, title, abstract, year, _citation_count in primary_papers:
        if abstract:
            context_parts.append(f"\\noindent\\textbf{{{title}}} ({year}) [\\cite{{{paper_id}}}]")
            context_parts.append(f"{abstract[:800]}\n")

    if fulltext_papers:
        context_parts.append(f"\n## Full-Text Papers ({len(fulltext_papers)})")
        for paper_id, title, fulltext_path, access_method in fulltext_papers:
            context_parts.append(f"\\noindent\\textbf{{{title}}} [\\cite{{{paper_id}}}] (via {access_method})")
            if fulltext_path and os.path.exists(fulltext_path) and fulltext_path.endswith(".html"):
                try:
                    with open(fulltext_path, "r", encoding="utf-8", errors="ignore") as handle:
                        from bs4 import BeautifulSoup

                        soup = BeautifulSoup(handle, "html.parser")
                        fulltext = soup.get_text(separator=" ", strip=True)[:5000]
                    context_parts.append(f"{fulltext}\n")
                except Exception as exc:
                    context_parts.append(f"[Error reading file: {exc}]\n")
            elif fulltext_path and fulltext_path.endswith(".pdf"):
                context_parts.append("[PDF file - use database metadata and extraction outputs]\n")
            else:
                context_parts.append("[Full-text file not found]\n")

    if citation_papers:
        context_parts.append(f"\n## Chapter Citation Pool ({len(citation_papers)})")
        context_parts.append("These papers are available for background, comparison, historical context, and citation density even if they were not full-text extracted.")
        for paper_id, title, year, _citation_count in citation_papers:
            context_parts.append(f"- {title} ({year}) [\\cite{{{paper_id}}}]")

    context_parts.append(
        """
## Writing Rules
1. Write in academic LaTeX format.
2. Use \\cite{paper_id} for every substantive claim.
3. Focus on synthesis, not enumeration.
4. Compare findings across papers and discuss conflicts.
5. Reference previous sections where useful.
6. Output only LaTeX content with no preamble.
7. Target 1800-3000 words for this chapter and aim for 25-45 unique citations, using chapter-local citation papers when primary evidence is sparse.
""".strip()
    )

    return "\n".join(context_parts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--chapter-tag", required=True)
    parser.add_argument("--blueprint", default="outputs/blueprint.json")
    parser.add_argument("--sections-dir", default="outputs/sections")
    parser.add_argument("--output")
    args = parser.parse_args()

    if sys.stdout.encoding != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass

    try:
        ensure_stage_ready("write", args.db_path, chapter_tag=args.chapter_tag)
    except RuntimeError as exc:
        print(str(exc))
        sys.exit(1)

    context = get_chapter_context(args.db_path, args.chapter_tag, args.blueprint, args.sections_dir)
    if args.output:
        output_dir = os.path.dirname(args.output)
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(context)
        print(f"Context written to {args.output}")
        return

    print(context)


if __name__ == "__main__":
    main()
