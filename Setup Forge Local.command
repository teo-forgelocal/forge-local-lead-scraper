#!/bin/bash
# ───────────────────────────────────────────────────────────────────────────
#  Double-click this file to set up Forge Local on this Mac.
#  It just runs setup.sh in this folder, then waits so you can read the result.
# ───────────────────────────────────────────────────────────────────────────
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR" || exit 1
bash "./setup.sh"
echo
read -r -p "Press Return to close this window..." _
