---
name: desktop-computer-use
description: Cross-platform desktop computer use and parallel virtual desktop sandboxes (Windows, macOS & Linux) using a zero-MCP hybrid CLI stack: Vercel Labs `agent-desktop` as primary headless accessibility engine (AX/UIA/AT-SPI, `@snapshotId:eN` refs, surfaces, Chromium/Electron, `wait`, `batch`, multi-worker sessions), cross-platform `pilot_ctl.py` (zero-dependency Set-of-Mark `--annotate` & `--grid` screenshots, deterministic Risk-Guard + JSONL `--trace`, multi-display `window-tile`, parameterized `recipe`, cross-platform `notify`, plus macOS `record`/`shortcut` accelerators), and `sandbox_ctl.py` for `<1s` isolated virtual Linux desktops with live noVNC takeover. Use whenever controlling desktop apps across Windows, macOS, or Linux, arranging windows, running parallel GUI workers, or automating desktop tasks.
---

# Cross-Platform Desktop Computer Use (`agent-desktop` + `pilot_ctl.py` + `sandbox_ctl.py`)

`antigravity-pilot` uses a **zero-MCP hybrid CLI architecture** across **Windows, macOS, and Linux**:

### macOS & Linux Setup
```bash
PILOT_CTL="$HOME/.gemini/config/plugins/antigravity-pilot/bin/pilot-ctl"
# or directly: python3 "$HOME/.gemini/config/plugins/antigravity-pilot/scripts/pilot_ctl.py"

SANDBOX_CTL="$HOME/.gemini/config/plugins/antigravity-pilot/bin/sandbox-ctl"
# or directly: python3 "$HOME/.gemini/config/plugins/antigravity-pilot/scripts/sandbox_ctl.py"
```

### Windows Setup (PowerShell / cmd)
```powershell
$PILOT_CTL   = "$HOME\.gemini\config\plugins\antigravity-pilot\scripts\pilot_ctl.py"
$SANDBOX_CTL = "$HOME\.gemini\config\plugins\antigravity-pilot\scripts\sandbox_ctl.py"
# Invoke with: python $PILOT_CTL <subcommand> / python $SANDBOX_CTL <subcommand>
```
```bat
python "%USERPROFILE%\.gemini\config\plugins\antigravity-pilot\scripts\pilot_ctl.py" doctor
python "%USERPROFILE%\.gemini\config\plugins\antigravity-pilot\scripts\sandbox_ctl.py" list
```

- **Primary engine (`agent-desktop` on `PATH`):** Native accessibility automation on **Windows (`UIA`), macOS (`AX`), and Linux (`AT-SPI`)** (`snapshot`, `find`, `get`, `is`, `click`, `type`, `set-value`, `press`, `select`, `toggle`, `scroll`, `wait`, `batch`, `surfaces`, `launch`, `close-app`, `list-apps`, `list-windows`, `focus-window`, `clipboard-*`, `list-notifications`, `permissions`, `screenshot`). Every command returns a structured JSON envelope (`ok`, `data` or `error`).
- **Complement engine (`pilot_ctl.py` / `bin/pilot-ctl`):** Zero-dependency Python 3 cross-platform CLI covering capabilities `agent-desktop` does not ship:
  - **Set-of-Mark & Grid Visual Fallback:** `screenshot <out.png> ... --annotate [--grid]`
  - **Deterministic Risk-Guard & Audit Trace:** `guard-check`, `safe-click`, `safe-close`, exit code `2` (`RISK_GUARD_BLOCKED`), `--confirm-risky`, `--trace <file.jsonl>` / `PILOT_CTL_TRACE`
  - **Multi-Display Window Tiling:** `window-tile <app> <left|right|top|bottom|maximize|center> [--window-id <id>] [--title <substr>] [--display <1-based>]`
  - **Parameterized Recipes:** `recipe list|show|save|run|delete` (`--param key=val`, `--dry-run`, `--stdin`, `--confirm-risky`)
  - **Cross-Platform Notifications & Diagnostics:** `notify <title> <message>`, `doctor [--json]`
  - **macOS Native Accelerators (via `bin/macos-ctl`):** `record` and `shortcut`
- **Virtual Desktop Sandbox engine (`sandbox_ctl.py` / `bin/sandbox-ctl`):** Manages `<1s` isolated virtual Linux desktops in Docker/Colima (`antigravity-desktop-sandbox:latest`, container prefix `ag-desk-*`) across Windows, macOS, and Linux, with optional live **noVNC** human-in-the-loop takeover (`up --vnc`, `url`).

