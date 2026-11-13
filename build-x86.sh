#!/bin/sh
# Cross-build the INTEL (x86_64) binary on an Apple Silicon (M-series) Mac.
# PyInstaller does not cross-compile, so we run an x86_64 Python under Rosetta 2.
# Dev tools like lipo/codesign fail under Rosetta because libxcrun is arm64-only;
# we wrap them with shims that fall back to native arm64.
# Output: dist-x86/ex20v-tool (runs natively on Intel Macs).
set -e
cd "$(dirname "$0")"

/usr/bin/pgrep -q oahd || { echo "Rosetta 2 required: softwareupdate --install-rosetta"; exit 1; }

PYVER="3.11.18"
TAG="20261110"   # python-build-standalone release tag (update if needed)
NAME="cpython-${PYVER}+${TAG}-x86_64-apple-darwin-install_only.tar.gz"
URL="https://github.com/astral-sh/python-build-standalone/releases/download/${TAG}/${NAME}"

if [ ! -x x86py/python/bin/python3.11 ]; then
  echo "Downloading x86_64 Python..."
  curl -sSL -o x86py.tar.gz "$URL"
  rm -rf x86py && mkdir x86py && tar -xzf x86py.tar.gz -C x86py
fi
PY="$(pwd)/x86py/python/bin/python3.11"

# shims that redirect dev tools to native arm64
mkdir -p archshim
for t in lipo codesign xcrun strip; do
  printf '#!/bin/sh\nexec arch -arm64 /usr/bin/%s "$@"\n' "$t" > "archshim/$t"
  chmod +x "archshim/$t"
done
SHIM="$(pwd)/archshim"

echo "x86_64 venv + dependencies..."
arch -x86_64 "$PY" -m venv .venv-x86
arch -x86_64 .venv-x86/bin/python -m pip install --upgrade pip >/dev/null
arch -x86_64 .venv-x86/bin/python -m pip install -r requirements.txt >/dev/null

echo "Building x86_64..."
rm -rf build dist-x86
arch -x86_64 env PATH="$SHIM:$(pwd)/.venv-x86/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
  .venv-x86/bin/pyinstaller --noconfirm --onefile --name ex20v-tool \
  --distpath dist-x86 --add-data "agent.sh:." --console ex20v-tool.py
file dist-x86/ex20v-tool
echo "Done: dist-x86/ex20v-tool (Intel)"
