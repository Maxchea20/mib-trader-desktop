# Building the MIB Trader Desktop App

This turns mib-trader into one installable app that runs the whole trading
engine locally, with no separate hosting needed. This has **not been
compiled yet** — I wrote all the code and config carefully, but Rust/Tauri
apps have to be built on a real machine with a display (they can't be
compiled and tested in a sandbox). Treat your first build the way we
treated the first live MEXC order: go slowly, check each step actually
worked before moving to the next one, and don't be surprised if step 4 or
5 needs a small fix — that's normal for a first build, not a sign
something is fundamentally wrong.

## What you're building

```
desktop/
  src-tauri/          <- the Rust "host" app (window, tray, process manager)
    binaries/          <- the packaged Python backend goes here (you build it)
    icons/              <- app icons (you generate these)
  package.json          <- just runs the Tauri CLI

backend/
  run_server.py         <- new entrypoint, makes the backend runnable standalone
  scripts/
    build_sidecar.sh     <- packages backend/ into one executable (Mac/Linux)
    build_sidecar.ps1    <- same, for Windows
```

The Rust app starts, launches the packaged Python backend as a background
process ("sidecar"), loads your existing React dashboard into a window,
and gets a system tray icon so closing the window doesn't kill the bot.

---

## Step 0 — Install the tools (one-time)

You'll need three things installed on the machine you're building on:

1. **Rust** — https://rustup.rs — run the installer, accept defaults.
2. **Node.js** (you likely already have this for the frontend) — v18+.
3. **Python 3.11+** with your backend's `requirements.txt` already
   installed, plus PyInstaller:
   ```
   pip install pyinstaller --break-system-packages
   ```

On **Windows**, Tauri also needs "Microsoft C++ Build Tools" — the Rust
installer will prompt you for this if it's missing, just follow its link.

On **Linux**, Tauri needs some system packages first:
```
sudo apt install libwebkit2gtk-4.1-dev build-essential curl wget file \
  libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev
```

macOS needs Xcode Command Line Tools: `xcode-select --install`

Verify Rust installed correctly:
```
rustc --version
cargo --version
```

---

## Step 1 — Build the backend into one executable

From the repo root:
```
cd backend
pip install -r requirements.txt --break-system-packages   # if not already done
```

**Mac/Linux:**
```
bash scripts/build_sidecar.sh
```

**Windows (PowerShell):**
```
.\scripts\build_sidecar.ps1
```

This takes a few minutes the first time. When it's done, it prints where
it placed the file — it should land in
`desktop/src-tauri/binaries/mib-backend-<your-platform>`.

**Check it before moving on.** Run it directly:
```
./desktop/src-tauri/binaries/mib-backend-<your-platform>    # Mac/Linux
```
or double-click the `.exe` on Windows. You should see uvicorn startup
logs and "Application startup complete." Then open
`http://127.0.0.1:8811/api/` in a browser — you should get a small JSON
response. If you see that, the backend build worked. Press Ctrl+C to stop
it, then continue.

If it crashes instead with an import error, the most common fix is
re-running the build script — PyInstaller sometimes misses a hidden
import on the first pass. Tell me the exact error and I'll add the right
`--hidden-import` flag to the build script.

---

## Step 2 — Build the frontend

```
cd frontend
npm install
npm run build
```

This produces `frontend/build/` — a static bundle of your React
dashboard. `tauri.conf.json` already points at this folder.

---

## Step 3 — Generate app icons

From the `desktop/` folder, pick any square PNG logo you have (even a
placeholder is fine for now — you can swap it later):
```
cd desktop
npm install
npx tauri icon path/to/your-logo.png
```

This generates every icon size Tauri needs into `desktop/src-tauri/icons/`.

---

## Step 4 — Build the desktop app

Still inside `desktop/`:
```
npm run build
```

This compiles the Rust host and bundles everything (Rust host + your
sidecar backend + your frontend) into a real installer:
- **Windows**: an `.msi` and/or `.exe` installer
- **macOS**: a `.app` and `.dmg`
- **Linux**: a `.deb` / `.AppImage`

Find the output under `desktop/src-tauri/target/release/bundle/`.

This step is the one most likely to need a small fix on the first try —
Rust will tell you exactly what's wrong if something doesn't compile
(usually a small API mismatch in `main.rs`, since I wrote that code
without being able to compile-check it myself in this environment). Paste
me the error and I'll fix it immediately.

---

## Step 5 — Run it

Install/launch the built app. It should:
1. Open a window showing your dashboard
2. Show a tray icon (bottom-right on Windows, top-right on Mac)
3. Closing the window (the X button) should just hide it to the tray, not
   quit — check the tray icon is still there after clicking X
4. Right-click (or click, depending on OS) the tray icon → you should see
   "Show MIB Trader", "Restart Trading Engine", "Show Data Folder", "Quit"

**Test the watchdog:** open Task Manager / Activity Monitor, find the
`mib-backend` process, and kill it manually. Within ~3 seconds the app
should notice and restart it automatically — check the dashboard
reconnects on its own.

---

## Setting your MEXC API keys for the desktop build

The packaged app doesn't read a `.env` file next to your code anymore
(there is no "next to the code" once it's frozen into an executable).
Instead:

1. From the tray menu, click **"Show Data Folder"** — this opens the
   folder where the app stores its database and looks for config.
2. Create a plain text file there named `.env` (exactly that, with the
   dot) containing:
   ```
   MEXC_API_KEY=your_key_here
   MEXC_API_SECRET=your_secret_here
   ```
3. Use **"Restart Trading Engine"** from the tray menu to pick it up.

(A proper in-app Settings screen for this — instead of hand-editing a
text file — is a reasonable next step once you're comfortable the basic
packaging works. Say the word and I'll build that next.)

---

## Auto-start on login

The autostart plugin is wired in but not yet turned on by default — it
needs one line calling `app.autolaunch().enable()` somewhere (e.g. a
Settings screen toggle, or unconditionally in `setup()` if you always
want it on). Let me know which you want and I'll wire it in.

---

## If something breaks

Send me the exact error text from whichever step failed — Step 1
(PyInstaller), Step 4 (Rust/cargo), or a runtime crash after Step 5 — and
I'll patch the relevant file. This is genuinely expected to need one or
two rounds of fixes on a first build; that's normal for packaging a real
app, not a sign the plan is wrong.