For deeper `agent-desktop` flag references at any time, run:
```bash
agent-desktop <command> --help
agent-desktop skills get desktop <commands-observation|commands-interaction|commands-system|workflows>
```

---

## Core Loop: Observe → Act → Verify (`agent-desktop`)

Use `find` when you already know the exact role or name. Otherwise map the app with `--skeleton` and drill into a subtree with `--root`:

```bash
# 1. Shallow skeleton map of a dense app (small apps can skip --skeleton)
agent-desktop snapshot --skeleton --app "Google Chrome" -i --compact

# 2. Drill into a specific container ref
agent-desktop snapshot --root @s8f3k2p9:e3 -i --compact

# 3. Act on a qualified ref
agent-desktop click @s8f3k2p9:e12

# 4. Verify by re-observing the same region (accessibility return codes alone do not prove effect)
agent-desktop snapshot --root @s8f3k2p9:e3 -i --compact
```

### Refs (`@<snapshot_id>:e<N>`) & Error Handling

- **Qualified refs:** Pass refs verbatim as `@<snapshot_id>:e<N>` (e.g. `@s8f3k2p9:e12`).
- **`--root` drills:** Add new refs under the same snapshot ID. Refs outside the drilled subtree (and the drilled root itself) stay valid; old refs inside the drilled subtree are replaced (`STALE_REF`).
- **Common ref errors:**
  - `STALE_REF` (`retained_object_stale` / `saved_ref_absent`): element changed or disappeared — take a fresh snapshot.
  - `AMBIGUOUS_TARGET` (`saved_ref_ambiguous` / `identity_not_unique`): target not unique or snapshot timed out — use a unique control or retake with a larger `--timeout-ms`.
  - `SNAPSHOT_NOT_FOUND`: snapshot ID expired — snapshot again.
- **Strict retry rule (`disposition`):**
  - Retry or substitute an action **only** when `error.disposition.retry == "safe"` (i.e. `delivery == "not_delivered"`).
  - Any other `delivery` value means the action may have landed (repeating `click`/`type`/`toggle` can double-submit or invert state). Read live state with `get`, `is`, or a fresh `snapshot` before acting again.
  - Branch on `error.code` and `error.disposition`, not human-readable error strings.

### Headless vs `--headed` & `POLICY_DENIED`

- **Headless by default:** Ref actions use semantic AX/UIA/AT-SPI calls without stealing focus, moving the cursor, or touching the clipboard. If no semantic path exists, the command fails closed.
- **`--headed`:** Use only when physical input is explicitly required and permitted (`double-click`, `triple-click`, `hover`, `drag`). Never switch to `--headed` automatically to work around a headless failure.
- **Quit shortcut guard:** Destructive quit key combos (`cmd+q`, `alt+f4`, etc.) are blocked (`POLICY_DENIED`) unless forced. Use `agent-desktop close-app <app>` or `pilot_ctl.py safe-close <app>` instead.
- **Sensitive or long text (`--stdin`):** Pipe text into `type --stdin`, `set-value --stdin`, or `clipboard-set --stdin` so secrets and multiline payloads stay out of shell arguments.
- **Platform modifiers:** Use `cmd` on macOS (`press cmd+s`) and `ctrl` on Windows/Linux (`press ctrl+s`).

### Surfaces, Menus, Notifications & Chromium/Electron

- **Surfaces:** Never use `--skeleton` on a surface (it is already focused). When an action opens an overlay, `data.surfaces` lists it; target it with `--surface`:
  - `menu`, `sheet`, `alert`, `popover` — active overlays (window refs behind them are the wrong target while open).
  - `menubar` — application menu bar (File/Edit/View).
  - `menu-bar-extras` / `dock` — macOS status bar items and Dock (check `agent-desktop status` for `supported_surfaces` on the current OS).
- **Combo boxes & menu buttons:** Use `select @ref "Option"` (dismiss any open menu first).
- **Notifications:** Use `list-notifications` and `wait --notification --app "<App>"`. Pass `--expected-app` and `--expected-title` when acting on or dismissing a notification.
- **Chromium & Electron apps:** `agent-desktop` enables renderer web accessibility automatically when supported. For dense web/Electron UIs, start with `snapshot --skeleton --app "<App>" -i --compact` and drill with `--root`.

---

## Parallelism: Level 1 & Level 2

### Level 1: Multi-Worker Host Sessions (`agent-desktop`)

