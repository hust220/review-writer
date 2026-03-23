#!/bin/bash

# Review-OS OpenCode Skill Installer
# This script installs the UniversalReviewer skill into the OpenCode skills directory.

SKILL_NAME="universal-reviewer"
TARGET_DIR="$HOME/.opencode/skills/$SKILL_NAME"
REPO_URL="https://github.com/$(whoami)/review-os" # Placeholder, user should update

echo "🚀 Installing $SKILL_NAME into $TARGET_DIR..."

# 1. Create target directory
mkdir -p "$TARGET_DIR"

# 2. Copy files (assuming run from the project root)
cp -r . "$TARGET_DIR"

# 3. Initialize Python Environment
echo "🐍 Checking Python dependencies..."
cd "$TARGET_DIR"
pip install -r requirements.txt || pip3 install -r requirements.txt

# 4. Install Playwright browser
echo "🌐 Installing Playwright browser..."
playwright install chromium || python3 -m playwright install chromium

echo "✅ Installation complete!"
echo "💡 You can now use the skill in OpenCode by typing: 'write a mechanistic review on [TOPIC]'"
