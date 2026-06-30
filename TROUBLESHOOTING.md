# Forge Local — Troubleshooting

## Desktop icon missing / app won't launch

**Symptom:** The "Forge Local" icon disappeared from your Desktop, or double-clicking does nothing.

**Quick fix — paste this prompt into a new Claude chat:**

---

My Mac app "Forge Local" disappeared from my Desktop, but the app itself may still be installed at /Applications/Forge Local.app. I need help recreating the Desktop shortcut and relaunching it safely.

Context: Forge Local is a custom lead-gen AI agent I built. It's a Mac .app bundle with a custom purple flame "F" icon. The actual app lives in /Applications/Forge Local.app — only the Desktop symlink/shortcut tends to go missing (e.g. after a desktop cleanup tool, OS update, or accidental deletion).

Please walk me through:

1. Verify the app still exists: ls -la "/Applications/Forge Local.app"
2. If it exists, recreate the Desktop shortcut: ln -s "/Applications/Forge Local.app" ~/Desktop/
3. First launch after recreating (since macOS may re-flag it):
   - Right-click (not double-click) the icon on Desktop
   - Click "Open"
   - If a security warning appears ("unidentified developer"), click "Open" again to confirm
   - Terminal should open showing a "FORGE LOCAL — DAILY RUN" banner and a 4-option menu
4. If the app does NOT exist at /Applications/Forge Local.app (full rebuild needed), I'll need help rebuilding the .app bundle from my project files. The project is at /Users/teo./Developer/forge-local/ and should contain launch_forge.sh and icon.icns. The bundle structure needed is Forge Local.app/Contents/Info.plist, Forge Local.app/Contents/MacOS/ForgeLocal (executable, calls launch_forge.sh via open -a Terminal), and Forge Local.app/Contents/Resources/icon.icns.

Please diagnose first (step 1), then guide me through whichever path applies. I'm comfortable running terminal commands but appreciate clear step-by-step instructions.

---

## Other known issues

### "zsh: permission denied: ./launch_forge.sh"
The script lost its executable bit. Fix by running this in terminal:

    chmod +x /Users/teo./Developer/forge-local/launch_forge.sh

### OAuth sends from wrong account (taylor.whitmore12@gmail.com instead of leadagent@forgelocal.app)
Delete the token and re-auth as hello@forgelocal.app. Run this in terminal:

    rm credentials/oauth-token.json
    python src/daily_run.py --configure

When the browser opens, click "Use another account" and sign in as hello@forgelocal.app — NOT your personal Gmail.

### Google Sheet creation fails with "File not found" / 404 on folder ID
The GOOGLE_DRIVE_FOLDER_ID in .env points to a folder owned by a different Google account than the one currently authenticated. Create a new folder in the correct account's Drive, copy its ID from the URL, update .env.

### Duplicate leads across multiple runs
KNOWN ISSUE — see IDEAS.md "MUST FIX BEFORE BETA LAUNCH" section. Agent currently only dedupes at the city level, not individual business level. Scheduled fix: business-level Place ID tracking via Supabase/SQLite. Not yet implemented as of May 19, 2026.