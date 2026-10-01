#!/bin/bash
# Update Bathroom Designer to the latest version.
# Keeps your projects/, output/, saved Gemini key, and .venv.
set -e
cd "$(dirname "$0")"
URL="https://github.com/SimchaHalpert/DAV-5400/archive/refs/heads/claude/website-image-extraction-77uevy.zip"
TMP="$(mktemp -d)"
curl -sSL -o "$TMP/latest.zip" "$URL"
unzip -q "$TMP/latest.zip" -d "$TMP"
cp -R "$TMP"/*/bathroom_designer/. .
rm -rf "$TMP"
if [ -x .venv/bin/pip ]; then .venv/bin/pip install -q -r requirements.txt; fi
echo "Updated to the latest version. Start it with:  python3 app.py"
