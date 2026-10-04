#!/usr/bin/env bash
# Cloud environment setup script: installs the three graph tools bootstrap.py
# expects on PATH, then bootstraps. Paste into the cloud environment's setup
# field (runs as root), or run once by hand. Versions are the ones the repo was
# developed against. Fails loudly: a missing tool must stop setup, not be skipped.
set -euo pipefail
cd "$(dirname "$0")/.."

# Own venv for the Python tools: system pip may be externally managed.
python3.11 -m venv /opt/graphtools
/opt/graphtools/bin/pip install -q graphifyy==0.9.67 codebase-memory-mcp==0.11.0
npm install -g @nanonets/graft@0.19.0
for t in graphify codebase-memory-mcp; do ln -sf "/opt/graphtools/bin/$t" "/usr/local/bin/$t"; done
for t in graphify graft codebase-memory-mcp; do command -v "$t" >/dev/null || { echo "setup: $t not on PATH" >&2; exit 1; }; done

python3.11 scripts/bootstrap.py
