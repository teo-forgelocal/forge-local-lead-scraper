#!/bin/bash
# Forge Local — daily run launcher
# Activates the project's venv and runs the agent's daily_run.py.
# Pauses at the end so the user can read the output before the window closes.

set -e  # exit on first error

# Resolve the project root — this script's directory
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# Clear screen and print a banner
clear
echo "┌─────────────────────────────────────────────────────────────┐"
echo "│                                                             │"
echo "│              🔥  FORGE LOCAL — DAILY RUN  🔥                │"
echo "│                                                             │"
echo "└─────────────────────────────────────────────────────────────┘"
echo
echo "  Project: $PROJECT_DIR"
echo

# Activate the virtual environment
if [ ! -d ".venv" ]; then
    echo "❌ Virtual environment not found at $PROJECT_DIR/.venv"
    echo "   Run: python3.12 -m venv .venv && pip install -r requirements.txt"
    echo
    read -p "Press any key to close..."
    exit 1
fi
source .venv/bin/activate

# Ask the user what they want to do
echo "  How do you want to run today?"
echo
echo "    1. Run with current config (fast)"
echo "    2. Configure today's run (state, niche, tier)"
echo "    3. Dry run — just show what would happen"
echo "    4. Cancel"
echo
read -p "  Enter 1, 2, 3, or 4: " choice
echo

case "$choice" in
    1)
        python src/daily_run.py
        ;;
    2)
        python src/daily_run.py --configure
        ;;
    3)
        python src/daily_run.py --dry-run
        ;;
    4)
        echo "  Cancelled. No run made."
        ;;
    *)
        echo "  Invalid choice. No run made."
        ;;
esac

echo
echo "─────────────────────────────────────────────────────────────"
read -p "  Press any key to close this window..." -n 1 -s
echo