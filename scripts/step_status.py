"""
Step Status: Report pipeline readiness and next recommended action.
"""

import argparse
import json
import sys

from pipeline_state import describe_blockers, get_next_action, load_workspace_state, recommended_command


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    args = parser.parse_args()

    if sys.stdout.encoding != "utf-8":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except AttributeError:
            pass

    state = load_workspace_state(args.db_path)
    next_action = get_next_action(state)
    blockers = describe_blockers(next_action, state)

    payload = {
        "workspace": state.workspace,
        "prompt": state.prompt,
        "counts": {
            "queries": state.query_count,
            "candidates": state.candidate_count,
            "pending": state.pending_count,
            "included": state.included_count,
            "fetched_fulltext": state.fetched_count,
            "knowledge": state.knowledge_count,
            "summaries": state.summaries_count,
            "chapters_written": state.chapter_count,
        },
        "artifacts": {
            "queries_path": state.queries_path,
            "blueprint_path": state.blueprint_path,
            "sections_dir": state.sections_dir,
        },
        "next_action": next_action,
        "recommended_command": recommended_command(next_action, state),
        "blockers": blockers,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
        return

    print(f"Workspace: {state.workspace}")
    print(f"Prompt: {state.prompt or 'N/A'}")
    print(f"Queries: {state.query_count}")
    print(f"Candidates: {state.candidate_count}")
    print(f"Pending: {state.pending_count}")
    print(f"Included: {state.included_count}/{state.target_included}")
    print(f"Fetched fulltext: {state.fetched_count}/{state.target_fulltext}")
    print(f"Knowledge points: {state.knowledge_count}")
    print(f"Summaries: {state.summaries_count}")
    print(f"Blueprint: {'yes' if state.blueprint_exists else 'no'}")
    print(f"Chapters written: {state.chapter_count}/{state.total_blueprint_chapters or '?'}")
    print("")
    print(f"Next action: {next_action}")
    print(f"Command: {recommended_command(next_action, state)}")
    if blockers:
        print("Blockers:")
        for blocker in blockers:
            print(f"- {blocker}")


if __name__ == "__main__":
    main()