When multiple subagents or workers operate concurrently on the host desktop, isolate their snapshot state with `--session` and `--agent-id`:

```bash
# Worker A
agent-desktop --session run-42 --agent-id worker-a list-windows --app "Google Chrome"
agent-desktop --session run-42 --agent-id worker-a snapshot --app "Google Chrome" --window-id w-101 -i --compact

# Worker B (concurrently on a different window)
agent-desktop --session run-42 --agent-id worker-b snapshot --app "Google Chrome" --window-id w-205 -i --compact
```

> **Multi-window gotcha:** On multi-window apps like **Google Chrome**, **Edge**, **Finder**, or **Explorer**, always pass `--app "<App>"` and `--window-id <id>` **together** on `snapshot`, `pilot_ctl.py screenshot`, `pilot_ctl.py window-tile`, and targeting commands. Passing only `--app` can hit the wrong window when another worker or the user shifts focus.

### Level 2: Isolated Virtual Linux Desktops + Live noVNC (`sandbox_ctl.py`)

When parallel tasks need complete display, clipboard, and filesystem isolation on **Windows, macOS, or Linux** (Docker / Colima), use `sandbox_ctl.py` (image `antigravity-desktop-sandbox:latest`, container prefix `ag-desk-*`):

| Command | Description |
| :--- | :--- |
| `sandbox_ctl.py build [--with-chromium]` | Build the `antigravity-desktop-sandbox:latest` container image (optionally bundling Chromium). |
| `sandbox_ctl.py up <name> [--vnc] [--vnc-port <port>]` | Start an isolated virtual desktop container (`ag-desk-<name>`). Pass `--vnc` (and optional `--vnc-port <port>`) to expose a live noVNC browser stream for human-in-the-loop takeover. |
| `sandbox_ctl.py url <name>` | Print the live noVNC web URL for a running `--vnc` sandbox. |
| `sandbox_ctl.py exec <name> <agent-desktop-args...>` | Run `agent-desktop` inside `ag-desk-<name>`. |
| `sandbox_ctl.py list [--json]` | List active `ag-desk-*` virtual desktop sandboxes. |
| `sandbox_ctl.py down <name>` | Stop and remove `ag-desk-<name>`. |
| `sandbox_ctl.py down-all` | Stop and remove all `ag-desk-*` sandboxes. |

```bash
SANDBOX_CTL="python3 $HOME/.gemini/config/plugins/antigravity-pilot/scripts/sandbox_ctl.py"

$SANDBOX_CTL build --with-chromium
$SANDBOX_CTL up desk-1 --vnc --vnc-port 6081
$SANDBOX_CTL url desk-1
$SANDBOX_CTL exec desk-1 status
$SANDBOX_CTL list --json
$SANDBOX_CTL down-all
```

Use `--vnc` + `url` whenever a sandboxed workflow needs human-in-the-loop interaction (such as 2FA, SSO login, or CAPTCHA) without exposing the host desktop.

---

## Primary Command Reference (`agent-desktop`)

### Permissions & Status

| Command | Description |
| :--- | :--- |
| `agent-desktop status` | Report platform, permissions, and `supported_surfaces`. |
| `agent-desktop permissions` | Non-prompting check of OS accessibility and screen-capture permissions. |
| `agent-desktop permissions --guide` | Guided permissions flow on macOS for Accessibility / Screen Recording. |

### Observation

| Command | Description |
| :--- | :--- |
| `agent-desktop snapshot [--app <name>] [--window-id <id>] [--skeleton] [--root <ref>] [--surface <kind>] [-i] [--compact]` | Capture the accessibility tree and mint `@<snapshot_id>:e<N>` refs. Omit `-i` when drilling a row container to read static-text labels alongside buttons. |
| `agent-desktop find --app <name> [--role <role>] [--name <text>]` | Locate elements directly by role/name when known. |
| `agent-desktop get <prop> <ref>` | Read live property (`value`, `title`, `text`, etc.) of a ref. |
| `agent-desktop is <state> <ref>` | Check boolean state (`enabled`, `checked`, `focused`, `visible`, etc.). |
| `agent-desktop list-surfaces [--app <name>]` | List currently active surfaces (`menu`, `sheet`, `alert`, `popover`). |
| `agent-desktop screenshot [--app <name>] [--out <path>]` | Capture a standard screenshot. |

### Interaction

