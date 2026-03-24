"""
Step 0: Initialize Workspace (v17.0.0)
Sets up the project structure and initializes the database.
"""

import os
import argparse
import sys
import re
from pathlib import Path

# Add core to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from core.db import DatabaseManager

def slugify(text: str) -> str:
    text = text.lower()
    return re.sub(r'[^a-z0-9]+', '-', text).strip('-')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", required=True, help="Research topic prompt")
    parser.add_argument("--workspace-root", default="workspaces", help="Root directory for workspaces")
    args = parser.parse_args()

    # Fix UTF-8 encoding for Windows
    if sys.stdout.encoding != 'utf-8':
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except AttributeError:
            pass

    slug = slugify(args.prompt)
    workspace = os.path.abspath(os.path.join(args.workspace_root, slug))
    db_path = os.path.join(workspace, "db", "review.duckdb")
    
    print(f"🔧 Initializing workspace: {workspace}")
    os.makedirs(os.path.join(workspace, "db"), exist_ok=True)
    os.makedirs(os.path.join(workspace, "data"), exist_ok=True)
    os.makedirs(os.path.join(workspace, "data", "query_bundles"), exist_ok=True)
    os.makedirs(os.path.join(workspace, "outputs", "sections"), exist_ok=True)
    
    # Initialize DB (Schema path is handled internally by DatabaseManager)
    db = DatabaseManager(db_path=db_path)
    
    # Save the current workspace path for other scripts to use (optional but helpful)
    with open(os.path.join(workspace, "workspace_info.json"), 'w') as f:
        import json
        json.dump({"prompt": args.prompt, "slug": slug, "db_path": db_path}, f)
    
    print(f"✅ Workspace ready.")
    print(f"   DB Path: {db_path}")
    print(f"   Next Step (Step 0b): Generate OpenAlex queries using step0b_queries.py")

if __name__ == "__main__":
    main()
