"""
Step 0b: Generate OpenAlex Queries.
Creates deterministic search query sets for the initial OpenAlex pass and
for targeted bridge/mechanism expansion in narrow intersection reviews.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import List


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}


def normalize_prompt(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extract_terms(prompt: str) -> List[str]:
    cleaned = re.sub(r"[^\w\s\-']", " ", prompt.lower())
    parts = re.split(r"\band\b|,|/|;", cleaned)
    terms: List[str] = []
    for part in parts:
        candidate = normalize_prompt(part.replace("alzheimer's", "alzheimer disease"))
        if candidate and candidate not in terms:
            terms.append(candidate)
    return terms


def build_queries(prompt: str) -> List[str]:
    prompt = normalize_prompt(prompt)
    lowered = prompt.lower()
    terms = extract_terms(prompt)

    disease = "alzheimer disease" if "alzheimer" in lowered else ""
    mechanism_terms = ["mechanism", "pathogenesis", "lipid raft", "membrane microdomain", "amyloid beta"]
    target_terms = [term for term in terms if term not in {"alzheimer disease", "disease"}]
    anchors = target_terms[:3] or [prompt]

    queries: List[str] = []

    def add(query: str) -> None:
        query = normalize_prompt(query)
        if query and query not in queries:
            queries.append(query)

    add(prompt)
    if disease:
        for anchor in anchors:
            add(f"{anchor} {disease}")
            add(f"{anchor} {disease} mechanism")

    paired = " ".join(anchors[:2]) if len(anchors) >= 2 else anchors[0]
    add(f"{paired} amyloid beta")
    add(f"{paired} lipid raft")
    add(f"{paired} membrane microdomain")
    add(f"{paired} pathogenesis")

    keyword_tokens = [
        token
        for token in re.findall(r"[a-zA-Z0-9\-]+", lowered)
        if len(token) > 2 and token not in STOPWORDS
    ]
    dedup_tokens: List[str] = []
    for token in keyword_tokens:
        if token not in dedup_tokens:
            dedup_tokens.append(token)

    if dedup_tokens:
        core = " ".join(dedup_tokens[:4])
        for term in mechanism_terms:
            add(f"{core} {term}")

    return queries[:8]


def build_bridge_queries(prompt: str) -> List[str]:
    prompt = normalize_prompt(prompt)
    lowered = prompt.lower()
    terms = [term for term in extract_terms(prompt) if term not in {"disease"}]

    disease = "alzheimer disease" if "alzheimer" in lowered else ""
    anchors = [term for term in terms if term != disease]
    if not anchors:
        anchors = [prompt]

    primary = anchors[0]
    secondary = anchors[1] if len(anchors) > 1 else ""

    bridge_terms = [
        "mechanism",
        "amyloid beta",
        "lipid raft",
        "membrane microdomain",
        "cholesterol",
        "transport",
        "review",
    ]

    queries: List[str] = []

    def add(query: str) -> None:
        query = normalize_prompt(query)
        if query and query not in queries:
            queries.append(query)

    pair = normalize_prompt(" ".join([primary, secondary]).strip()) if secondary else primary
    add(f"{pair} {disease}".strip())
    add(f"{pair} mechanism".strip())
    add(f"{pair} amyloid beta".strip())
    add(f"{pair} lipid raft".strip())
    add(f"{pair} membrane microdomain".strip())
    add(f"{pair} transport".strip())

    if disease:
        add(f"{primary} {secondary} {disease} review".strip())
        add(f"{primary} {secondary} cholesterol {disease}".strip())

    for term in bridge_terms:
        add(f"{primary} {secondary} {term}".strip())

    return queries[:8]


def build_refinement_prompt(prompt: str, queries: List[str]) -> str:
    rendered = "\n".join([f"- {query}" for query in queries])
    return (
        "You are preparing OpenAlex search queries for UniversalReviewer.\n\n"
        f"Review topic: {prompt}\n\n"
        "Instructions:\n"
        "1. Keep 5-10 title_and_abstract search strings.\n"
        "2. Cover mechanism, pathology, and intervention subthemes.\n"
        "3. Prefer precise biomedical phrases over broad single-word searches.\n"
        "4. Return only a JSON array of query strings.\n\n"
        "Seed queries:\n"
        f"{rendered}\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", help="Research topic prompt. Defaults to workspace_info.json")
    parser.add_argument("--workspace", required=True, help="Workspace directory created by step0_init.py")
    parser.add_argument("--output", help="Optional override for search_queries.json path")
    parser.add_argument(
        "--strategy",
        choices=["initial", "bridge"],
        default="initial",
        help="Query strategy: initial corpus seeding or bridge/mechanism expansion for narrow topics",
    )
    args = parser.parse_args()

    if sys.stdout.encoding != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass

    workspace = Path(args.workspace).resolve()
    info_path = workspace / "workspace_info.json"
    prompt = normalize_prompt(args.prompt or "")
    if not prompt and info_path.exists():
        with open(info_path, "r", encoding="utf-8") as handle:
            prompt = normalize_prompt(json.load(handle).get("prompt", ""))
    if not prompt:
        raise SystemExit("Error: prompt is required or must exist in workspace_info.json")

    outputs_dir = workspace / "outputs"
    query_bundle_dir = workspace / "data" / "query_bundles"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    query_bundle_dir.mkdir(parents=True, exist_ok=True)

    queries = build_queries(prompt) if args.strategy == "initial" else build_bridge_queries(prompt)
    default_name = "search_queries.json" if args.strategy == "initial" else "search_queries_bridge.json"
    output_path = Path(args.output).resolve() if args.output else outputs_dir / default_name
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(queries, handle, indent=2)

    refinement_name = "refinement_prompt.txt" if args.strategy == "initial" else "refinement_prompt_bridge.txt"
    refinement_prompt_path = query_bundle_dir / refinement_name
    with open(refinement_prompt_path, "w", encoding="utf-8") as handle:
        handle.write(build_refinement_prompt(prompt, queries))

    print(f"Generated {len(queries)} OpenAlex queries using strategy '{args.strategy}'.")
    print(f"Queries: {output_path}")
    print(f"Refinement prompt: {refinement_prompt_path}")


if __name__ == "__main__":
    main()