| Command | Description |
| :--- | :--- |
| `agent-desktop click <ref>` | Semantic press/click (`right-click` opens context menus headlessly where supported). |
| `agent-desktop type <ref> [text] [--stdin] [--interval-ms <ms>]` | Type text into a field (`--stdin` reads UTF-8 from stdin). |
| `agent-desktop set-value <ref> [value] [--stdin]` | Set accessibility value directly (`--stdin` keeps secrets off argv). |
| `agent-desktop press <combo>` | Send key combos (`cmd+s` on macOS, `ctrl+s` on Windows/Linux). |
| `agent-desktop select <ref> "<option>"` | Choose an option in a combo box or menu button. |
| `agent-desktop toggle <ref>` / `check` / `uncheck` | Toggle or explicitly set checkbox/switch state. |
| `agent-desktop scroll <ref> ...` / `scroll-to <ref>` | Scroll a container or bring a ref into view. |
| `agent-desktop wait [--element <ref>] [--window <title>] [--text <str>] [--menu] [--notification]` | Event-driven wait (never use fixed `sleep` races). |
| `agent-desktop batch [--timeout-ms <ms>]` | Run a sequence of commands in one invocation (non-atomic). |

### Apps, Windows, Clipboard & Notifications

| Command | Description |
| :--- | :--- |
| `agent-desktop launch <app-or-id>` | Launch an app by display name or OS identifier. |
| `agent-desktop close-app <app>` | Close an app cleanly (preferred over quit hotkeys). |
| `agent-desktop list-apps` | List running applications. |
| `agent-desktop list-windows [--app <name>]` | List windows with stable window IDs. |
| `agent-desktop focus-window <window-id-or-app>` | Focus a specific window. |
| `agent-desktop clipboard-get [--format text\|auto\|image\|file-urls] [--out <path>]` | Read plain-text, image, or file URLs from the clipboard. |
| `agent-desktop clipboard-set [text] [--stdin] [--image <path>] [--file-url <path>]` | Write text, PNG image, or file URLs to the clipboard. |
| `agent-desktop list-notifications` | Read current OS notification items where supported. |

---

## Complement Command Reference (`pilot_ctl.py` / `bin/pilot-ctl`)

Use `python3 "$HOME/.gemini/config/plugins/antigravity-pilot/scripts/pilot_ctl.py"` (macOS/Linux), `bin/pilot-ctl` (macOS/Linux), or `python "%USERPROFILE%\.gemini\config\plugins\antigravity-pilot\scripts\pilot_ctl.py"` (Windows):

### Global Options (Risk-Guard & JSONL Audit Trace)

| Option / Env Var | Description |
| :--- | :--- |
| `--confirm-risky` | Allow a destructive or external-send action that Risk-Guard blocked with exit code `2` (`RISK_GUARD_BLOCKED`). **Only pass after explicit user approval.** |
| `--trace <file.jsonl>` | Append a compact JSONL audit trace entry for the invocation. |
| `PILOT_CTL_TRACE=<file.jsonl>` | Environment variable equivalent of `--trace <file.jsonl>` for automatic session-wide tracing. |

### Diagnostics, Set-of-Mark (`--annotate`), Window Tiling & Safe Actions (All Platforms)

| Command | Description |
| :--- | :--- |
| `pilot_ctl.py doctor [--json]` | Check OS backend, `agent-desktop`, permissions, Docker availability, and optional macOS native accelerator. |
| `pilot_ctl.py screenshot <out.png> [--app <App>] [--window-id <id>] [--screen <idx>] [--annotate] [--grid] [--max-depth <N>] [--limit <N>] [--session <S>] [--agent-id <A>] [--json]` | Capture a zero-dependency screenshot with **Set-of-Mark (SoM)** interactive bounding boxes + numbered refs (`--annotate`) and/or a labeled coordinate grid (`--grid`) for visual inspection via `read_file`. |
| `pilot_ctl.py window-tile <app> <left\|right\|top\|bottom\|maximize\|center> [--window-id <id>] [--title <substr>] [--display <1-based>] [--json]` | Snap an app's window to a screen half, full visible frame, or centered layout on the specified 1-based display. |
| `pilot_ctl.py guard-check <action> <target> [--json]` | Pre-flight Risk-Guard check on an action/target pair (exits `2` with `RISK_GUARD_BLOCKED` if risky). |
| `pilot_ctl.py safe-click <ref> [--label <text>] [--app <App>] [--headed]` | Click an `agent-desktop` ref through deterministic Risk-Guard enforcement (exits `2` on risky labels unless `--confirm-risky`). |
| `pilot_ctl.py safe-close <app> [--force]` | Close an app through Risk-Guard (`--force` exits `2` with `RISK_GUARD_BLOCKED` unless `--confirm-risky`). |
| `pilot_ctl.py notify <title> <message>` | Display a native OS notification on Windows, macOS, or Linux. |

