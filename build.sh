#!/bin/sh
# macOS icin tek parca binary derler (Apple Silicon/arm64 veya Intel/x86_64).
# Cikti: dist/ex20v-tool  (baska makinede python kurulumu gerektirmez)
set -e
cd "$(dirname "$0")"
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip >/dev/null
pip install -r requirements.txt
pyinstaller --noconfirm --onefile --name ex20v-tool --add-data "agent.sh:." --console ex20v-tool.py
echo "Tamam: dist/ex20v-tool"
