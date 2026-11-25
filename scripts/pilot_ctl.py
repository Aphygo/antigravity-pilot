#!/usr/bin/env python3
"""Cross-platform desktop automation complement CLI for antigravity-pilot.

Zero-dependency Python 3.9+ standard library CLI that works identically on
Windows, macOS, and Linux on top of `agent-desktop`, with optional native
`bin/macos-ctl` acceleration on macOS.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_DIR = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import png_overlay  # noqa: E402

VERSION = "3.1.0"

RISKY_KEYWORDS: Tuple[str, ...] = (
    "send",
    "skicka",
    "publish",
    "publicera",
    "post",
    "delete",
    "radera",
    "ta bort",
    "remove",
    "empty trash",
    "töm papperskorgen",
    "pay",
    "betala",
    "purchase",
    "köp",
    "transfer",
    "överför",
    "sign",
    "signera",
    "erase",
    "format",
    "confirm payment",
    "submit order",
)

_RISKY_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = tuple(
    (
        kw,
        re.compile(r"(?<![\w])(?:" + re.escape(kw) + r")(?![\w])", re.IGNORECASE | re.UNICODE),
    )
    for kw in RISKY_KEYWORDS
)

_FORCE_QUIT_ACTIONS = frozenset(
    {
        "quit --force",
        "close-app --force",
        "safe-close --force",
        "quit_force",
        "close_app_force",
    }
)

_RECIPE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.-]+)\s*\}\}")

TILE_POSITIONS = ("left", "right", "top", "bottom", "maximize", "center")


class PilotExit(Exception):
    """Structured exit exception so audit tracing always fires."""

    def __init__(self, code: int, risk_blocked: bool = False) -> None:
        super().__init__(f"Exit {code}")
        self.code = code
        self.risk_blocked = risk_blocked


def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _is_windows() -> bool:
    return os.name == "nt" or sys.platform == "win32"


def get_recipes_dir() -> Path:
    """Return cross-platform recipes directory (respecting env override)."""
    override = os.environ.get("ANTIGRAVITY_PILOT_RECIPES_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    if _is_windows():
        appdata = os.environ.get("APPDATA", "").strip()
        base = Path(appdata) if appdata else (Path.home() / "AppData" / "Roaming")
        return base / "antigravity-pilot" / "recipes"
    if sys.platform == "darwin":
        return Path.home() / ".config" / "antigravity-pilot" / "recipes"
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else (Path.home() / ".config")
    return base / "antigravity-pilot" / "recipes"


def get_legacy_macos_recipes_dir() -> Optional[Path]:
    """Optional read-only fallback on macOS for legacy antigravity-macos recipes."""
    if sys.platform != "darwin":
        return None
    if os.environ.get("ANTIGRAVITY_PILOT_RECIPES_DIR", "").strip():
        return None
    legacy = Path.home() / ".config" / "antigravity-macos" / "recipes"
    return legacy if legacy.is_dir() else None


def get_refs_cache_path() -> Path:
    return Path(tempfile.gettempdir()) / "antigravity-pilot-refs.json"


def find_agent_desktop() -> Optional[str]:
    """Locate `agent-desktop` cross-platform without hardcoded system paths."""
    if _is_windows():
        candidates = ["agent-desktop.exe", "agent-desktop.cmd", "agent-desktop.bat", "agent-desktop"]
    else:
        candidates = ["agent-desktop"]
    for name in candidates:
        found = shutil.which(name)
        if found:
            return found
    for name in candidates:
        local = PLUGIN_DIR / "bin" / name
        if local.is_file() and os.access(local, os.X_OK):
            return str(local)
    return None


def find_macos_ctl() -> Optional[Path]:
    """Locate optional local macOS native accelerator (`bin/macos-ctl`)."""
    if sys.platform != "darwin":
        return None
    candidate = PLUGIN_DIR / "bin" / "macos-ctl"
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return candidate
    return None


def run_subprocess(
    cmd: Sequence[str],
    *,
    input_text: Optional[str] = None,
    timeout: Optional[float] = 30.0,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(cmd),
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )


def parse_json_output(raw: str) -> Optional[Any]:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Handle occasional leading/trailing diagnostic lines
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("{") or line.startswith("["):
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
        return None


def run_agent_desktop(
    args: Sequence[str],
    *,
    session: Optional[str] = None,
    agent_id: Optional[str] = None,
    headed: bool = False,
    input_text: Optional[str] = None,
    timeout: float = 30.0,
) -> Tuple[int, Optional[Dict[str, Any]], str, str]:
    binary = find_agent_desktop()
    if not binary:
        err = {
            "ok": False,
            "error": {
                "code": "AGENT_DESKTOP_NOT_FOUND",
                "message": "agent-desktop binary not found in PATH.",
            },
        }
        return 127, err, "", "agent-desktop binary not found in PATH."

    cmd: List[str] = [binary]
    if session:
        cmd.extend(["--session", session])
    if agent_id:
        cmd.extend(["--agent-id", agent_id])
    if headed:
        cmd.append("--headed")
    cmd.extend(args)

    try:
        proc = run_subprocess(cmd, input_text=input_text, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        err = {
            "ok": False,
            "error": {
                "code": "TIMEOUT",
                "message": f"agent-desktop timed out after {timeout}s",
            },
        }
        return 124, err, str(exc.stdout or ""), str(exc.stderr or "")
    except OSError as exc:
        err = {
            "ok": False,
            "error": {"code": "EXEC_ERROR", "message": str(exc)},
        }
        return 1, err, "", str(exc)

    parsed = parse_json_output(proc.stdout)
    if not isinstance(parsed, dict):
        parsed = parse_json_output(proc.stderr)
    return proc.returncode, parsed if isinstance(parsed, dict) else None, proc.stdout, proc.stderr


def evaluate_risk_guard(action: str, target_label: str) -> Optional[Dict[str, Any]]:
    """Deterministically evaluate whether an action + target should be blocked.

    Returns a `RISK_GUARD_BLOCKED` dict if risky, or `None` if safe.
    """
    act_norm = " ".join((action or "").strip().lower().split())
    target_str = str(target_label or "")

    if act_norm in _FORCE_QUIT_ACTIONS:
        return {
            "code": "RISK_GUARD_BLOCKED",
            "action": action,
            "target": target_str,
            "matched_keyword": act_norm,
            "hint": "Re-run with --confirm-risky only after explicit user approval.",
        }

    if target_str.strip():
        for kw, pattern in _RISKY_PATTERNS:
            if pattern.search(target_str):
                return {
                    "code": "RISK_GUARD_BLOCKED",
                    "action": action,
                    "target": target_str,
                    "matched_keyword": kw,
                    "hint": "Re-run with --confirm-risky only after explicit user approval.",
                }
    return None


def enforce_risk_guard(action: str, target_label: str, confirm_risky: bool) -> None:
    """Block risky operations with exit code 2 unless `--confirm-risky` is set."""
    blocked = evaluate_risk_guard(action, target_label)
    if blocked and not confirm_risky:
        print(json.dumps(blocked, ensure_ascii=False, indent=2))
        raise PilotExit(2, risk_blocked=True)


def append_trace(
    trace_path: Optional[str],
    *,
    command: str,
    args: Sequence[str],
    risk_blocked: bool,
    exit_code: int,
    duration_ms: float,
) -> None:
    if not trace_path:
        return
    entry = {
        "timestamp": utc_now_iso(),
        "platform": sys.platform,
        "command": command,
        "args": list(args),
        "risk_blocked": bool(risk_blocked),
        "exit_code": int(exit_code),
        "duration_ms": round(float(duration_ms), 3),
    }
    try:
        path = Path(trace_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError as exc:
        print(f"Warning: failed to write trace to {trace_path}: {exc}", file=sys.stderr)


def fail_json_or_text(
    message: str,
    *,
    code: str = "ERROR",
    as_json: bool = False,
    extra: Optional[Dict[str, Any]] = None,
    exit_code: int = 1,
) -> None:
    payload: Dict[str, Any] = {"ok": False, "code": code, "message": message}
    if extra:
        payload.update(extra)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Error [{code}]: {message}", file=sys.stderr)
    raise PilotExit(exit_code)


# ---------------------------------------------------------------------------
# 1. doctor
# ---------------------------------------------------------------------------


def cmd_doctor(args: argparse.Namespace) -> int:
    ad_path = find_agent_desktop()
    ad_version: Optional[str] = None
    permissions: Any = None

    if ad_path:
        try:
            ver_proc = run_subprocess([ad_path, "--version"], timeout=5.0)
            if ver_proc.returncode == 0:
                ad_version = (ver_proc.stdout or ver_proc.stderr).strip()
        except (OSError, subprocess.TimeoutExpired):
            ad_version = None

        rc, perm_env, _out, _err = run_agent_desktop(["permissions"], timeout=8.0)
        if rc == 0 and isinstance(perm_env, dict):
            permissions = perm_env.get("data", perm_env)
        elif isinstance(perm_env, dict):
            permissions = perm_env.get("error", perm_env)

    docker_path = shutil.which("docker")
    docker_running = False
    if docker_path:
        try:
            dproc = run_subprocess([docker_path, "info"], timeout=3.0)
            docker_running = dproc.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            docker_running = False

    macos_ctl_path = find_macos_ctl()
    macos_accel: Dict[str, Any] = {
        "available": False,
        "path": None,
        "input_monitoring": None,
    }
    if macos_ctl_path:
        macos_accel["available"] = True
        macos_accel["path"] = str(macos_ctl_path)
        try:
            mproc = run_subprocess([str(macos_ctl_path), "doctor", "--json"], timeout=5.0)
            mdoc = parse_json_output(mproc.stdout)
            if isinstance(mdoc, dict):
                macos_accel["input_monitoring"] = mdoc.get("input_monitoring")
        except (OSError, subprocess.TimeoutExpired):
            pass

    report: Dict[str, Any] = {
        "ok": bool(ad_path),
        "version": VERSION,
        "platform": sys.platform,
        "arch": platform.machine(),
        "python": platform.python_version(),
        "agent_desktop": {
            "available": bool(ad_path),
            "path": ad_path,
            "version": ad_version,
        },
        "permissions": permissions,
        "docker": {
            "available": bool(docker_path),
            "path": docker_path,
            "running": docker_running,
        },
        "macos_accelerator": macos_accel,
        "recipes_dir": str(get_recipes_dir()),
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"antigravity-pilot doctor (v{VERSION})")
        print(f"  Platform:          {report['platform']} ({report['arch']})")
        print(f"  Python:            {report['python']}")
        if ad_path:
            print(f"  agent-desktop:     OK ({ad_version or 'installed'}) [{ad_path}]")
        else:
            print("  agent-desktop:     MISSING (not found in PATH)")
        if isinstance(permissions, dict):
            perm_parts = []
            for k, v in permissions.items():
                state = v.get("state") if isinstance(v, dict) else v
                perm_parts.append(f"{k}={state}")
            print(f"  Permissions:       {', '.join(perm_parts)}")
        else:
            print("  Permissions:       unavailable")
        docker_status = (
            f"running [{docker_path}]"
            if docker_running
            else (f"installed (daemon stopped) [{docker_path}]" if docker_path else "not installed")
        )
        print(f"  Docker:            {docker_status}")
        if sys.platform == "darwin":
            if macos_accel["available"]:
                im = macos_accel["input_monitoring"]
                im_str = "granted" if im is True else ("missing" if im is False else "unknown")
                print(f"  macOS Accelerator: available (input_monitoring={im_str}) [{macos_accel['path']}]")
            else:
                print("  macOS Accelerator: not built (optional; run ./install.sh)")
        print(f"  Recipes dir:       {report['recipes_dir']}")
    return 0


# ---------------------------------------------------------------------------
# Helpers for displays, windows, snapshots, ref cache
# ---------------------------------------------------------------------------


def fetch_displays(
    *,
    session: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    rc, env, _out, _err = run_agent_desktop(
        ["list-displays"], session=session, agent_id=agent_id, timeout=10.0
    )
    if rc == 0 and isinstance(env, dict) and isinstance(env.get("data"), list):
        return [d for d in env["data"] if isinstance(d, dict)]
    return []


def fetch_windows(
    app: Optional[str] = None,
    *,
    session: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    cmd = ["list-windows"]
    if app:
        cmd.extend(["--app", app])
    rc, env, _out, _err = run_agent_desktop(
        cmd, session=session, agent_id=agent_id, timeout=10.0
    )
    if rc == 0 and isinstance(env, dict) and isinstance(env.get("data"), list):
        return [w for w in env["data"] if isinstance(w, dict)]
    return []


def pick_window(
    windows: Sequence[Dict[str, Any]],
    *,
    window_id: Optional[str] = None,
    title: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if not windows:
        return None
    if window_id:
        for w in windows:
            if str(w.get("id", "")) == window_id:
                return w
        return None

    visible = [
        w for w in windows
        if w.get("visible", True) is not False and not w.get("minimized", False)
    ]
    pool = visible if visible else list(windows)

    if title:
        needle = title.lower()
        matches = [w for w in pool if needle in str(w.get("title") or "").lower()]
        if not matches and pool is not windows:
            matches = [w for w in windows if needle in str(w.get("title") or "").lower()]
        if not matches:
            return None
        pool = matches

    if len(pool) == 1:
        return pool[0]
    for w in pool:
        if w.get("is_focused"):
            return w
    return pool[0]


def pick_display(
    displays: Sequence[Dict[str, Any]],
    *,
    index_zero_based: Optional[int] = None,
    index_one_based: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    if not displays:
        return None
    if index_one_based is not None:
        idx = index_one_based - 1
        if 0 <= idx < len(displays):
            return displays[idx]
        return None
    if index_zero_based is not None:
        if 0 <= index_zero_based < len(displays):
            return displays[index_zero_based]
        return None
    for d in displays:
        if d.get("is_primary"):
            return d
    return displays[0]


def extract_snapshot_elements(
    tree: Any,
    *,
    limit: int = 120,
) -> List[Dict[str, Any]]:
    """Walk an agent-desktop snapshot tree depth-first and collect interactive elements."""
    elements: List[Dict[str, Any]] = []

    def walk(node: Any) -> None:
        if len(elements) >= limit or not isinstance(node, dict):
            return
        ref_id = node.get("ref_id")
        bounds = node.get("bounds")
        states = node.get("states") or []
        is_hidden = isinstance(states, list) and "hidden" in states

        if ref_id and isinstance(bounds, dict) and not is_hidden:
            try:
                bw = float(bounds.get("width", 0.0))
                bh = float(bounds.get("height", 0.0))
                bx = float(bounds.get("x", 0.0))
                by = float(bounds.get("y", 0.0))
            except (TypeError, ValueError):
                bw = bh = 0.0
                bx = by = 0.0
            if bw > 0.0 and bh > 0.0:
                short_id = f"e{len(elements) + 1}"
                elements.append(
                    {
                        "label": short_id,
                        "short_id": short_id,
                        "ref_id": str(ref_id),
                        "role": str(node.get("role") or ""),
                        "name": str(node.get("name") or node.get("value") or ""),
                        "bounds": {
                            "x": bx,
                            "y": by,
                            "width": bw,
                            "height": bh,
                        },
                    }
                )

        children = node.get("children")
        if isinstance(children, list):
            for child in children:
                if len(elements) >= limit:
                    break
                walk(child)

    walk(tree)
    return elements


def save_refs_cache(
    elements: Sequence[Dict[str, Any]],
    *,
    app: Optional[str] = None,
    window_id: Optional[str] = None,
    snapshot_id: Optional[str] = None,
) -> Path:
    cache_path = get_refs_cache_path()
    payload = {
        "updated_at": utc_now_iso(),
        "app": app,
        "window_id": window_id,
        "snapshot_id": snapshot_id,
        "elements": list(elements),
    }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return cache_path


def load_refs_cache() -> Dict[str, Any]:
    cache_path = get_refs_cache_path()
    if not cache_path.is_file():
        return {}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def lookup_cached_ref(ref_token: str) -> Optional[Dict[str, Any]]:
    cache = load_refs_cache()
    elems = cache.get("elements")
    if not isinstance(elems, list):
        return None
    for el in elems:
        if not isinstance(el, dict):
            continue
        if el.get("ref_id") == ref_token or el.get("short_id") == ref_token or el.get("label") == ref_token:
            return el
    return None


# ---------------------------------------------------------------------------
# 2. screenshot
# ---------------------------------------------------------------------------


def _is_ambiguous_target(env: Optional[Dict[str, Any]], stdout: str, stderr: str) -> bool:
    if isinstance(env, dict):
        err = env.get("error")
        if isinstance(err, dict) and str(err.get("code", "")).upper() == "AMBIGUOUS_TARGET":
            return True
    combined = f"{stdout}\n{stderr}"
    return "AMBIGUOUS_TARGET" in combined


def _is_window_offscreen_error(env: Optional[Dict[str, Any]], stdout: str, stderr: str) -> bool:
    parts: List[str] = [stdout or "", stderr or ""]
    if isinstance(env, dict):
        err = env.get("error")
        if isinstance(err, dict):
            parts.append(str(err.get("message") or ""))
            parts.append(str(err.get("code") or ""))
    combined = "\n".join(parts).lower()
    return "not fully on the screen" in combined or "outside the screen" in combined


def _select_display_for_window(
    displays: Sequence[Dict[str, Any]],
    win_bounds: Optional[Dict[str, Any]],
) -> Tuple[int, Optional[Dict[str, Any]]]:
    if not displays:
        return 0, None
    if isinstance(win_bounds, dict):
        wx = float(win_bounds.get("x", 0.0))
        wy = float(win_bounds.get("y", 0.0))
        ww = float(win_bounds.get("width", 0.0))
        wh = float(win_bounds.get("height", 0.0))
        best_idx = -1
        best_area = 0.0
        for idx, disp in enumerate(displays):
            db = disp.get("bounds")
            if not isinstance(db, dict):
                continue
            dx = float(db.get("x", 0.0))
            dy = float(db.get("y", 0.0))
            dw = float(db.get("width", 0.0))
            dh = float(db.get("height", 0.0))
            ix = max(0.0, min(wx + ww, dx + dw) - max(wx, dx))
            iy = max(0.0, min(wy + wh, dy + dh) - max(wy, dy))
            area = ix * iy
            if area > best_area:
                best_area = area
                best_idx = idx
        if best_idx >= 0:
            return best_idx, displays[best_idx]
    for idx, disp in enumerate(displays):
        if disp.get("is_primary"):
            return idx, disp
    return 0, displays[0]


def cmd_screenshot(args: argparse.Namespace) -> int:
    out_path = Path(args.output).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    displays = fetch_displays(session=args.session, agent_id=args.agent_id)
    selected_window: Optional[Dict[str, Any]] = None
    resolved_window_id: Optional[str] = args.window_id

    if args.app:
        windows = fetch_windows(args.app, session=args.session, agent_id=args.agent_id)
        selected_window = pick_window(windows, window_id=resolved_window_id)
        if selected_window and not resolved_window_id:
            resolved_window_id = str(selected_window.get("id") or "") or None
    elif resolved_window_id:
        windows = fetch_windows(None, session=args.session, agent_id=args.agent_id)
        selected_window = pick_window(windows, window_id=resolved_window_id)

    selected_display = pick_display(displays, index_zero_based=args.screen)

    # Step 2: Capture raw screenshot via agent-desktop (with macOS multi-PID & X11 offscreen-crop fallbacks)
    shot_cmd = ["screenshot"]
    if args.app:
        shot_cmd.extend(["--app", args.app])
        if resolved_window_id:
            shot_cmd.extend(["--window-id", resolved_window_id])
    elif resolved_window_id:
        shot_cmd.extend(["--window-id", resolved_window_id])
    else:
        screen_idx = args.screen if args.screen is not None else 0
        shot_cmd.extend(["--screen", str(screen_idx)])
    shot_cmd.append(str(out_path))

    rc, shot_env, shot_out, shot_err = run_agent_desktop(
        shot_cmd, session=args.session, agent_id=args.agent_id, timeout=20.0
    )

    used_macos_fallback = False
    used_screen_crop_fallback = False
    raw_buf: Optional[bytearray] = None
    cropped_logical_bounds: Optional[Dict[str, Any]] = None
    img_w = 0
    img_h = 0

    if rc != 0 or not out_path.is_file():
        macos_ctl = find_macos_ctl()
        if args.app and macos_ctl and _is_ambiguous_target(shot_env, shot_out, shot_err):
            fb = run_subprocess(
                [str(macos_ctl), "screenshot", str(out_path), "--app", args.app],
                timeout=20.0,
            )
            if fb.returncode == 0 and out_path.is_file():
                used_macos_fallback = True
            else:
                fail_json_or_text(
                    f"Screenshot failed on both agent-desktop and macos-ctl fallback: {fb.stderr.strip() or fb.stdout.strip()}",
                    code="SCREENSHOT_FAILED",
                    as_json=args.json,
                )
        elif (args.app or resolved_window_id) and _is_window_offscreen_error(shot_env, shot_out, shot_err):
            # Linux X11 XGetImage fails when a tiled/framed window extends even 1px past the screen edge.
            # Fall back to capturing the containing display and cropping to the visible window intersection.
            win_bounds = selected_window.get("bounds") if isinstance(selected_window, dict) else None
            disp_idx, disp_obj = _select_display_for_window(displays, win_bounds)
            fb_rc, fb_env, fb_out, fb_err = run_agent_desktop(
                ["screenshot", "--screen", str(disp_idx), str(out_path)],
                session=args.session,
                agent_id=args.agent_id,
                timeout=20.0,
            )
            if fb_rc == 0 and out_path.is_file():
                used_screen_crop_fallback = True
                shot_env = fb_env
                disp_img_w, disp_img_h, disp_buf = png_overlay.read_png(out_path)
                db = disp_obj.get("bounds") if isinstance(disp_obj, dict) else None
                if isinstance(db, dict) and float(db.get("width", 0.0)) > 0.0 and float(db.get("height", 0.0)) > 0.0:
                    dx = float(db.get("x", 0.0))
                    dy = float(db.get("y", 0.0))
                    dw = float(db.get("width", 0.0))
                    dh = float(db.get("height", 0.0))
                    dsx = disp_img_w / dw
                    dsy = disp_img_h / dh
                else:
                    dx, dy = 0.0, 0.0
                    dsx = float((disp_obj or {}).get("scale", 1.0) or 1.0)
                    dsy = dsx
                    dw = disp_img_w / dsx
                    dh = disp_img_h / dsy

                if isinstance(win_bounds, dict) and float(win_bounds.get("width", 0.0)) > 0.0 and float(win_bounds.get("height", 0.0)) > 0.0:
                    wx = float(win_bounds.get("x", 0.0))
                    wy = float(win_bounds.get("y", 0.0))
                    ww = float(win_bounds.get("width", 0.0))
                    wh = float(win_bounds.get("height", 0.0))
                    vx0 = max(dx, wx)
                    vy0 = max(dy, wy)
                    vx1 = min(dx + dw, wx + ww)
                    vy1 = min(dy + dh, wy + wh)
                    px0 = max(0, int(round((vx0 - dx) * dsx)))
                    py0 = max(0, int(round((vy0 - dy) * dsy)))
                    px1 = min(disp_img_w, int(round((vx1 - dx) * dsx)))
                    py1 = min(disp_img_h, int(round((vy1 - dy) * dsy)))
                    if px1 > px0 and py1 > py0:
                        img_w, img_h, raw_buf = png_overlay.crop_rgba(
                            disp_buf, disp_img_w, disp_img_h, px0, py0, px1 - px0, py1 - py0
                        )
                        cropped_logical_bounds = {
                            "x": dx + px0 / dsx,
                            "y": dy + py0 / dsy,
                            "width": img_w / dsx,
                            "height": img_h / dsy,
                        }
                    else:
                        img_w, img_h, raw_buf = disp_img_w, disp_img_h, disp_buf
                        cropped_logical_bounds = {"x": dx, "y": dy, "width": dw, "height": dh}
                else:
                    img_w, img_h, raw_buf = disp_img_w, disp_img_h, disp_buf
                    cropped_logical_bounds = {"x": dx, "y": dy, "width": dw, "height": dh}
            else:
                err_msg = ""
                if isinstance(fb_env, dict) and isinstance(fb_env.get("error"), dict):
                    err_msg = str(fb_env["error"].get("message") or fb_env["error"])
                if not err_msg:
                    err_msg = fb_err.strip() or fb_out.strip() or "display screenshot fallback failed"
                fail_json_or_text(err_msg, code="SCREENSHOT_FAILED", as_json=args.json)
        else:
            err_msg = ""
            if isinstance(shot_env, dict) and isinstance(shot_env.get("error"), dict):
                err_msg = str(shot_env["error"].get("message") or shot_env["error"])
            if not err_msg:
                err_msg = shot_err.strip() or shot_out.strip() or "agent-desktop screenshot failed"
            fail_json_or_text(err_msg, code="SCREENSHOT_FAILED", as_json=args.json)

    if not used_screen_crop_fallback:
        img_w, img_h = png_overlay.read_png_dimensions(out_path)

    # Step 3: Optional SoM snapshot + exact origin/scale computation
    elements: List[Dict[str, Any]] = []
    snapshot_id: Optional[str] = None
    tree_bounds: Optional[Dict[str, Any]] = None

    if args.annotate:
        snap_cmd = ["snapshot", "-i", "--include-bounds"]
        if args.max_depth is not None:
            snap_cmd.extend(["--max-depth", str(args.max_depth)])
        if args.app:
            snap_cmd.extend(["--app", args.app])
        if resolved_window_id:
            snap_cmd.extend(["--window-id", resolved_window_id])

        snap_rc, snap_env, _snap_out, _snap_err = run_agent_desktop(
            snap_cmd, session=args.session, agent_id=args.agent_id, timeout=20.0
        )
        if snap_rc == 0 and isinstance(snap_env, dict) and isinstance(snap_env.get("data"), dict):
            sdata = snap_env["data"]
            snapshot_id = sdata.get("snapshot_id")
            tree = sdata.get("tree")
            if isinstance(tree, dict) and isinstance(tree.get("bounds"), dict):
                tree_bounds = tree["bounds"]
            elements = extract_snapshot_elements(tree, limit=max(1, int(args.limit)))

    # Determine logical bounds for origin and pixel scale factor
    logical_bounds: Optional[Dict[str, Any]] = None
    if used_screen_crop_fallback and cropped_logical_bounds:
        logical_bounds = cropped_logical_bounds
    elif args.app or resolved_window_id:
        if selected_window and isinstance(selected_window.get("bounds"), dict):
            logical_bounds = selected_window["bounds"]
        elif tree_bounds:
            logical_bounds = tree_bounds
    elif selected_display and isinstance(selected_display.get("bounds"), dict):
        logical_bounds = selected_display["bounds"]

    origin_x = 0.0
    origin_y = 0.0
    scale_x = 1.0
    scale_y = 1.0

    # Decode PNG once if we need overlays or shadow-border detection
    if raw_buf is None and (args.annotate or args.grid):
        img_w, img_h, raw_buf = png_overlay.read_png(out_path)

    if logical_bounds:
        origin_x = float(logical_bounds.get("x", 0.0))
        origin_y = float(logical_bounds.get("y", 0.0))
        lw = float(logical_bounds.get("width", 0.0))
        lh = float(logical_bounds.get("height", 0.0))
        if lw > 0.0 and lh > 0.0:
            # Account for transparent window drop-shadow padding (e.g. macOS/Windows window capture)
            if raw_buf is not None and (args.app or resolved_window_id) and not used_screen_crop_fallback:
                ox, oy, cw, ch = png_overlay.detect_opaque_bounds(raw_buf, img_w, img_h)
                if cw > 0 and ch > 0 and (ox > 0 or oy > 0 or cw < img_w or ch < img_h):
                    scale_x = cw / lw
                    scale_y = ch / lh
                    origin_x -= ox / scale_x
                    origin_y -= oy / scale_y
                else:
                    scale_x = img_w / lw
                    scale_y = img_h / lh
            else:
                scale_x = img_w / lw
                scale_y = img_h / lh
    elif selected_display:
        dscale = float(selected_display.get("scale", 1.0) or 1.0)
        scale_x = dscale
        scale_y = dscale
    elif isinstance(shot_env, dict) and isinstance(shot_env.get("data"), dict):
        sf = float(shot_env["data"].get("scale_factor", 1.0) or 1.0)
        scale_x = sf
        scale_y = sf

    # Step 4: Render grid and/or SoM annotations via zero-dependency png_overlay
    rendered_count = 0
    if raw_buf is not None and (args.annotate or args.grid or used_screen_crop_fallback):
        if args.grid:
            png_overlay.render_grid(raw_buf, img_w, img_h, step=100, scale_x=scale_x, scale_y=scale_y)
        if args.annotate and elements:
            rendered_count = png_overlay.render_som_annotations(
                raw_buf,
                img_w,
                img_h,
                elements,
                origin_x=origin_x,
                origin_y=origin_y,
                scale_x=scale_x,
                scale_y=scale_y,
            )
        png_overlay.write_png(out_path, img_w, img_h, raw_buf)

    cache_path: Optional[Path] = None
    if args.annotate:
        cache_path = save_refs_cache(
            elements,
            app=args.app,
            window_id=resolved_window_id,
            snapshot_id=snapshot_id,
        )

    result = {
        "ok": True,
        "path": str(out_path),
        "width": img_w,
        "height": img_h,
        "app": args.app,
        "window_id": resolved_window_id,
        "annotated": bool(args.annotate),
        "grid": bool(args.grid),
        "origin_x": round(origin_x, 2),
        "origin_y": round(origin_y, 2),
        "scale_x": round(scale_x, 4),
        "scale_y": round(scale_y, 4),
        "rendered_elements": rendered_count,
        "elements_count": len(elements),
        "snapshot_id": snapshot_id,
        "macos_fallback": used_macos_fallback,
        "screen_crop_fallback": used_screen_crop_fallback,
        "refs_cache": str(cache_path) if cache_path else None,
        "elements": [
            {
                "short_id": el["short_id"],
                "ref_id": el["ref_id"],
                "role": el["role"],
                "name": el["name"],
                "bounds": el["bounds"],
            }
            for el in elements
        ],
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        flags_desc = []
        if args.annotate:
            flags_desc.append(f"som={rendered_count}/{len(elements)}")
        if args.grid:
            flags_desc.append("grid=100pt")
        extra = f" ({', '.join(flags_desc)})" if flags_desc else ""
        print(f"Saved screenshot: {out_path} [{img_w}x{img_h}]{extra}")
        for el in elements:
            b = el["bounds"]
            cx = int(round(b["x"] + b["width"] / 2.0))
            cy = int(round(b["y"] + b["height"] / 2.0))
            label_name = el["name"].replace("\n", " ").strip()
            print(
                f"  [{el['short_id']}] ({el['ref_id']}) {el['role']} \"{label_name}\" @({cx},{cy})"
            )
    return 0


# ---------------------------------------------------------------------------
# 3. window-tile
# ---------------------------------------------------------------------------


def compute_tile_rect(
    display: Dict[str, Any],
    position: str,
    *,
    platform_name: Optional[str] = None,
) -> Tuple[int, int, int, int]:
    plat = sys.platform if platform_name is None else platform_name
    bounds = display.get("bounds") or {}
    dx = float(bounds.get("x", 0.0))
    dy = float(bounds.get("y", 0.0))
    dw = float(bounds.get("width", 1280.0))
    dh = float(bounds.get("height", 800.0))

    # Reserve menu-bar margin on macOS primary display when y == 0
    if plat == "darwin" and display.get("is_primary") and dy == 0.0:
        top_margin = 25.0
        dy += top_margin
        dh = max(100.0, dh - top_margin)

    pos = position.lower()
    if pos == "left":
        tx, ty, tw, th = dx, dy, dw / 2.0, dh
    elif pos == "right":
        tx, ty, tw, th = dx + dw / 2.0, dy, dw / 2.0, dh
    elif pos == "top":
        tx, ty, tw, th = dx, dy, dw, dh / 2.0
    elif pos == "bottom":
        tx, ty, tw, th = dx, dy + dh / 2.0, dw, dh / 2.0
    elif pos == "maximize":
        tx, ty, tw, th = dx, dy, dw, dh
    elif pos == "center":
        tw = dw * 0.7
        th = dh * 0.75
        tx = dx + (dw - tw) / 2.0
        ty = dy + (dh - th) / 2.0
    else:
        raise ValueError(f"Invalid tile position: {position}")

    return int(round(tx)), int(round(ty)), int(round(tw)), int(round(th))


def cmd_window_tile(args: argparse.Namespace) -> int:
    displays = fetch_displays(session=args.session, agent_id=args.agent_id)
    if not displays:
        fail_json_or_text("No displays returned by agent-desktop list-displays.", code="NO_DISPLAYS", as_json=args.json)

    display = pick_display(displays, index_one_based=args.display)
    if not display:
        fail_json_or_text(
            f"Display {args.display} out of range (found {len(displays)} display(s)).",
            code="DISPLAY_NOT_FOUND",
            as_json=args.json,
        )

    windows = fetch_windows(args.app, session=args.session, agent_id=args.agent_id)
    win = pick_window(windows, window_id=args.window_id, title=args.title)
    if not win:
        fail_json_or_text(
            f"No matching window found for app '{args.app}'.",
            code="WINDOW_NOT_FOUND",
            as_json=args.json,
        )

    wid = str(win.get("id") or "")
    tx, ty, tw, th = compute_tile_rect(display, args.position)

    # Always use --x= and --y= with '=' so negative multi-monitor coordinates parse cleanly.
    move_cmd = ["move-window", "--app", args.app]
    if wid:
        move_cmd.extend(["--window-id", wid])
    move_cmd.extend([f"--x={tx}", f"--y={ty}"])
    mrc, menv, mout, merr = run_agent_desktop(
        move_cmd, session=args.session, agent_id=args.agent_id, timeout=15.0
    )
    if mrc != 0:
        fail_json_or_text(
            f"move-window failed: {merr.strip() or mout.strip() or menv}",
            code="MOVE_WINDOW_FAILED",
            as_json=args.json,
        )

    resize_cmd = ["resize-window", "--app", args.app]
    if wid:
        resize_cmd.extend(["--window-id", wid])
    resize_cmd.extend(["--width", str(tw), "--height", str(th)])
    rrc, renv, rout, rerr = run_agent_desktop(
        resize_cmd, session=args.session, agent_id=args.agent_id, timeout=15.0
    )
    if rrc != 0:
        fail_json_or_text(
            f"resize-window failed: {rerr.strip() or rout.strip() or renv}",
            code="RESIZE_WINDOW_FAILED",
            as_json=args.json,
        )

    payload = {
        "ok": True,
        "app": args.app,
        "window_id": wid,
        "title": win.get("title"),
        "position": args.position,
        "display_id": display.get("id"),
        "target_bounds": {"x": tx, "y": ty, "width": tw, "height": th},
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"Tiled '{args.app}' ({wid}) -> {args.position} "
            f"@({tx},{ty}) {tw}x{th}"
        )
    return 0


# ---------------------------------------------------------------------------
# 4. Deterministic Risk-Guard: guard-check, safe-click, safe-close
# ---------------------------------------------------------------------------


def cmd_guard_check(args: argparse.Namespace, confirm_risky: bool) -> int:
    blocked = evaluate_risk_guard(args.action, args.target)
    if blocked and not confirm_risky:
        print(json.dumps(blocked, ensure_ascii=False, indent=2))
        raise PilotExit(2, risk_blocked=True)

    payload = {
        "ok": True,
        "risk_blocked": False,
        "action": args.action,
        "target": args.target,
        "matched_keyword": blocked["matched_keyword"] if blocked else None,
        "confirmed": bool(blocked and confirm_risky),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if blocked and confirm_risky:
            print(
                f"ALLOWED via --confirm-risky: action '{args.action}' on '{args.target}' "
                f"(matched '{blocked['matched_keyword']}')"
            )
        else:
            print(f"SAFE: action '{args.action}' on '{args.target}'")
    return 0


def resolve_ref_and_label(
    ref_token: str,
    *,
    explicit_label: Optional[str] = None,
    fetch_label: bool = True,
    session: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Tuple[str, str]:
    """Resolve short ref (`e1`) or qualified ref (`@s...:e1`) and gather label text."""
    resolved_ref = ref_token
    labels: List[str] = []
    if explicit_label:
        labels.append(explicit_label)

    cached = lookup_cached_ref(ref_token)
    if cached:
        if cached.get("ref_id") and not ref_token.startswith("@"):
            resolved_ref = str(cached["ref_id"])
        if cached.get("name"):
            labels.append(str(cached["name"]))

    # Fetch live accessible text if requested, no label yet, and ref is qualified
    if fetch_label and not labels and resolved_ref.startswith("@"):
        rc, env, _out, _err = run_agent_desktop(
            ["get", resolved_ref, "--property", "text"],
            session=session,
            agent_id=agent_id,
            timeout=10.0,
        )
        if rc == 0 and isinstance(env, dict) and isinstance(env.get("data"), dict):
            val = env["data"].get("value")
            if val:
                labels.append(str(val))

    return resolved_ref, " | ".join(dict.fromkeys(l for l in labels if l.strip()))


def cmd_safe_click(args: argparse.Namespace, confirm_risky: bool) -> int:
    resolved_ref, label_text = resolve_ref_and_label(
        args.ref,
        explicit_label=args.label,
        session=args.session,
        agent_id=args.agent_id,
    )
    enforce_risk_guard("click", label_text, confirm_risky)

    rc, env, out, err = run_agent_desktop(
        ["click", resolved_ref],
        session=args.session,
        agent_id=args.agent_id,
        headed=bool(args.headed),
        timeout=15.0,
    )
    if args.json:
        if isinstance(env, dict):
            print(json.dumps(env, ensure_ascii=False, indent=2))
        else:
            print(
                json.dumps(
                    {"ok": rc == 0, "ref": resolved_ref, "label": label_text, "stdout": out, "stderr": err},
                    ensure_ascii=False,
                    indent=2,
                )
            )
    else:
        if rc == 0:
            desc = f' "{label_text}"' if label_text else ""
            print(f"Clicked {resolved_ref}{desc}")
        else:
            print(err.strip() or out.strip() or f"click failed (exit {rc})", file=sys.stderr)
    return rc


def cmd_safe_close(args: argparse.Namespace, confirm_risky: bool) -> int:
    if args.force:
        enforce_risk_guard("close-app --force", args.app, confirm_risky)

    cmd = ["close-app", args.app]
    if args.force:
        cmd.append("--force")

    rc, env, out, err = run_agent_desktop(
        cmd,
        session=args.session,
        agent_id=args.agent_id,
        timeout=15.0,
    )
    if args.json:
        if isinstance(env, dict):
            print(json.dumps(env, ensure_ascii=False, indent=2))
        else:
            print(
                json.dumps(
                    {"ok": rc == 0, "app": args.app, "force": bool(args.force), "stdout": out, "stderr": err},
                    ensure_ascii=False,
                    indent=2,
                )
            )
    else:
        if rc == 0:
            mode = "force-closed" if args.force else "closed"
            print(f"Successfully {mode} '{args.app}'")
        else:
            print(err.strip() or out.strip() or f"close-app failed (exit {rc})", file=sys.stderr)
    return rc


# ---------------------------------------------------------------------------
# 5. Cross-Platform Self-Learning Recipes
# ---------------------------------------------------------------------------


def validate_recipe_name(name: str, *, as_json: bool = False) -> str:
    if not name or "/" in name or "\\" in name or ".." in name or "\x00" in name:
        fail_json_or_text(
            f"Invalid recipe name '{name}': path traversal and separators are forbidden.",
            code="INVALID_RECIPE_NAME",
            as_json=as_json,
        )
    if not _RECIPE_NAME_RE.match(name):
        fail_json_or_text(
            f"Invalid recipe name '{name}': use only alphanumeric characters, '.', '_', '-'.",
            code="INVALID_RECIPE_NAME",
            as_json=as_json,
        )
    return name


def locate_recipe_file(name: str) -> Optional[Path]:
    primary = get_recipes_dir() / f"{name}.json"
    if primary.is_file():
        return primary
    legacy = get_legacy_macos_recipes_dir()
    if legacy:
        candidate = legacy / f"{name}.json"
        if candidate.is_file():
            return candidate
    return None


def collect_placeholders(obj: Any) -> List[str]:
    found: List[str] = []

    def walk(val: Any) -> None:
        if isinstance(val, str):
            for m in _PLACEHOLDER_RE.finditer(val):
                k = m.group(1)
                if k not in found:
                    found.append(k)
        elif isinstance(val, list):
            for item in val:
                walk(item)
        elif isinstance(val, dict):
            for v in val.values():
                walk(v)

    walk(obj)
    return found


def substitute_placeholders(obj: Any, params: Dict[str, str]) -> Any:
    if isinstance(obj, str):
        return _PLACEHOLDER_RE.sub(lambda m: params[m.group(1)], obj)
    if isinstance(obj, list):
        return [substitute_placeholders(item, params) for item in obj]
    if isinstance(obj, dict):
        return {k: substitute_placeholders(v, params) for k, v in obj.items()}
    return obj


def _step_action(step: Dict[str, Any]) -> str:
    return str(step.get("action") or step.get("type") or "").strip().lower()


def assess_step_risk(step: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    act = _step_action(step)
    if act in ("quit", "close-app", "safe-close") and step.get("force") is True:
        target = str(step.get("app") or step.get("target") or "")
        return evaluate_risk_guard("close-app --force", target)

    if act in ("click", "press", "safe-click"):
        candidates: List[str] = []
        for field in ("label", "name", "title", "target"):
            val = step.get(field)
            if isinstance(val, str) and val.strip():
                candidates.append(val)
        find_obj = step.get("find")
        if isinstance(find_obj, dict):
            for field in ("name", "text", "label", "value"):
                val = find_obj.get(field)
                if isinstance(val, str) and val.strip():
                    candidates.append(val)
        for text in candidates:
            risk = evaluate_risk_guard(act, text)
            if risk:
                return risk
        return None

    if act == "menu":
        items = step.get("path") or step.get("items") or []
        if isinstance(items, list):
            joined = " > ".join(str(x) for x in items)
            risk = evaluate_risk_guard("menu", joined)
            if risk:
                return risk
            for item in items:
                risk = evaluate_risk_guard("menu", str(item))
                if risk:
                    return risk
        elif isinstance(items, str):
            return evaluate_risk_guard("menu", items)
        return None

    return None


def describe_step(step: Dict[str, Any]) -> str:
    act = _step_action(step)
    if act in ("launch", "open"):
        return f"{act} {step.get('app') or step.get('target') or ''}".strip()
    if act == "focus":
        return f"focus {step.get('app') or step.get('window_id') or step.get('title') or ''}".strip()
    if act in ("click", "press", "safe-click"):
        tgt = step.get("ref") or step.get("label") or step.get("name") or step.get("title") or step.get("target")
        if not tgt and isinstance(step.get("find"), dict):
            tgt = json.dumps(step["find"], ensure_ascii=False)
        return f"{act} {tgt or ''}".strip()
    if act in ("set", "set-value"):
        tgt = step.get("ref") or step.get("name") or step.get("target") or ""
        return f"{act} {tgt} (stdin)"
    if act == "type":
        return f"type ({len(str(step.get('text') or step.get('value') or ''))} chars via stdin)"
    if act == "key":
        return f"key {step.get('combo') or step.get('key') or ''}".strip()
    if act == "wait":
        if step.get("wait_for"):
            return f"wait wait_for={step['wait_for']}"
        return f"wait {step.get('seconds', step.get('duration', 1.0))}s"
    if act == "window-tile":
        pos = step.get("position") or step.get("layout") or ""
        return f"window-tile {step.get('app') or ''} {pos}".strip()
    if act == "menu":
        items = step.get("path") or step.get("items") or []
        path_str = " > ".join(str(x) for x in items) if isinstance(items, list) else str(items)
        return f"menu {step.get('app') or ''} {path_str}".strip()
    return f"{act or 'unknown'} {json.dumps(step, ensure_ascii=False)}"


def _open_url_or_path(target: str) -> int:
    if sys.platform == "darwin":
        return run_subprocess(["open", target], timeout=15.0).returncode
    if _is_windows():
        try:
            os.startfile(target)  # type: ignore[attr-defined]
            return 0
        except OSError:
            return 1
    opener = shutil.which("xdg-open")
    if opener:
        return run_subprocess([opener, target], timeout=15.0).returncode
    return 1


def _find_first_ref(
    *,
    app: Optional[str] = None,
    window_id: Optional[str] = None,
    name: Optional[str] = None,
    role: Optional[str] = None,
    text: Optional[str] = None,
    surface: Optional[str] = None,
    session: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Tuple[Optional[str], Optional[Dict[str, Any]], str]:
    cmd = ["find", "--first"]
    if app:
        cmd.extend(["--app", app])
    if window_id:
        cmd.extend(["--window-id", window_id])
    if name:
        cmd.extend(["--name", name])
    if role:
        cmd.extend(["--role", role])
    if text:
        cmd.extend(["--text", text])
    if surface:
        cmd.extend(["--surface", surface])

    rc, env, out, err = run_agent_desktop(
        cmd, session=session, agent_id=agent_id, timeout=15.0
    )
    if rc == 0 and isinstance(env, dict) and isinstance(env.get("data"), dict):
        match = env["data"].get("match")
        if isinstance(match, dict) and match.get("ref_id"):
            return str(match["ref_id"]), match, ""
        matches = env["data"].get("matches")
        if isinstance(matches, list) and matches and isinstance(matches[0], dict) and matches[0].get("ref_id"):
            return str(matches[0]["ref_id"]), matches[0], ""
    err_detail = err.strip() or out.strip() or "element not found"
    return None, None, err_detail


def execute_recipe_step(
    step: Dict[str, Any],
    *,
    confirm_risky: bool,
    session: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    act = _step_action(step)
    step_app = step.get("app")
    step_wid = step.get("window_id")

    if act in ("launch", "open"):
        target = str(step.get("app") or step.get("target") or "").strip()
        if not target:
            raise ValueError(f"Step '{act}' requires 'app' or 'target'.")
        if act == "open" and (
            target.startswith(("http://", "https://", "file://")) or Path(target).expanduser().exists()
        ):
            rc = _open_url_or_path(target)
            if rc != 0:
                raise RuntimeError(f"Failed to open '{target}' (exit {rc})")
            return {"action": act, "target": target, "ok": True}
        rc, _env, out, err = run_agent_desktop(
            ["launch", target, "--activate"], session=session, agent_id=agent_id, timeout=30.0
        )
        if rc != 0:
            raise RuntimeError(f"launch '{target}' failed: {err.strip() or out.strip()}")
        return {"action": act, "app": target, "ok": True}

    if act == "focus":
        cmd = ["focus-window"]
        if step_app:
            cmd.extend(["--app", str(step_app)])
        elif step.get("target"):
            cmd.extend(["--app", str(step["target"])])
        if step_wid:
            cmd.extend(["--window-id", str(step_wid)])
        if step.get("title"):
            cmd.extend(["--title", str(step["title"])])
        if len(cmd) == 1:
            raise ValueError("Step 'focus' requires 'app', 'window_id', or 'title'.")
        rc, _env, out, err = run_agent_desktop(cmd, session=session, agent_id=agent_id, timeout=15.0)
        if rc != 0:
            raise RuntimeError(f"focus-window failed: {err.strip() or out.strip()}")
        return {"action": act, "ok": True}

    if act in ("click", "press", "safe-click"):
        ref_val = step.get("ref")
        find_obj = step.get("find") if isinstance(step.get("find"), dict) else {}
        resolved_ref: Optional[str] = None
        matched_label = str(step.get("label") or step.get("name") or step.get("title") or "")

        if isinstance(ref_val, str) and ref_val.strip():
            resolved_ref, live_label = resolve_ref_and_label(
                ref_val.strip(),
                explicit_label=matched_label or None,
                session=session,
                agent_id=agent_id,
            )
            matched_label = live_label or matched_label
        else:
            fname = find_obj.get("name") or step.get("name") or step.get("title") or step.get("target")
            frole = find_obj.get("role") or step.get("role")
            ftext = find_obj.get("text")
            fapp = find_obj.get("app") or step_app
            fwid = find_obj.get("window_id") or step_wid
            if fname or frole or ftext:
                resolved_ref, match, ferr = _find_first_ref(
                    app=str(fapp) if fapp else None,
                    window_id=str(fwid) if fwid else None,
                    name=str(fname) if fname else None,
                    role=str(frole) if frole else None,
                    text=str(ftext) if ftext else None,
                    session=session,
                    agent_id=agent_id,
                )
                if not resolved_ref:
                    raise RuntimeError(f"Could not find element for '{act}': {ferr}")
                if match and match.get("name"):
                    matched_label = f"{matched_label} {match['name']}".strip()
            elif step.get("x") is not None and step.get("y") is not None:
                enforce_risk_guard(act, matched_label, confirm_risky)
                cx = int(round(float(step["x"])))
                cy = int(round(float(step["y"])))
                rc, _env, out, err = run_agent_desktop(
                    ["mouse-click", f"--xy={cx},{cy}"],
                    session=session,
                    agent_id=agent_id,
                    headed=True,
                    timeout=15.0,
                )
                if rc != 0:
                    raise RuntimeError(f"mouse-click failed: {err.strip() or out.strip()}")
                return {"action": act, "x": step["x"], "y": step["y"], "ok": True}
            else:
                raise ValueError(f"Step '{act}' requires 'ref', 'find'/'name'/'role', or ('x', 'y').")

        enforce_risk_guard("click", matched_label, confirm_risky)
        rc, _env, out, err = run_agent_desktop(
            ["click", resolved_ref],
            session=session,
            agent_id=agent_id,
            headed=bool(step.get("headed", False)),
            timeout=15.0,
        )
        if rc != 0:
            raise RuntimeError(f"click {resolved_ref} failed: {err.strip() or out.strip()}")
        return {"action": act, "ref": resolved_ref, "ok": True}

    if act in ("set", "set-value"):
        text_val = str(step.get("text") if step.get("text") is not None else (step.get("value") or ""))
        ref_val = step.get("ref")
        resolved_ref = None
        if isinstance(ref_val, str) and ref_val.strip():
            resolved_ref, _ = resolve_ref_and_label(
                ref_val.strip(), fetch_label=False, session=session, agent_id=agent_id
            )
        else:
            find_obj = step.get("find") if isinstance(step.get("find"), dict) else {}
            fname = find_obj.get("name") or step.get("name") or step.get("target")
            frole = find_obj.get("role") or step.get("role")
            fapp = find_obj.get("app") or step_app
            resolved_ref, _m, ferr = _find_first_ref(
                app=str(fapp) if fapp else None,
                window_id=str(step_wid) if step_wid else None,
                name=str(fname) if fname else None,
                role=str(frole) if frole else None,
                session=session,
                agent_id=agent_id,
            )
            if not resolved_ref:
                raise RuntimeError(f"Could not resolve target for '{act}': {ferr}")

        rc, _env, out, err = run_agent_desktop(
            ["set-value", resolved_ref, "--stdin"],
            session=session,
            agent_id=agent_id,
            input_text=text_val,
            timeout=15.0,
        )
        if rc != 0:
            raise RuntimeError(f"set-value failed: {err.strip() or out.strip()}")
        if step.get("submit") is True:
            pcmd = ["press", "return"]
            if step_app:
                pcmd.extend(["--app", str(step_app)])
            run_agent_desktop(pcmd, session=session, agent_id=agent_id, timeout=10.0)
        return {"action": act, "ref": resolved_ref, "submitted": bool(step.get("submit")), "ok": True}

    if act == "type":
        text_val = str(step.get("text") if step.get("text") is not None else (step.get("value") or ""))
        ref_val = step.get("ref")
        resolved_ref = None
        if isinstance(ref_val, str) and ref_val.strip():
            resolved_ref, _ = resolve_ref_and_label(
                ref_val.strip(), fetch_label=False, session=session, agent_id=agent_id
            )
        elif step.get("name") or isinstance(step.get("find"), dict):
            find_obj = step.get("find") if isinstance(step.get("find"), dict) else {}
            resolved_ref, _m, ferr = _find_first_ref(
                app=str(find_obj.get("app") or step_app) if (find_obj.get("app") or step_app) else None,
                window_id=str(step_wid) if step_wid else None,
                name=str(find_obj.get("name") or step.get("name")) if (find_obj.get("name") or step.get("name")) else None,
                role=str(find_obj.get("role") or step.get("role")) if (find_obj.get("role") or step.get("role")) else None,
                session=session,
                agent_id=agent_id,
            )
            if not resolved_ref:
                raise RuntimeError(f"Could not resolve target for 'type': {ferr}")
        else:
            resolved_ref, _m, _ferr = _find_first_ref(
                app=str(step_app) if step_app else None,
                surface="focused",
                session=session,
                agent_id=agent_id,
            )

        if resolved_ref:
            tcmd = ["type", resolved_ref, "--stdin"]
            if step.get("delay_ms") is not None:
                tcmd.extend(["--interval-ms", str(int(step["delay_ms"]))])
            rc, _env, out, err = run_agent_desktop(
                tcmd,
                session=session,
                agent_id=agent_id,
                input_text=text_val,
                timeout=30.0,
            )
            if rc != 0:
                raise RuntimeError(f"type failed: {err.strip() or out.strip()}")
            return {"action": act, "ref": resolved_ref, "ok": True}

        macos_ctl = find_macos_ctl()
        if macos_ctl:
            proc = run_subprocess([str(macos_ctl), "type", text_val], timeout=20.0)
            if proc.returncode == 0:
                return {"action": act, "macos_fallback": True, "ok": True}
        raise RuntimeError("Step 'type' requires a 'ref', 'name'/'find', or a focused accessible text element.")

    if act == "key":
        combo = str(step.get("combo") or step.get("key") or "").strip()
        if not combo:
            raise ValueError("Step 'key' requires 'combo' or 'key'.")
        kcmd = ["press", combo]
        if step_app:
            kcmd.extend(["--app", str(step_app)])
        if step_wid:
            kcmd.extend(["--window-id", str(step_wid)])
        rc, _env, out, err = run_agent_desktop(
            kcmd, session=session, agent_id=agent_id, timeout=15.0
        )
        if rc != 0:
            raise RuntimeError(f"key '{combo}' failed: {err.strip() or out.strip()}")
        return {"action": act, "combo": combo, "ok": True}

    if act == "wait":
        wait_for = step.get("wait_for")
        timeout_ms = int(step.get("timeout_ms") or step.get("timeout") or 30000)
        if wait_for:
            wcmd = ["snapshot", "--wait-for", str(wait_for), "--wait-timeout", str(timeout_ms)]
            if step_app:
                wcmd.extend(["--app", str(step_app)])
            if step_wid:
                wcmd.extend(["--window-id", str(step_wid)])
            rc, _env, out, err = run_agent_desktop(
                wcmd,
                session=session,
                agent_id=agent_id,
                timeout=max(10.0, timeout_ms / 1000.0 + 5.0),
            )
            if rc != 0:
                raise RuntimeError(f"wait_for '{wait_for}' failed: {err.strip() or out.strip()}")
            return {"action": act, "wait_for": wait_for, "ok": True}

        seconds = step.get("seconds")
        if seconds is None:
            seconds = step.get("duration")
        if seconds is None and step.get("ms") is not None:
            seconds = float(step["ms"]) / 1000.0
        sec = max(0.0, float(seconds if seconds is not None else 1.0))
        time.sleep(sec)
        return {"action": act, "seconds": sec, "ok": True}

    if act == "window-tile":
        app_name = str(step_app or step.get("target") or "").strip()
        pos = str(step.get("position") or step.get("layout") or "").strip().lower()
        if not app_name or pos not in TILE_POSITIONS:
            raise ValueError(f"Step 'window-tile' requires 'app' and valid 'position' ({', '.join(TILE_POSITIONS)}).")
        tile_ns = argparse.Namespace(
            app=app_name,
            position=pos,
            window_id=step_wid,
            title=step.get("title"),
            display=step.get("display"),
            session=session,
            agent_id=agent_id,
            json=True,
        )
        # Execute directly without printing extra noise
        displays = fetch_displays(session=session, agent_id=agent_id)
        display = pick_display(displays, index_one_based=tile_ns.display)
        if not display:
            raise RuntimeError("No matching display for window-tile step.")
        windows = fetch_windows(app_name, session=session, agent_id=agent_id)
        win = pick_window(windows, window_id=tile_ns.window_id, title=tile_ns.title)
        if not win:
            raise RuntimeError(f"No matching window for '{app_name}' in window-tile step.")
        wid = str(win.get("id") or "")
        tx, ty, tw, th = compute_tile_rect(display, pos)
        move_cmd = ["move-window", "--app", app_name]
        if wid:
            move_cmd.extend(["--window-id", wid])
        move_cmd.extend([f"--x={tx}", f"--y={ty}"])
        rc1, _, out1, err1 = run_agent_desktop(
            move_cmd,
            session=session,
            agent_id=agent_id,
        )
        if rc1 != 0:
            raise RuntimeError(f"window-tile move failed: {err1.strip() or out1.strip()}")
        resize_cmd = ["resize-window", "--app", app_name]
        if wid:
            resize_cmd.extend(["--window-id", wid])
        resize_cmd.extend(["--width", str(tw), "--height", str(th)])
        rc2, _, out2, err2 = run_agent_desktop(
            resize_cmd,
            session=session,
            agent_id=agent_id,
        )
        if rc2 != 0:
            raise RuntimeError(f"window-tile resize failed: {err2.strip() or out2.strip()}")
        return {"action": act, "app": app_name, "position": pos, "ok": True}

    if act == "menu":
        app_name = str(step_app or "").strip()
        raw_path = step.get("path") or step.get("items") or []
        if isinstance(raw_path, str):
            items = [raw_path]
        elif isinstance(raw_path, list):
            items = [str(x) for x in raw_path if str(x).strip()]
        else:
            items = []
        if not app_name or not items:
            raise ValueError("Step 'menu' requires 'app' and non-empty 'path' list.")

        macos_ctl = find_macos_ctl()
        if macos_ctl:
            mcmd = [str(macos_ctl)]
            if confirm_risky:
                mcmd.append("--confirm-risky")
            mcmd.extend(["menu", app_name, *items])
            proc = run_subprocess(mcmd, timeout=20.0)
            if proc.returncode != 0:
                raise RuntimeError(f"macos-ctl menu failed: {proc.stderr.strip() or proc.stdout.strip()}")
            return {"action": act, "app": app_name, "path": items, "ok": True}

        # Cross-platform agent-desktop menubar -> menu traversal
        for idx, item in enumerate(items):
            surface = "menubar" if idx == 0 else "menu"
            ref, _m, ferr = _find_first_ref(
                app=app_name,
                name=item,
                surface=surface,
                session=session,
                agent_id=agent_id,
            )
            if not ref:
                raise RuntimeError(f"Menu item '{item}' not found on surface '{surface}': {ferr}")
            rc, _env, out, err = run_agent_desktop(
                ["click", ref], session=session, agent_id=agent_id, timeout=15.0
            )
            if rc != 0:
                raise RuntimeError(f"Failed clicking menu item '{item}': {err.strip() or out.strip()}")
        return {"action": act, "app": app_name, "path": items, "ok": True}

    if act in ("quit", "close-app", "safe-close"):
        app_name = str(step_app or step.get("target") or "").strip()
        if not app_name:
            raise ValueError(f"Step '{act}' requires 'app'.")
        force = bool(step.get("force", False))
        if force:
            enforce_risk_guard("close-app --force", app_name, confirm_risky)
        cmd = ["close-app", app_name] + (["--force"] if force else [])
        rc, _env, out, err = run_agent_desktop(cmd, session=session, agent_id=agent_id, timeout=15.0)
        if rc != 0:
            raise RuntimeError(f"close-app '{app_name}' failed: {err.strip() or out.strip()}")
        return {"action": act, "app": app_name, "force": force, "ok": True}

    raise ValueError(f"Unsupported recipe step action: '{act}'")


def cmd_recipe_list(args: argparse.Namespace) -> int:
    seen: Dict[str, Dict[str, Any]] = {}
    search_dirs: List[Tuple[str, Path]] = [("pilot", get_recipes_dir())]
    legacy = get_legacy_macos_recipes_dir()
    if legacy and legacy != search_dirs[0][1]:
        search_dirs.append(("macos-legacy", legacy))

    for source, directory in search_dirs:
        if not directory.is_dir():
            continue
        for fp in sorted(directory.glob("*.json")):
            name = fp.stem
            if name in seen:
                continue
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    continue
                steps = data.get("steps") if isinstance(data.get("steps"), list) else []
                params = list(
                    dict.fromkeys(
                        list(data.get("params") or []) + collect_placeholders(steps)
                    )
                )
                seen[name] = {
                    "name": name,
                    "description": str(data.get("description") or ""),
                    "params": params,
                    "step_count": len(steps),
                    "path": str(fp),
                    "source": source,
                }
            except (OSError, json.JSONDecodeError):
                continue

    entries = sorted(seen.values(), key=lambda x: x["name"])
    if args.json:
        print(json.dumps(entries, ensure_ascii=False, indent=2))
    else:
        if not entries:
            print(f"No recipes found in {get_recipes_dir()}.")
        else:
            for item in entries:
                p_str = f" params=[{', '.join(item['params'])}]" if item["params"] else ""
                desc = f" — {item['description']}" if item["description"] else ""
                print(f"  {item['name']} ({item['step_count']} steps){p_str}{desc}")
    return 0


def cmd_recipe_show(args: argparse.Namespace) -> int:
    name = validate_recipe_name(args.name)
    fp = locate_recipe_file(name)
    if not fp:
        fail_json_or_text(f"Recipe '{name}' not found.", code="RECIPE_NOT_FOUND")
    print(fp.read_text(encoding="utf-8").rstrip())
    return 0


def cmd_recipe_save(args: argparse.Namespace) -> int:
    name = validate_recipe_name(args.name)
    if args.stdin:
        raw = sys.stdin.read()
    elif args.file:
        src = Path(args.file).expanduser()
        if not src.is_file():
            fail_json_or_text(f"Input file not found: {src}", code="FILE_NOT_FOUND")
        raw = src.read_text(encoding="utf-8")
    else:
        fail_json_or_text("Specify either <file.json> or --stdin.", code="INVALID_ARGS")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail_json_or_text(f"Invalid recipe JSON: {exc}", code="INVALID_JSON")

    if not isinstance(data, dict) or not isinstance(data.get("steps"), list):
        fail_json_or_text(
            "Recipe JSON must be an object containing a 'steps' list.",
            code="INVALID_RECIPE_SCHEMA",
        )
    if not all(isinstance(s, dict) for s in data["steps"]):
        fail_json_or_text("Every element in 'steps' must be a JSON object.", code="INVALID_RECIPE_SCHEMA")

    data.setdefault("name", name)
    dest_dir = get_recipes_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{name}.json"
    dest.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved recipe '{name}' ({len(data['steps'])} steps) to {dest}")
    return 0


def cmd_recipe_delete(args: argparse.Namespace) -> int:
    name = validate_recipe_name(args.name)
    dest = get_recipes_dir() / f"{name}.json"
    if not dest.is_file():
        fail_json_or_text(f"Recipe '{name}' not found in {get_recipes_dir()}.", code="RECIPE_NOT_FOUND")
    dest.unlink()
    print(f"Deleted recipe '{name}' ({dest})")
    return 0


def cmd_recipe_run(args: argparse.Namespace, confirm_risky: bool) -> int:
    name = validate_recipe_name(args.name, as_json=args.json)
    fp = locate_recipe_file(name)
    if not fp:
        fail_json_or_text(f"Recipe '{name}' not found.", code="RECIPE_NOT_FOUND", as_json=args.json)

    try:
        recipe = json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail_json_or_text(f"Failed to read recipe '{name}': {exc}", code="INVALID_JSON", as_json=args.json)

    steps = recipe.get("steps")
    if not isinstance(steps, list):
        fail_json_or_text(f"Recipe '{name}' has no valid 'steps' list.", code="INVALID_RECIPE_SCHEMA", as_json=args.json)

    param_map: Dict[str, str] = {}
    for pair in args.param or []:
        if "=" not in pair:
            fail_json_or_text(
                f"Invalid --param '{pair}': expected key=val format.",
                code="INVALID_PARAM",
                as_json=args.json,
            )
        k, v = pair.split("=", 1)
        param_map[k.strip()] = v

    required_keys = collect_placeholders(steps)
    missing = sorted(k for k in required_keys if k not in param_map)
    if missing:
        fail_json_or_text(
            f"Missing required recipe parameter(s): {', '.join(missing)}. Pass via --param key=val.",
            code="MISSING_PARAMS",
            as_json=args.json,
            extra={"missing_params": missing},
        )

    expanded_steps: List[Dict[str, Any]] = [substitute_placeholders(s, param_map) for s in steps]

    if args.dry_run:
        dry_items = []
        for idx, step in enumerate(expanded_steps, start=1):
            risk = assess_step_risk(step)
            would_block = bool(risk and not confirm_risky)
            dry_items.append(
                {
                    "index": idx,
                    "action": _step_action(step),
                    "description": describe_step(step),
                    "step": step,
                    "risk_guard": risk,
                    "would_block": would_block,
                }
            )
        if args.json:
            print(
                json.dumps(
                    {
                        "ok": True,
                        "dry_run": True,
                        "recipe": name,
                        "step_count": len(expanded_steps),
                        "steps": dry_items,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(f"[DRY-RUN] Recipe '{name}' ({len(expanded_steps)} steps):")
            for item in dry_items:
                print(f"  [{item['index']}/{len(expanded_steps)}] {item['description']}")
                risk = item["risk_guard"]
                if risk:
                    if confirm_risky:
                        print(
                            f"      [RISK-GUARD ALLOWED via --confirm-risky: matched \"{risk['matched_keyword']}\"]"
                        )
                    else:
                        print(
                            f"      [RISK-GUARD WOULD BLOCK: matched \"{risk['matched_keyword']}\" in \"{risk['target']}\"]"
                        )
        return 0

    results: List[Dict[str, Any]] = []
    if not args.json:
        print(f"Running recipe '{name}' ({len(expanded_steps)} steps)...")

    for idx, step in enumerate(expanded_steps, start=1):
        if not args.json:
            print(f"[{idx}/{len(expanded_steps)}] {describe_step(step)}")
        risk = assess_step_risk(step)
        if risk and not confirm_risky:
            print(json.dumps(risk, ensure_ascii=False, indent=2))
            raise PilotExit(2, risk_blocked=True)
        try:
            step_res = execute_recipe_step(
                step,
                confirm_risky=confirm_risky,
                session=args.session,
                agent_id=args.agent_id,
            )
            results.append({"index": idx, **step_res})
        except PilotExit:
            raise
        except Exception as exc:
            fail_json_or_text(
                f"Recipe '{name}' failed at step {idx}: {exc}",
                code="RECIPE_STEP_FAILED",
                as_json=args.json,
                extra={"step_index": idx, "completed_steps": results},
            )

    if args.json:
        print(
            json.dumps(
                {
                    "ok": True,
                    "recipe": name,
                    "step_count": len(expanded_steps),
                    "results": results,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"Recipe '{name}' completed ({len(expanded_steps)} steps).")
    return 0


# ---------------------------------------------------------------------------
# 6. Cross-platform notify + macOS accelerators (record, shortcut)
# ---------------------------------------------------------------------------


def _applescript_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\r", " ").replace("\n", " ")


def cmd_notify(args: argparse.Namespace) -> int:
    title = str(args.title)
    message = str(args.message)
    backend = "stdout"

    if sys.platform == "darwin":
        script = f'display notification "{_applescript_escape(message)}" with title "{_applescript_escape(title)}"'
        proc = run_subprocess(["osascript", "-e", script], timeout=10.0)
        if proc.returncode != 0:
            fail_json_or_text(
                f"osascript notify failed: {proc.stderr.strip() or proc.stdout.strip()}",
                code="NOTIFY_FAILED",
                as_json=args.json,
            )
        backend = "osascript"

    elif _is_windows():
        ps_script = (
            "$t = $env:AG_PILOT_NOTIFY_TITLE; $m = $env:AG_PILOT_NOTIFY_MSG; "
            "try { "
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
            "$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
            "$nodes = $xml.GetElementsByTagName('text'); "
            "$nodes.Item(0).AppendChild($xml.CreateTextNode($t)) | Out-Null; "
            "$nodes.Item(1).AppendChild($xml.CreateTextNode($m)) | Out-Null; "
            "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml); "
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('antigravity-pilot').Show($toast); "
            "} catch { "
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$n = New-Object System.Windows.Forms.NotifyIcon; "
            "$n.Icon = [System.Drawing.SystemIcons]::Information; "
            "$n.Visible = $true; "
            "$n.ShowBalloonTip(4000, $t, $m, [System.Windows.Forms.ToolTipIcon]::Info); "
            "Start-Sleep -Milliseconds 300; $n.Dispose(); "
            "}"
        )
        env = dict(os.environ)
        env["AG_PILOT_NOTIFY_TITLE"] = title
        env["AG_PILOT_NOTIFY_MSG"] = message
        ps = shutil.which("powershell") or shutil.which("pwsh")
        if ps:
            proc = run_subprocess([ps, "-NoProfile", "-Command", ps_script], timeout=15.0, env=env)
            backend = "powershell" if proc.returncode == 0 else "stdout"
        else:
            backend = "stdout"

    else:
        notify_send = shutil.which("notify-send")
        if notify_send:
            proc = run_subprocess([notify_send, title, message], timeout=10.0)
            backend = "notify-send" if proc.returncode == 0 else "stdout"
        else:
            backend = "stdout"

    payload = {"ok": True, "backend": backend, "title": title, "message": message}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        if backend == "stdout":
            print(f"[notify] {title}: {message}")
        else:
            print(f"Notification sent via {backend}: {title}")
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    if sys.platform != "darwin":
        print(
            json.dumps(
                {
                    "ok": False,
                    "code": "UNSUPPORTED_PLATFORM",
                    "platform": sys.platform,
                    "command": "record",
                    "message": (
                        "Live input recording is currently supported on macOS only. "
                        "Use 'recipe save' and 'recipe run', which work identically on Windows, macOS, and Linux."
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise PilotExit(1)

    macos_ctl = find_macos_ctl()
    if not macos_ctl:
        print(
            json.dumps(
                {
                    "ok": False,
                    "code": "MACOS_ACCELERATOR_MISSING",
                    "message": "bin/macos-ctl not found. Build it with ./install.sh (or ./scripts/install.sh).",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise PilotExit(1)

    cmd = [str(macos_ctl), "record"]
    if args.duration is not None:
        cmd.extend(["--duration", str(args.duration)])
    if args.output:
        cmd.extend(["--output", args.output])
    if args.app:
        cmd.extend(["--app", args.app])

    # Run foreground so user can press Ctrl-C to stop recording cleanly
    proc = subprocess.run(cmd)
    return proc.returncode


def cmd_shortcut(args: argparse.Namespace) -> int:
    if sys.platform != "darwin":
        print(
            json.dumps(
                {
                    "ok": False,
                    "code": "UNSUPPORTED_PLATFORM",
                    "platform": sys.platform,
                    "command": "shortcut",
                    "message": "Apple Shortcuts is only available on macOS.",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise PilotExit(1)

    macos_ctl = find_macos_ctl()
    if macos_ctl:
        cmd = [str(macos_ctl), "shortcut", args.name]
        if args.input is not None:
            cmd.extend(["--input", args.input])
        proc = run_subprocess(cmd, timeout=60.0)
        if proc.stdout:
            sys.stdout.write(proc.stdout)
        if proc.stderr:
            sys.stderr.write(proc.stderr)
        return proc.returncode

    shortcuts_bin = shutil.which("shortcuts") or ("/usr/bin/shortcuts" if Path("/usr/bin/shortcuts").is_file() else None)
    if not shortcuts_bin:
        fail_json_or_text("macOS 'shortcuts' CLI not found.", code="SHORTCUTS_NOT_FOUND")

    cmd = [shortcuts_bin, "run", args.name]
    tmp_input: Optional[Path] = None
    try:
        if args.input is not None:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as tf:
                tf.write(args.input)
                tmp_input = Path(tf.name)
            cmd.extend(["--input-path", str(tmp_input)])
        proc = run_subprocess(cmd, timeout=60.0)
        if proc.stdout:
            sys.stdout.write(proc.stdout)
        if proc.stderr:
            sys.stderr.write(proc.stderr)
        return proc.returncode
    finally:
        if tmp_input and tmp_input.exists():
            try:
                tmp_input.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Argument Parsing (global flags allowed before OR after subcommands)
# ---------------------------------------------------------------------------


def extract_global_flags(argv: Sequence[str]) -> Tuple[List[str], bool, Optional[str]]:
    """Extract --confirm-risky and --trace <file> from anywhere in argv."""
    remaining: List[str] = []
    confirm_risky = False
    trace_path: Optional[str] = os.environ.get("PILOT_CTL_TRACE", "").strip() or None

    i = 0
    n = len(argv)
    while i < n:
        tok = argv[i]
        if tok == "--":
            remaining.extend(argv[i:])
            break
        if tok == "--confirm-risky":
            confirm_risky = True
            i += 1
            continue
        if tok == "--trace":
            if i + 1 >= n:
                raise SystemExit("error: argument --trace: expected one argument")
            trace_path = argv[i + 1]
            i += 2
            continue
        if tok.startswith("--trace="):
            trace_path = tok.split("=", 1)[1]
            i += 1
            continue
        remaining.append(tok)
        i += 1

    return remaining, confirm_risky, trace_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pilot_ctl.py",
        description=(
            "Cross-platform desktop automation complement CLI (Windows, macOS, Linux) "
            "built on agent-desktop + zero-dependency PNG overlays."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Global options (accepted before OR after any subcommand):\n"
            "  --confirm-risky           Allow Risk-Guard blocked actions after explicit user approval\n"
            "  --trace <file.jsonl>      Append JSONL audit trace entry (or set PILOT_CTL_TRACE)\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"pilot_ctl.py {VERSION}")
    parser.add_argument(
        "--confirm-risky",
        action="store_true",
        default=False,
        help="Allow Risk-Guard blocked actions (only after explicit user approval).",
    )
    parser.add_argument(
        "--trace",
        metavar="FILE.JSONL",
        default=None,
        help="Append compact JSONL audit trace (or set PILOT_CTL_TRACE).",
    )

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    # doctor
    p_doc = sub.add_parser("doctor", help="Check platform, agent-desktop, permissions, Docker, and recipes dir.")
    p_doc.add_argument("--json", action="store_true", help="Output valid JSON.")

    # screenshot
    p_shot = sub.add_parser(
        "screenshot",
        help="Capture PNG screenshot with optional Set-of-Mark (--annotate) and/or 100pt --grid overlay.",
    )
    p_shot.add_argument("output", metavar="out.png", help="Destination PNG file path.")
    p_shot.add_argument("--app", help="Target application name.")
    p_shot.add_argument("--window-id", help="Scope to a specific window ID.")
    p_shot.add_argument("--screen", type=int, default=None, help="Display index (0-based) for fullscreen capture.")
    p_shot.add_argument("--annotate", action="store_true", help="Overlay Set-of-Mark bounding boxes and refs.")
    p_shot.add_argument("--grid", action="store_true", help="Overlay 100pt logical coordinate grid.")
    p_shot.add_argument("--max-depth", type=int, default=None, help="Maximum AX snapshot depth.")
    p_shot.add_argument("--limit", type=int, default=120, help="Max interactive elements to annotate (default 120).")
    p_shot.add_argument("--session", help="agent-desktop session namespace.")
    p_shot.add_argument("--agent-id", help="agent-desktop subagent ID.")
    p_shot.add_argument("--json", action="store_true", help="Output structured JSON.")

    # window-tile
    p_tile = sub.add_parser("window-tile", help="Tile an application window to a display region.")
    p_tile.add_argument("app", help="Application name.")
    p_tile.add_argument("position", choices=TILE_POSITIONS, help="Target tile layout.")
    p_tile.add_argument("--window-id", help="Specific window ID.")
    p_tile.add_argument("--title", help="Case-insensitive window title substring.")
    p_tile.add_argument("--display", type=int, default=None, help="1-based display index (default primary).")
    p_tile.add_argument("--session", help="agent-desktop session namespace.")
    p_tile.add_argument("--agent-id", help="agent-desktop subagent ID.")
    p_tile.add_argument("--json", action="store_true", help="Output structured JSON.")

    # guard-check
    p_gc = sub.add_parser("guard-check", help="Evaluate deterministic Risk-Guard for an action and target.")
    p_gc.add_argument("action", help="Action name (e.g. press, click, quit --force).")
    p_gc.add_argument("target", help="Target element label or app name.")
    p_gc.add_argument("--json", action="store_true", help="Output structured JSON.")

    # safe-click
    p_sc = sub.add_parser("safe-click", help="Click a ref after deterministic Risk-Guard verification.")
    p_sc.add_argument("ref", help="Element ref (@s...:eN or cached short label e1).")
    p_sc.add_argument("--label", help="Explicit target label for Risk-Guard check.")
    p_sc.add_argument("--app", help="Optional application context.")
    p_sc.add_argument("--session", help="agent-desktop session namespace.")
    p_sc.add_argument("--agent-id", help="agent-desktop subagent ID.")
    p_sc.add_argument("--headed", action="store_true", help="Permit physical delivery.")
    p_sc.add_argument("--json", action="store_true", help="Output structured JSON.")

    # safe-close
    p_cl = sub.add_parser("safe-close", help="Close an app gracefully (guards --force with Risk-Guard).")
    p_cl.add_argument("app", help="Application name.")
    p_cl.add_argument("--force", action="store_true", help="Force terminate (blocked without --confirm-risky).")
    p_cl.add_argument("--session", help="agent-desktop session namespace.")
    p_cl.add_argument("--agent-id", help="agent-desktop subagent ID.")
    p_cl.add_argument("--json", action="store_true", help="Output structured JSON.")

    # recipe
    p_rec = sub.add_parser("recipe", help="Manage and run parameterized cross-platform recipes.")
    rsub = p_rec.add_subparsers(dest="recipe_cmd", metavar="<subcommand>")

    pr_list = rsub.add_parser("list", help="List available recipes.")
    pr_list.add_argument("--json", action="store_true", help="Output JSON array.")

    pr_show = rsub.add_parser("show", help="Show a recipe's JSON definition.")
    pr_show.add_argument("name", help="Recipe name.")

    pr_save = rsub.add_parser("save", help="Validate and save a recipe JSON.")
    pr_save.add_argument("name", help="Recipe name.")
    pr_save.add_argument("file", nargs="?", default=None, help="Path to recipe JSON file.")
    pr_save.add_argument("--stdin", action="store_true", help="Read recipe JSON from stdin.")

    pr_del = rsub.add_parser("delete", help="Delete a saved recipe.")
    pr_del.add_argument("name", help="Recipe name.")

    pr_run = rsub.add_parser("run", help="Execute (or dry-run) a parameterized recipe.")
    pr_run.add_argument("name", help="Recipe name.")
    pr_run.add_argument("--param", action="append", default=[], metavar="KEY=VAL", help="Substitute {{KEY}} parameter.")
    pr_run.add_argument("--dry-run", action="store_true", help="Print expanded steps and Risk-Guard flags without running.")
    pr_run.add_argument("--session", help="agent-desktop session namespace.")
    pr_run.add_argument("--agent-id", help="agent-desktop subagent ID.")
    pr_run.add_argument("--json", action="store_true", help="Output structured JSON.")

    # notify
    p_not = sub.add_parser("notify", help="Send a native desktop notification (cross-platform).")
    p_not.add_argument("title", help="Notification title.")
    p_not.add_argument("message", help="Notification body message.")
    p_not.add_argument("--json", action="store_true", help="Output structured JSON.")

    # record
    p_record = sub.add_parser("record", help="Record UI actions into a recipe (macOS accelerator).")
    p_record.add_argument("--duration", type=int, default=None, help="Recording duration in seconds.")
    p_record.add_argument("--output", default=None, help="Output JSON file path.")
    p_record.add_argument("--app", default=None, help="Filter recording to application name.")

    # shortcut
    p_short = sub.add_parser("shortcut", help="Run an Apple Shortcut by name (macOS).")
    p_short.add_argument("name", help="Shortcut name.")
    p_short.add_argument("--input", default=None, help="Optional input text.")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    start_ts = time.perf_counter()

    # Keep --help intact while allowing global flags anywhere
    try:
        cleaned_argv, confirm_risky, trace_path = extract_global_flags(raw_argv)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2

    parser = build_parser()
    if not cleaned_argv:
        parser.print_help()
        return 0

    exit_code = 0
    risk_blocked = False
    traced_cmd = cleaned_argv[0] if cleaned_argv else ""
    traced_args = cleaned_argv[1:] if len(cleaned_argv) > 1 else []
    should_trace = bool(trace_path) and not any(tok in ("-h", "--help", "--version") for tok in cleaned_argv)

    try:
        args = parser.parse_args(cleaned_argv)
        confirm_risky = confirm_risky or bool(getattr(args, "confirm_risky", False))
        cmd = args.command
        traced_cmd = cmd or traced_cmd

        if cmd == "doctor":
            exit_code = cmd_doctor(args)
        elif cmd == "screenshot":
            exit_code = cmd_screenshot(args)
        elif cmd == "window-tile":
            exit_code = cmd_window_tile(args)
        elif cmd == "guard-check":
            exit_code = cmd_guard_check(args, confirm_risky=confirm_risky)
        elif cmd == "safe-click":
            exit_code = cmd_safe_click(args, confirm_risky=confirm_risky)
        elif cmd == "safe-close":
            exit_code = cmd_safe_close(args, confirm_risky=confirm_risky)
        elif cmd == "recipe":
            rcmd = args.recipe_cmd
            if not rcmd:
                parser.parse_args(["recipe", "--help"])
                exit_code = 0
            else:
                traced_cmd = f"recipe {rcmd}"
                if rcmd == "list":
                    exit_code = cmd_recipe_list(args)
                elif rcmd == "show":
                    exit_code = cmd_recipe_show(args)
                elif rcmd == "save":
                    exit_code = cmd_recipe_save(args)
                elif rcmd == "delete":
                    exit_code = cmd_recipe_delete(args)
                elif rcmd == "run":
                    exit_code = cmd_recipe_run(args, confirm_risky=confirm_risky)
                else:
                    exit_code = 2
        elif cmd == "notify":
            exit_code = cmd_notify(args)
        elif cmd == "record":
            exit_code = cmd_record(args)
        elif cmd == "shortcut":
            exit_code = cmd_shortcut(args)
        else:
            parser.print_help()
            exit_code = 0

    except PilotExit as exc:
        exit_code = exc.code
        risk_blocked = exc.risk_blocked
    except SystemExit as exc:
        code = exc.code
        exit_code = int(code) if isinstance(code, int) else (1 if code else 0)
    finally:
        if should_trace:
            duration_ms = (time.perf_counter() - start_ts) * 1000.0
            append_trace(
                trace_path,
                command=traced_cmd,
                args=traced_args,
                risk_blocked=risk_blocked,
                exit_code=exit_code,
                duration_ms=duration_ms,
            )

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
