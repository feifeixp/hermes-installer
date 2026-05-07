#!/usr/bin/env bash
# scripts/snapshot-neowow-patches.sh
#
# Extracts every BEGIN/END-bracketed integration block out of the files
# we patch upstream into a sibling .patch.txt file, so a future subtree
# conflict that wipes the markers can be repaired by a one-liner copy.
#
# Output (gitignored — these are reproducible from the source):
#   webui/api/routes.py.neowow.patch.txt          ← both GET + POST blocks
#   webui/static/index.html.neowow.patch.txt      ← both side-menu + pane
#
# Run this from the repo root after every change to a marker block:
#
#   bash scripts/snapshot-neowow-patches.sh
#
# It is also wired up to run on PR review (CI sanity check) so we catch
# accidental marker breakage early.
#
# Why a script instead of just relying on git history: history works
# until someone force-pushes or rebases the branch the integration
# landed on. A snapshot file is dumb-simple — copy and paste back.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

extract() {
  local source="$1"
  local out="$2"
  local marker_label="$3"

  if [[ ! -f "$source" ]]; then
    echo "❌ source missing: $source" >&2
    return 1
  fi

  # Pull every region whose first line contains "BEGIN: $marker_label"
  # and last line contains "END: $marker_label". `sed -n /A/,/B/p` matches
  # in pairs so multiple BEGIN/END blocks all land in the output.
  python3 - "$source" "$marker_label" >"$out" <<'PYEOF'
import sys, re

src_path, label = sys.argv[1], sys.argv[2]
src = open(src_path, encoding="utf-8").read().splitlines()

# Match BEGIN/END pairs whose first line contains the label phrase.
# The marker comments are at varying indents; just look for the
# substring anywhere in the line.
in_block = False
out_lines = []
block_idx = 0
for line in src:
    if not in_block and ("BEGIN: " + label) in line:
        in_block = True
        block_idx += 1
        out_lines.append(f"# ─── Block #{block_idx} ─" + "─" * 60)
    if in_block:
        out_lines.append(line)
    if in_block and ("END: " + label) in line:
        in_block = False
        out_lines.append("")        # blank separator between blocks

if not out_lines:
    sys.stderr.write(f"!  no '{label}' blocks found in {src_path}\n")
    sys.exit(2)
sys.stdout.write("\n".join(out_lines) + "\n")
PYEOF

  echo "  ✓ $out  ($(wc -l <"$out" | tr -d ' ') lines)"
}

echo "→ Extracting Neowow integration blocks…"

extract \
  "webui/api/routes.py" \
  "webui/api/routes.py.neowow.patch.txt" \
  "Neowow integration"

extract \
  "webui/static/index.html" \
  "webui/static/index.html.neowow.patch.txt" \
  "Neowow integration"

echo ""
echo "✅ Done. The .patch.txt files capture the current bytes of every"
echo "   BEGIN: Neowow integration … END: Neowow integration block."
echo "   Commit them whenever the markers move around so they stay in sync"
echo "   with the source files.  See INTEGRATIONS.md for recovery flow."
