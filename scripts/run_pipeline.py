"""
Pipeline runner for UniversalReviewer.
Runs the next or requested non-agent step after validating stage prerequisites.
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List

from pipeline_state import ensure_stage_ready, get_next_action, load_workspace_state


def run_command(command: List[str], cwd: str) -> None:
    print("Running:")
    print(" ".join([f'"{part}"' if " " in part else part for part in command]))
    subprocess.run(command, cwd=cwd, check=True)


def resolve_prompt(state) -> str:
    if state.prompt:
        return state.prompt
    info_path = Path(state.workspace) / "workspace_info.json"
    if info_path.exists():
        with open(info_path, "r", encoding="utf-8") as handle:
            return json.load(handle).get("prompt", "")
    return ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True)
    parser.add_argument(
        "--step",
        choices=[
            "next",
            "queries",
            "bridge-queries",
            "acquire",
            "acquire-bridge",
            "screen",
            "snowball",
            "references",
            "fetch",
            "extract",
            "design",
            "write",
            "render",
            "status",
        ],
        default="next",
    )
    parser.add_argument("--chapter-tag", help="Required when --step write is used")
    parser.add_argument("--limit", type=int, default=80, help="Acquisition per-query limit")
    parser.add_argument("--screen-limit", type=int, default=100)
    parser.add_argument("--fetch-limit", type=int, default=25)
    parser.add_argument("--extract-limit", type=int, default=25)
    parser.add_argument("--target", type=int, default=80, help="Snowball target included papers")
    args = parser.parse_args()

    if sys.stdout.encoding != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass

    state = load_workspace_state(args.db_path, target_included=args.target)
    step = get_next_action(state) if args.step == "next" else args.step
    scripts_dir = Path(__file__).resolve().parent
    workspace = state.workspace
    prompt = resolve_prompt(state)

    if step == "status":
        run_command([sys.executable, str(scripts_dir / "step_status.py"), "--db-path", state.db_path], cwd=workspace)
        return

    if step == "queries":
        command = [sys.executable, str(scripts_dir / "step0b_queries.py"), "--workspace", workspace]
        if prompt:
            command.extend(["--prompt", prompt])
        run_command(command, cwd=workspace)
        return

    if step == "bridge-queries":
        command = [
            sys.executable,
            str(scripts_dir / "step0b_queries.py"),
            "--workspace",
            workspace,
            "--strategy",
            "bridge",
        ]
        if prompt:
            command.extend(["--prompt", prompt])
        run_command(command, cwd=workspace)
        return

    ensure_stage_ready(step, state.db_path, chapter_tag=args.chapter_tag)

    if step == "acquire":
        run_command(
            [
                sys.executable,
                str(scripts_dir / "step1_acquire.py"),
                "--db-path",
                state.db_path,
                "--queries-json",
                state.queries_path,
                "--limit",
                str(args.limit),
            ],
            cwd=workspace,
        )
        return

    if step == "acquire-bridge":
        run_command(
            [
                sys.executable,
                str(scripts_dir / "step1_acquire.py"),
                "--db-path",
                state.db_path,
                "--queries-json",
                str(Path(state.outputs_dir) / "search_queries_bridge.json"),
                "--limit",
                str(args.limit),
            ],
            cwd=workspace,
        )
        return

    if step == "screen":
        run_command(
            [
                sys.executable,
                str(scripts_dir / "step2_screen.py"),
                "--db-path",
                state.db_path,
                "--create-bundles",
                "--output-dir",
                str(Path(state.data_dir) / "screening_bundles"),
                "--topic",
                prompt or "Research topic",
                "--limit",
                str(args.screen_limit),
            ],
            cwd=workspace,
        )
        return

    if step == "snowball":
        run_command(
            [
                sys.executable,
                str(scripts_dir / "step3_snowball.py"),
                "--db-path",
                state.db_path,
                "--target",
                str(args.target),
                "--auto",
            ],
            cwd=workspace,
        )
        return

    if step == "references":
        run_command(
            [
                sys.executable,
                str(scripts_dir / "step3b_references.py"),
                "--db-path",
                state.db_path,
            ],
            cwd=workspace,
        )
        return

    if step == "fetch":
        run_command(
            [
                sys.executable,
                str(scripts_dir / "step4_fetch.py"),
                "--db-path",
                state.db_path,
                "--output-dir",
                str(Path(state.data_dir) / "fulltext"),
                "--limit",
                str(args.fetch_limit),
            ],
            cwd=workspace,
        )
        return

    if step == "extract":
        run_command(
            [
                sys.executable,
                str(scripts_dir / "step5_extract.py"),
                "--db-path",
                state.db_path,
                "--prepare-prompts",
                "--output-dir",
                str(Path(state.data_dir) / "extraction_prompts"),
                "--limit",
                str(args.extract_limit),
            ],
            cwd=workspace,
        )
        return

    if step == "design":
        run_command(
            [
                sys.executable,
                str(scripts_dir / "step6_design.py"),
                "--db-path",
                state.db_path,
                "--dump",
            ],
            cwd=workspace,
        )
        return

    if step == "write":
        if not args.chapter_tag:
            raise SystemExit("Error: --chapter-tag is required for --step write")
        output_dir = Path(state.data_dir) / "writing_contexts"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{args.chapter_tag}.md"
        run_command(
            [
                sys.executable,
                str(scripts_dir / "step7_write.py"),
                "--db-path",
                state.db_path,
                "--chapter-tag",
                args.chapter_tag,
                "--blueprint",
                state.blueprint_path,
                "--sections-dir",
                state.sections_dir,
                "--output",
                str(output_path),
            ],
            cwd=workspace,
        )
        return

    run_command(
        [
            sys.executable,
            str(scripts_dir / "step8_render.py"),
            "--db-path",
            state.db_path,
            "--sections-dir",
            state.sections_dir,
            "--output-pdf",
            str(Path(state.outputs_dir) / "review.pdf"),
            "--title",
            prompt or "Review",
        ],
        cwd=workspace,
    )


if __name__ == "__main__":
    main()
