# Antigravity Pilot (`antigravity-pilot` v1.0)

Cross-platform desktop computer use and parallel virtual desktop control across **Windows, macOS & Linux** using a **zero-MCP hybrid CLI architecture** (intentionally without an MCP server to avoid tool-schema token bloat):

- **Primary engine — `agent-desktop` (via `PATH` on Windows, macOS & Linux):** All UI observation, interaction, app/window lifecycle, clipboard, notifications, surfaces, `wait`, `batch`, permissions, and standard screenshots via native Accessibility APIs (`AX` on macOS, `UIA` on Windows, `AT-SPI` on Linux). Headless by default, native Chromium/Electron accessibility support, qualified `@<snapshot_id>:e<N>` stale-ref protection, `--stdin` for sensitive text, and built-in `POLICY_DENIED` guards.
- **Cross-platform complement engine — `pilot_ctl.py`:**
  - macOS / Linux: `python3 "$HOME/.gemini/config/plugins/antigravity-pilot/scripts/pilot_ctl.py"` (or wrapper `"$HOME/.gemini/config/plugins/antigravity-pilot/bin/pilot-ctl"`)
  - Windows: `python "%USERPROFILE%\.gemini\config\plugins\antigravity-pilot\scripts\pilot_ctl.py"`
  - Capabilities across Windows, macOS & Linux:
    - `doctor [--json]` — cross-platform readiness, runtime, and permission diagnostics
    - `screenshot <out.png> [--app <App>] [--window-id <id>] [--screen <idx>] [--annotate] [--grid] [--max-depth <N>] [--limit <N>] [--session <S>] [--agent-id <A>] [--json]` — zero-dependency Set-of-Mark (`--annotate`) interactive bounding boxes + refs and/or coordinate `--grid` visual fallback
    - `window-tile <app> <left|right|top|bottom|maximize|center> [--window-id <id>] [--title <substr>] [--display <1-based>] [--json]` — multi-display window tiling
    - `guard-check <action> <target> [--json]` / `safe-click <ref> [--label <text>] [--app <App>] [--headed]` / `safe-close <app> [--force]` — deterministic **Risk-Guard** (`RISK_GUARD_BLOCKED`, exit code `2` unless `--confirm-risky` is supplied)
    - `recipe list|show|save|run|delete` (`--param key=val`, `--dry-run`, `--stdin`, `--confirm-risky`) — parameterized reusable workflows
    - `notify <title> <message>` — cross-platform desktop notifications
    - `record [--duration <sec>] [--output <file>] [--app <name>]` & `shortcut <name> [--input <text>]` — macOS native accelerators (delegated via `bin/macos-ctl`)
    - Global flags: `--confirm-risky` and `--trace <file.jsonl>` (or `PILOT_CTL_TRACE` env var) for compact JSONL audit traces
- **Parallel Virtual Desktop Sandbox engine — `sandbox_ctl.py` (Windows, macOS & Linux with Docker/Colima):**
  - macOS / Linux: `python3 "$HOME/.gemini/config/plugins/antigravity-pilot/scripts/sandbox_ctl.py"` (or wrapper `"$HOME/.gemini/config/plugins/antigravity-pilot/bin/sandbox-ctl"`)
  - Windows: `python "%USERPROFILE%\.gemini\config\plugins\antigravity-pilot\scripts\sandbox_ctl.py"`
  - Spins up isolated virtual Linux desktops (`antigravity-desktop-sandbox:latest`, container prefix `ag-desk-*`) in `<1s` with optional live **noVNC** human-in-the-loop takeover.

## Operating Rules

1. **System & CLI first, semantic AX/UIA/AT-SPI second, visual `--annotate` / `--grid` last:**
   - Prefer `agent-desktop` commands (`launch`, `close-app`, `list-apps`, `list-windows`, `focus-window`, `clipboard-get`/`clipboard-set`) or `pilot_ctl.py` (`window-tile`, `recipe`, `notify`, `shortcut`) when UI clicking is unnecessary.
   - For UI work, run `agent-desktop`'s observe–act–verify loop (`snapshot --skeleton --app "<App>" -i --compact` → `snapshot --root @<id>:eN -i --compact` → act → re-observe). Target active overlays via `--surface menu|menubar|menu-bar-extras|sheet|alert|popover|dock`.
   - When an app lacks accessibility exposure or visual grounding is needed (canvas, WebGL, custom views), prefer `pilot_ctl.py screenshot <out.png> --app "<App>" --annotate` (optionally combined with `--grid`) + `read_file`.
