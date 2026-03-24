"""
Step 8: Render Review.
Concatenates sections, generates BibTeX, and attempts PDF compilation.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Dict, List, Set, Tuple

# Add core to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from core.db import DatabaseManager
from pipeline_state import ensure_stage_ready


def clean_for_bibtex(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\\n", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")
    for char, replacement in {"&": "\\&", "%": "\\%", "$": "\\$", "#": "\\#"}.items():
        text = text.replace(char, replacement)
    return "".join([character for character in text if ord(character) < 128])


def generate_bib(db_path: str, sections_dir: str, output_path: str) -> None:
    db = DatabaseManager(db_path=db_path)
    conn = db.get_connection()
    try:
        cited_ids = set()
        cite_pattern = re.compile(r"\\cite\{([^}]+)\}")
        for name in os.listdir(sections_dir):
            if name.endswith(".tex") and name != "main.tex":
                with open(os.path.join(sections_dir, name), "r", encoding="utf-8") as handle:
                    for match in cite_pattern.findall(handle.read()):
                        cited_ids.update([item.strip() for item in match.split(",")])

        if not cited_ids:
            print("No citations found in section files.")
            return

        bib_entries = []
        for paper_id in cited_ids:
            row = conn.execute(
                """
                SELECT paper_id, doi, title, year, journal, authors_json
                FROM papers
                WHERE paper_id = ?
                """,
                [paper_id],
            ).fetchone()
            if not row:
                continue

            _, doi, title, year, journal, authors_json = row
            author_str = "Unknown"
            if authors_json:
                try:
                    authors = json.loads(authors_json)
                    author_str = " and ".join([clean_for_bibtex(author.get("name", "")) for author in authors if author.get("name")])
                except json.JSONDecodeError:
                    pass

            entry = [
                f"@article{{{paper_id},",
                f"  author = {{{author_str}}},",
                f"  title = {{{{{clean_for_bibtex(title or 'Unknown Title')}}}}},",
                f"  journal = {{{clean_for_bibtex(journal or 'Unknown Journal')}}},",
                f"  year = {{{year or ''}}},",
            ]
            if doi:
                entry.append(f"  doi = {{{doi}}},")
            entry.append("}")
            bib_entries.append("\n".join(entry))

        with open(output_path, "w", encoding="utf-8") as handle:
            handle.write("\n\n".join(bib_entries))
    finally:
        conn.close()


def collect_section_metrics(sections_dir: str) -> Tuple[Set[str], Dict[str, int], int]:
    cite_pattern = re.compile(r"\\cite\{([^}]+)\}")
    latex_pattern = re.compile(r"\\[a-zA-Z]+\{[^}]*\}|\\[a-zA-Z]+")

    cited_ids: Set[str] = set()
    per_section: Dict[str, int] = {}
    total_words = 0

    for name in sorted([n for n in os.listdir(sections_dir) if n.endswith(".tex") and n != "main.tex"]):
        with open(os.path.join(sections_dir, name), "r", encoding="utf-8") as handle:
            content = handle.read()
        section_ids: Set[str] = set()
        for match in cite_pattern.findall(content):
            section_ids.update([item.strip() for item in match.split(",") if item.strip()])
        cited_ids.update(section_ids)
        per_section[name] = len(section_ids)

        plain = latex_pattern.sub(" ", content)
        plain = re.sub(r"[^A-Za-z0-9\s]", " ", plain)
        total_words += len([token for token in plain.split() if token.strip()])

    return cited_ids, per_section, total_words


def validate_review_metrics(
    sections_dir: str,
    min_total_citations: int,
    min_total_words: int,
    min_section_citations: int,
) -> List[str]:
    cited_ids, per_section, total_words = collect_section_metrics(sections_dir)
    warnings: List[str] = []

    if len(cited_ids) < min_total_citations:
        warnings.append(
            f"Total unique citations below target: {len(cited_ids)} < {min_total_citations}"
        )

    if total_words < min_total_words:
        warnings.append(
            f"Approximate review word count below target: {total_words} < {min_total_words}"
        )

    for section_name, citation_count in per_section.items():
        if citation_count < min_section_citations:
            warnings.append(
                f"Section {section_name} has low citation coverage: {citation_count} < {min_section_citations}"
            )

    print(f"Review metrics: {len(cited_ids)} unique citations, ~{total_words} words.")
    for section_name, citation_count in per_section.items():
        print(f"  {section_name}: {citation_count} unique citations")

    return warnings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--sections-dir", default="outputs/sections")
    parser.add_argument("--output-pdf", default="outputs/review.pdf")
    parser.add_argument("--title", default="Technological Review")
    parser.add_argument("--min-total-citations", type=int, default=120)
    parser.add_argument("--min-total-words", type=int, default=8000)
    parser.add_argument("--min-section-citations", type=int, default=20)
    parser.add_argument("--strict-validation", action="store_true")
    args = parser.parse_args()

    if sys.stdout.encoding != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass

    try:
        ensure_stage_ready("render", args.db_path)
    except RuntimeError as exc:
        print(str(exc))
        sys.exit(1)

    os.makedirs(args.sections_dir, exist_ok=True)
    warnings = validate_review_metrics(
        args.sections_dir,
        min_total_citations=args.min_total_citations,
        min_total_words=args.min_total_words,
        min_section_citations=args.min_section_citations,
    )
    if warnings:
        print("Validation warnings:")
        for warning in warnings:
            print(f"- {warning}")
        if args.strict_validation:
            raise SystemExit("Render blocked by strict validation.")

    bib_path = os.path.join(args.sections_dir, "references.bib")
    generate_bib(args.db_path, args.sections_dir, bib_path)

    section_files = sorted(
        [name for name in os.listdir(args.sections_dir) if name.endswith(".tex") and name != "main.tex"]
    )
    main_tex = os.path.join(args.sections_dir, "main.tex")
    with open(main_tex, "w", encoding="utf-8") as handle:
        handle.write("\\documentclass[12pt,a4paper]{article}\n")
        handle.write("\\usepackage[utf8]{inputenc}\n")
        handle.write("\\usepackage[margin=1in]{geometry}\n")
        handle.write("\\usepackage{cite}\n")
        handle.write("\\usepackage{hyperref}\n")
        handle.write("\\begin{document}\n")
        handle.write(f"\\title{{{args.title}}}\n")
        handle.write("\\author{UniversalReviewer Engine}\n")
        handle.write("\\maketitle\n")
        handle.write("\\tableofcontents\n")
        for section_file in section_files:
            handle.write(f"\\input{{{section_file}}}\n")
        handle.write("\\bibliographystyle{naturemag}\n")
        handle.write("\\bibliography{references}\n")
        handle.write("\\end{document}\n")

    print(f"LaTeX source ready: {main_tex}")
    try:
        subprocess.run(["tectonic", "main.tex"], cwd=args.sections_dir, check=True)
        pdf_src = os.path.join(args.sections_dir, "main.pdf")
        if os.path.exists(pdf_src):
            shutil.copy(pdf_src, args.output_pdf)
            print(f"PDF rendered: {args.output_pdf}")
    except Exception:
        print(f"Compilation failed or tectonic is unavailable. Manual compile required for {main_tex}")


if __name__ == "__main__":
    main()
