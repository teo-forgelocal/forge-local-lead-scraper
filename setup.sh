#!/bin/bash
# ───────────────────────────────────────────────────────────────────────────
#  Forge Local — one-command setup for a new Mac.
#
#  What it does (safe to run more than once):
#    1. Checks you have Python 3.12
#    2. Builds the Python environment and installs dependencies
#    3. Creates your .env config file and credentials/ folder
#    4. Installs the "Forge Leads" desktop icon, wired to THIS computer
#
#  Run it from this folder with:   bash setup.sh
#  (or just double-click "Setup Forge Local.command")
# ───────────────────────────────────────────────────────────────────────────

# Resolve the project root = the folder this script lives in.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT" || exit 1

# Destinations — overridable so the installer can be tested without touching
# the real system. Normal users never set these; they default to the real spots.
APP_DEST="${FORGE_APP_DEST:-/Applications}"
DESKTOP_DIR="${FORGE_DESKTOP_DIR:-$HOME/Desktop}"
PY="${FORGE_PYTHON:-python3.12}"

# Pretty output helpers.
GREEN=$'\033[0;32m'; YELLOW=$'\033[1;33m'; RED=$'\033[0;31m'; BOLD=$'\033[1m'; NC=$'\033[0m'
say()  { echo "${GREEN}✅ $*${NC}"; }
warn() { echo "${YELLOW}⚠️  $*${NC}"; }
err()  { echo "${RED}❌ $*${NC}"; }
step() { echo; echo "${BOLD}── $* ──${NC}"; }

clear 2>/dev/null || true
echo "${BOLD}🔥  FORGE LOCAL — SETUP${NC}"
echo "    Project folder: $REPO_ROOT"

# ── 1. Python 3.12 ───────────────────────────────────────────────────────────
step "1 of 4   Checking for Python 3.12"
if ! command -v "$PY" >/dev/null 2>&1; then
    err "Python 3.12 is not installed yet."
    echo
    echo "    Install it, then double-click Setup again:"
    echo "      • Easiest: download the macOS installer (the .pkg) from"
    echo "        https://www.python.org/downloads/release/python-3120/"
    echo "      • Or, if you have Homebrew:  brew install python@3.12"
    echo
    exit 1
fi
say "Found $($PY --version)"

# ── 2. Python environment + dependencies ─────────────────────────────────────
step "2 of 4   Building the Python environment"
if [ ! -d ".venv" ]; then
    if "$PY" -m venv .venv; then
        say "Created the virtual environment (.venv)"
    else
        err "Could not create the environment. Make sure Python 3.12 installed correctly."
        exit 1
    fi
else
    say "Environment already exists — reusing it"
fi

# shellcheck source=/dev/null
source .venv/bin/activate
python -m pip install --quiet --upgrade pip >/dev/null 2>&1
echo "    Installing dependencies (this can take a minute)..."
if python -m pip install --quiet -r requirements.txt; then
    say "Dependencies installed"
else
    err "Dependency install failed. Check your internet connection and run Setup again."
    exit 1
fi

# ── 3. Config files (.env + credentials/) ────────────────────────────────────
step "3 of 4   Preparing your config files"
mkdir -p credentials
if [ ! -f ".env" ]; then
    cp .env.example .env
    say "Created your .env file (you'll fill in your values — see below)"
else
    say "Your .env already exists — leaving it untouched"
fi

# ── 4. Desktop app + icon ────────────────────────────────────────────────────
step "4 of 4   Installing the desktop icon"
APP_SRC="$REPO_ROOT/Forge Local.app"
APP_INSTALLED="$APP_DEST/Forge Local.app"

if [ -d "$APP_SRC" ]; then
    mkdir -p "$APP_DEST"
    rm -rf "$APP_INSTALLED" 2>/dev/null
    if cp -R "$APP_SRC" "$APP_DEST/" 2>/dev/null; then
        # Point the app's launcher at THIS computer's copy of the project.
        STUB="$APP_INSTALLED/Contents/MacOS/ForgeLocal"
        cat > "$STUB" <<STUBEOF
#!/bin/bash
# Mac app entry point — opens Terminal and runs the launcher.
open -a Terminal "$REPO_ROOT/launch_forge.sh"
STUBEOF
        chmod +x "$STUB"
        chmod +x "$REPO_ROOT/launch_forge.sh" 2>/dev/null
        say "Installed the app to $APP_INSTALLED"

        # Create a proper Finder alias on the Desktop (shows the real icon).
        rm -f "$DESKTOP_DIR/Forge Leads.app" "$DESKTOP_DIR/Forge Leads" 2>/dev/null
        if [ "$DESKTOP_DIR" = "$HOME/Desktop" ]; then
            if osascript >/dev/null 2>&1 <<OSAEOF
tell application "Finder"
    set newAlias to make alias file to POSIX file "$APP_INSTALLED" at desktop
    set name of newAlias to "Forge Leads"
end tell
OSAEOF
            then
                say "Put a 'Forge Leads' icon on your Desktop"
            else
                warn "Couldn't auto-create the Desktop icon."
                echo "    No problem — open your Applications folder, hold ⌘ + ⌥ (Command+Option),"
                echo "    and drag 'Forge Local' onto your Desktop. Rename it 'Forge Leads' if you like."
            fi
        fi
    else
        warn "Couldn't copy the app into $APP_DEST automatically."
        echo "    Open this folder in Finder and drag 'Forge Local.app' into Applications,"
        echo "    then hold ⌘ + ⌥ and drag it from Applications onto your Desktop."
    fi
else
    warn "No app bundle found in the project — skipping the desktop icon."
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo
echo "${BOLD}🎉  Setup is done. Two one-time things left:${NC}"
echo
echo "  ${BOLD}1.${NC} Open the file named  ${BOLD}.env${NC}  in this folder (open it with TextEdit) and paste in:"
echo "       • your Google Places API key   ->  GOOGLE_MAPS_API_KEY"
echo "       • your work email              ->  REPORT_EMAIL"
echo "     Full path:  $REPO_ROOT/.env"
echo "     (Don't have the API key yet? Follow Part 3 of the setup guide first.)"
echo
echo "  ${BOLD}2.${NC} Put your downloaded  ${BOLD}oauth-client.json${NC}  file into the  ${BOLD}credentials/${NC}  folder."
echo "     Full path:  $REPO_ROOT/credentials/"
echo
echo "  ${BOLD}Then:${NC} double-click the ${BOLD}Forge Leads${NC} icon on your Desktop."
echo "  The first time, a browser opens asking you to sign in to Google — click Allow."
echo