### Parameterized Recipes (`recipe`) & macOS Accelerators

| Command | Platform | Description |
| :--- | :--- | :--- |
| `pilot_ctl.py recipe list [--json]` | Win / macOS / Linux | List saved parameterized recipes. |
| `pilot_ctl.py recipe show <name>` | Win / macOS / Linux | Print the JSON definition of a saved recipe. |
| `pilot_ctl.py recipe save <name> <file.json> \| --stdin` | Win / macOS / Linux | Save a recipe from a JSON file or from `--stdin`. |
| `pilot_ctl.py recipe run <name> [--param key=val ...] [--dry-run] [--confirm-risky]` | Win / macOS / Linux | Replay a saved recipe with `{{key}}` parameter substitution, `--dry-run` preview, and deterministic Risk-Guard enforcement. |
| `pilot_ctl.py recipe delete <name>` | Win / macOS / Linux | Delete a saved recipe by name. |
| `pilot_ctl.py record [--duration <sec>] [--output <file>] [--app <name>]` | macOS accelerator (`bin/macos-ctl`) | Record live mouse clicks (with AX role/title) and typing into a JSON recipe (requires Input Monitoring). |
| `pilot_ctl.py shortcut <name> [--input <text>]` | macOS accelerator (`bin/macos-ctl`) | Run an Apple Shortcut by name, optionally passing input text. |

---

## Example Workflows

### Workflow 1: Headless Semantic Control (`agent-desktop`)
```bash
agent-desktop snapshot --skeleton --app "Visual Studio Code" -i --compact
agent-desktop snapshot --root @s8f3k2p9:e3 -i --compact
printf '%s' 'quarterly review' | agent-desktop set-value @s8f3k2p9:e14 --stdin
agent-desktop wait --text "quarterly review"
```

### Workflow 2: Multi-Display Window Tiling & Audit Trace (`pilot_ctl.py`)
```bash
PILOT_CTL="$HOME/.gemini/config/plugins/antigravity-pilot/bin/pilot-ctl"

"$PILOT_CTL" --trace /tmp/pilot_trace.jsonl window-tile "Google Chrome" left --display 1
"$PILOT_CTL" --trace /tmp/pilot_trace.jsonl window-tile "Terminal" right --display 1
"$PILOT_CTL" notify "Workspace Ready" "Browser and Terminal tiled side by side."
```

### Workflow 3: Zero-Dep Set-of-Mark (`--annotate`) Visual Fallback
When `agent-desktop snapshot` cannot expose a canvas, WebGL, or custom view, use `--annotate` (optionally combined with `--grid` for exact coordinates):
```bash
python3 "$HOME/.gemini/config/plugins/antigravity-pilot/scripts/pilot_ctl.py" \
  screenshot /tmp/pilot_som.png --app "Figma" --annotate --grid --json
```
Inspect `/tmp/pilot_som.png` with `read_file` to read labeled Set-of-Mark refs and `(x, y)` coordinates, then act via `pilot_ctl.py safe-click <ref>` or `agent-desktop` and verify.

### Workflow 4: Preview & Replay a Parameterized Recipe with Risk-Guard
```bash
PILOT_CTL="python3 $HOME/.gemini/config/plugins/antigravity-pilot/scripts/pilot_ctl.py"

$PILOT_CTL recipe save deploy-check /tmp/deploy_check.json
$PILOT_CTL recipe run deploy-check --param env="staging" --dry-run
$PILOT_CTL recipe run deploy-check --param env="staging"
```

---

## Safety & Verification Checklist

- **Always verify by observation:** Re-run `snapshot`, `get`, or `is` after acting — never trust a return code alone.
- **Respect Risk-Guard (exit code `2`, `RISK_GUARD_BLOCKED`):** Stop immediately and escalate to the user before ever passing `--confirm-risky`.
- **Stop before high-impact actions:** Never delete files, close unsaved documents, send external emails/messages, authorize payments, or enter passwords/admin credentials without explicit user approval.
- **Permission blocks:** Run `agent-desktop permissions` and `pilot_ctl.py doctor`; if permissions are missing on macOS, offer `agent-desktop permissions --guide` and report the exact missing toggle in **System Settings → Privacy & Security**.
