#!/bin/sh
# Build a single standalone binary for macOS (native arch: Apple Silicon/arm64
# or Intel/x86_64). Output: dist/ex20v-tool (needs no Python on the target).
set -e
cd "$(dirname "$0")"
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip >/dev/null
pip install -r requirements.txt
pyinstaller --noconfirm --onefile --name ex20v-tool --add-data "agent.sh:." --console ex20v-tool.py
echo "Done: dist/ex20v-tool"