2. **Headless by default & safe retries:**
   - Keep `agent-desktop` headless by default (semantic accessibility calls; no focus or cursor theft). Pass `--headed` only when physical input (`double-click`, `triple-click`, `hover`, `drag`) is required and permitted — never switch to `--headed` to work around a headless failure.
   - Retry an action **only** when `error.disposition.retry == "safe"` (`delivery == "not_delivered"`). Otherwise re-read live state (`get`, `is`, or fresh `snapshot`) before deciding.
   - Pass sensitive or long text via `--stdin` (`type --stdin`, `set-value --stdin`, `clipboard-set --stdin`). Use `close-app` or `pilot_ctl.py safe-close` instead of raw quit shortcuts (`POLICY_DENIED`).
3. **Deterministic Risk-Guard & Audit Tracing (`pilot_ctl.py`):**
   - `pilot_ctl.py` (`guard-check`, `safe-click`, `safe-close`, `recipe run`) deterministically intercepts destructive and external-send actions with **`RISK_GUARD_BLOCKED`** and exit code **`2`**.
   - **Never pass `--confirm-risky`** unless the user has explicitly approved the exact blocked action.
   - Pass `--trace <file.jsonl>` (or set `PILOT_CTL_TRACE`) whenever an audit trail of `pilot_ctl.py` actions is needed.

## Parallelism (Level 1 & Level 2)

- **Level 1 — Host Multi-Worker Sessions:** Isolate concurrent workers on the host desktop with `agent-desktop --session <id> --agent-id <worker>`. **Multi-window gotcha:** On multi-window apps (such as Chrome, Edge, Finder, or Explorer), always pass `--app "<App>"` and `--window-id <id>` together so each worker stays pinned to its intended window.
- **Level 2 — Isolated Virtual Linux Desktops (`sandbox_ctl.py`):** When parallel agents need fully isolated displays, clipboards, and app state without contending for the host desktop, use `sandbox_ctl.py` (or `bin/sandbox-ctl` on macOS/Linux):
  - `sandbox_ctl.py build [--with-chromium]` — build the `antigravity-desktop-sandbox:latest` image (Docker/Colima).
  - `sandbox_ctl.py up <name> [--vnc] [--vnc-port <port>]` — start container `ag-desk-<name>` (`<1s`), optionally exposing a live noVNC browser view for human-in-the-loop takeover (e.g. login, 2FA, CAPTCHA).
  - `sandbox_ctl.py url <name>` — print the live noVNC URL for a running `--vnc` sandbox.
  - `sandbox_ctl.py exec <name> <agent-desktop-args...>` — run `agent-desktop` inside `ag-desk-<name>`.
  - `sandbox_ctl.py list [--json]` / `down <name>` / `down-all` — inspect or tear down `ag-desk-*` sandboxes.

## Delegate Heavy Multi-Step GUI Flows

For multi-step desktop workflows that require repeated *observe → act → verify* iterations, delegate execution to the `desktop-operator` subagent (`agents/desktop-operator.md`) so the main conversation context stays clean.

## Safety Boundaries

Before executing any of the following, **stop and ask the user for explicit approval** unless unambiguously covered by the user's current mandate:
- **Destructive actions:** deleting files/records, emptying Trash/Recycle Bin, force-quitting or closing windows with unsaved work (never bypass `RISK_GUARD_BLOCKED` with `--confirm-risky` without explicit approval).
- **External sends & publishing:** sending emails, chat messages, posts, or submitting external forms.
- **Financial / legal commitments:** payments, purchases, transfers, or signing agreements.
- **System settings & credentials:** modifying OS security/privacy policies, or typing into password, Keychain, Credential Manager, Touch ID, UAC, or administrator authentication dialogs.

## Permissions & Troubleshooting

Check environment health and OS permissions first with:
```bash
agent-desktop permissions
python3 "$HOME/.gemini/config/plugins/antigravity-pilot/scripts/pilot_ctl.py" doctor
```
On Windows:
```powershell
agent-desktop status
python "%USERPROFILE%\.gemini\config\plugins\antigravity-pilot\scripts\pilot_ctl.py" doctor
```
If macOS Accessibility or Screen Recording is denied, guide the user with `agent-desktop permissions --guide` and report the exact missing toggle in **System Settings → Privacy & Security**. On Linux, verify AT-SPI accessibility (`accessibility-bus`) and `xdotool`/display server support reported by `doctor`.
