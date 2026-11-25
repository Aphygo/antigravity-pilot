#!/usr/bin/env python3
"""Cross-platform virtual desktop sandbox manager for antigravity-pilot.

Manages isolated Linux virtual desktop Docker containers (ag-desk-*) with
optional live noVNC browser takeover. Uses only the Python 3.9+ standard
library so it works identically on Windows, macOS, and Linux.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

DEFAULT_IMAGE = os.environ.get("AG_SANDBOX_IMAGE", "antigravity-desktop-sandbox:latest")
CONTAINER_PREFIX = "ag-desk-"
SANDBOX_DIR = Path(__file__).resolve().parent.parent / "sandbox"

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
DEFAULT_VNC_PORT_START = 6080
DEFAULT_VNC_PORT_END = 6180
CONTAINER_VNC_PORT = 6080
AGENT_DESKTOP_RUN = "/usr/local/bin/agent-desktop-run"


def eprint(message: str) -> None:
    print(message, file=sys.stderr)


def novnc_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/vnc.html?autoconnect=1&resize=scale"


def validate_name(name: str) -> str:
    if not name or not NAME_RE.fullmatch(name):
        eprint(
            f"sandbox-ctl: invalid sandbox name '{name}'. "
            "Must match ^[a-z0-9][a-z0-9_-]{0,47}$ (lowercase letters, digits, '_' or '-', 1-48 chars)."
        )
        raise SystemExit(2)
    return name


def validate_port(raw_port: object) -> int:
    try:
        port = int(str(raw_port))
    except (TypeError, ValueError):
        eprint(f"sandbox-ctl: invalid port '{raw_port}' (expected integer 1-65535).")
        raise SystemExit(2)
    if port < 1 or port > 65535:
        eprint(f"sandbox-ctl: invalid port '{port}' (expected 1-65535).")
        raise SystemExit(2)
    return port


def container_name(name: str) -> str:
    return f"{CONTAINER_PREFIX}{name}"


def run_docker(
    args: Sequence[str],
    *,
    capture: bool = True,
    check: bool = False,
    timeout: Optional[int] = None,
) -> subprocess.CompletedProcess[str]:
    cmd = ["docker", *args]
    try:
        if capture:
            return subprocess.run(
                cmd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=check,
                timeout=timeout,
            )
        return subprocess.run(cmd, check=check, timeout=timeout)
    except FileNotFoundError:
        eprint(
            "sandbox-ctl: 'docker' CLI not found on PATH.\n"
            "Install Docker Desktop (Windows/macOS), Colima (macOS), or Docker Engine (Linux)."
        )
        raise SystemExit(1)
    except subprocess.TimeoutExpired:
        eprint(
            "sandbox-ctl: timed out waiting for the Docker daemon.\n"
            "Check that Docker Desktop, Colima, or Docker Engine is running and healthy."
        )
        raise SystemExit(1)


def ensure_docker() -> None:
    if shutil.which("docker") is None:
        eprint(
            "sandbox-ctl: 'docker' CLI not found on PATH.\n"
            "Install Docker Desktop (Windows/macOS), Colima (macOS), or Docker Engine (Linux)."
        )
        raise SystemExit(1)
    res = run_docker(["info", "--format", "{{.ServerVersion}}"], capture=True, timeout=10)
    if res.returncode != 0:
        detail = (res.stderr or res.stdout or "").strip()
        msg = (
            "sandbox-ctl: Docker daemon is not responding.\n"
            "Start Docker Desktop (Windows/macOS), Colima ('colima start' on macOS), "
            "or the Docker Engine service (Linux) and try again."
        )
        if detail:
            msg += f"\nDocker error: {detail}"
        eprint(msg)
        raise SystemExit(1)


def is_port_free(port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def used_sandbox_vnc_ports() -> Set[int]:
    res = run_docker(
        [
            "ps",
            "--filter",
            "label=antigravity.sandbox.vnc_port",
            "--format",
            '{{.Label "antigravity.sandbox.vnc_port"}}',
        ]
    )
    ports: Set[int] = set()
    if res.returncode == 0:
        for line in res.stdout.splitlines():
            val = line.strip()
            if val.isdigit():
                ports.add(int(val))
    return ports


def find_free_vnc_port(
    start: int = DEFAULT_VNC_PORT_START,
    end: int = DEFAULT_VNC_PORT_END,
) -> int:
    claimed = used_sandbox_vnc_ports()
    for port in range(start, end + 1):
        if port not in claimed and is_port_free(port):
            return port
    eprint(f"sandbox-ctl: no free loopback port found in range {start}-{end}.")
    raise SystemExit(1)


def inspect_container(cname: str) -> Optional[Dict[str, object]]:
    res = run_docker(["inspect", "--format", "{{json .}}", cname])
    if res.returncode != 0 or not res.stdout.strip():
        return None
    try:
        data = json.loads(res.stdout)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def get_vnc_port_from_inspect(info: Dict[str, object]) -> Optional[int]:
    config = info.get("Config")
    if not isinstance(config, dict):
        return None
    labels = config.get("Labels")
    if not isinstance(labels, dict):
        return None
    raw = labels.get("antigravity.sandbox.vnc_port")
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


def is_container_running(info: Dict[str, object]) -> bool:
    state = info.get("State")
    return isinstance(state, dict) and bool(state.get("Running"))


def cmd_build(args: argparse.Namespace) -> int:
    ensure_docker()
    if not (SANDBOX_DIR / "Dockerfile").is_file():
        eprint(f"sandbox-ctl: Dockerfile not found in {SANDBOX_DIR}")
        return 1
    docker_args = ["build"]
    if args.with_chromium:
        docker_args.extend(["--build-arg", "INSTALL_CHROMIUM=1"])
    docker_args.extend(["-t", DEFAULT_IMAGE, str(SANDBOX_DIR)])
    res = run_docker(docker_args, capture=False)
    return res.returncode


def cmd_up(args: argparse.Namespace) -> int:
    name = validate_name(args.name)
    enable_vnc = bool(args.vnc or args.vnc_port is not None)
    requested_port = validate_port(args.vnc_port) if args.vnc_port is not None else None

    ensure_docker()
    cname = container_name(name)
    existing = inspect_container(cname)

    if existing is not None:
        if is_container_running(existing):
            existing_port = get_vnc_port_from_inspect(existing)
            if enable_vnc and existing_port is None:
                eprint(
                    f"sandbox-ctl: {cname} is already running without --vnc. "
                    f"Run 'sandbox-ctl down {name}' first to recreate it with --vnc."
                )
                return 1
            print(f"{cname} (already running)")
            if existing_port is not None:
                print(f"noVNC live view: {novnc_url(existing_port)}")
            return 0
        rm_res = run_docker(["rm", "-f", cname])
        if rm_res.returncode != 0:
            eprint(f"sandbox-ctl: failed to remove stopped container {cname}: {rm_res.stderr.strip()}")
            return rm_res.returncode

    docker_args = ["run", "-d", "--name", cname]
    vnc_port: Optional[int] = None

    if enable_vnc:
        if requested_port is not None:
            if not is_port_free(requested_port):
                eprint(f"sandbox-ctl: local loopback port 127.0.0.1:{requested_port} is already in use.")
                return 1
            vnc_port = requested_port
        else:
            vnc_port = find_free_vnc_port()

        docker_args.extend(
            [
                "-p",
                f"127.0.0.1:{vnc_port}:{CONTAINER_VNC_PORT}",
                "-e",
                "ENABLE_VNC=1",
                "--label",
                "antigravity.sandbox=true",
                "--label",
                f"antigravity.sandbox.name={name}",
                "--label",
                f"antigravity.sandbox.vnc_port={vnc_port}",
            ]
        )
    else:
        docker_args.extend(
            [
                "--label",
                "antigravity.sandbox=true",
                "--label",
                f"antigravity.sandbox.name={name}",
            ]
        )

    docker_args.extend(["--shm-size=256m", DEFAULT_IMAGE])
    res = run_docker(docker_args)
    if res.returncode != 0:
        err = res.stderr.strip()
        eprint(f"sandbox-ctl: failed to start {cname}: {err}")
        if "Unable to find image" in err or "No such image" in err or "pull access denied" in err:
            eprint("Hint: build the local image first with 'sandbox-ctl build'.")
        return res.returncode

    if enable_vnc and vnc_port is not None:
        subprocess.run(
            ["docker", "exec", cname, AGENT_DESKTOP_RUN, "status"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        print(cname)
        print(f"noVNC live view: {novnc_url(vnc_port)}")
    else:
        print(cname)

    return 0


def cmd_exec_raw(raw_args: Sequence[str]) -> int:
    if not raw_args or raw_args[0] in ("-h", "--help"):
        print(
            "Usage: sandbox-ctl exec <name> <agent-desktop-args...>\n\n"
            "Run agent-desktop inside a running virtual desktop sandbox (ag-desk-<name>).\n"
            "All arguments after <name> are forwarded verbatim to /usr/local/bin/agent-desktop-run."
        )
        return 0 if raw_args else 2

    name = validate_name(raw_args[0])
    forwarded = list(raw_args[1:])
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]
    if not forwarded:
        eprint("sandbox-ctl: exec requires agent-desktop arguments (e.g. 'sandbox-ctl exec dev status').")
        return 2

    ensure_docker()
    cname = container_name(name)
    docker_cmd = ["docker", "exec", "-i", cname, AGENT_DESKTOP_RUN, *forwarded]
    res = subprocess.run(docker_cmd, check=False)
    return res.returncode


def cmd_url(args: argparse.Namespace) -> int:
    name = validate_name(args.name)
    ensure_docker()
    cname = container_name(name)
    info = inspect_container(cname)
    if info is None or not is_container_running(info):
        eprint(f"sandbox-ctl: {cname} is not running.")
        return 1
    vnc_port = get_vnc_port_from_inspect(info)
    if vnc_port is None:
        eprint(f"sandbox-ctl: {cname} was started without --vnc.")
        return 1
    print(novnc_url(vnc_port))
    return 0


def list_sandbox_rows() -> List[Dict[str, object]]:
    fmt = '{{.Names}}\t{{.Status}}\t{{.Image}}\t{{.RunningFor}}\t{{.Label "antigravity.sandbox.vnc_port"}}'
    res = run_docker(
        [
            "ps",
            "-a",
            "--filter",
            f"name=^/{CONTAINER_PREFIX}",
            "--format",
            fmt,
        ]
    )
    if res.returncode != 0:
        eprint(f"sandbox-ctl: failed to list sandboxes: {res.stderr.strip()}")
        raise SystemExit(res.returncode)

    entries: List[Dict[str, object]] = []
    for line in res.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        while len(parts) < 5:
            parts.append("")
        c_name, c_status, c_image, c_created, c_port_raw = parts[:5]
        if not c_name.startswith(CONTAINER_PREFIX):
            continue
        short_name = c_name[len(CONTAINER_PREFIX) :]
        vnc_port: Optional[int] = int(c_port_raw) if c_port_raw.strip().isdigit() else None
        entries.append(
            {
                "name": short_name,
                "container": c_name,
                "status": c_status,
                "image": c_image,
                "created": c_created,
                "vnc_port": vnc_port,
                "novnc_url": novnc_url(vnc_port) if vnc_port is not None else None,
            }
        )
    return entries


def format_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()]
    for row in rows:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
    return "\n".join(lines)


def cmd_list(args: argparse.Namespace) -> int:
    ensure_docker()
    entries = list_sandbox_rows()
    if args.json:
        print(json.dumps(entries, indent=2))
        return 0
    headers = ["NAMES", "STATUS", "IMAGE", "CREATED", "NOVNC"]
    table_rows: List[Tuple[str, str, str, str, str]] = []
    for item in entries:
        url_val = item["novnc_url"]
        table_rows.append(
            (
                str(item["container"]),
                str(item["status"]),
                str(item["image"]),
                str(item["created"]),
                str(url_val) if url_val else "-",
            )
        )
    print(format_table(headers, table_rows))
    return 0


def cmd_down(args: argparse.Namespace) -> int:
    name = validate_name(args.name)
    ensure_docker()
    cname = container_name(name)
    if inspect_container(cname) is None:
        eprint(f"not found: {cname}")
        return 1
    res = run_docker(["rm", "-f", cname])
    if res.returncode != 0:
        eprint(f"sandbox-ctl: failed to remove {cname}: {res.stderr.strip()}")
        return res.returncode
    print(f"removed {cname}")
    return 0


def cmd_down_all(_args: argparse.Namespace) -> int:
    ensure_docker()
    res = run_docker(
        [
            "ps",
            "-a",
            "--filter",
            f"name=^/{CONTAINER_PREFIX}",
            "--format",
            "{{.Names}}",
        ]
    )
    if res.returncode != 0:
        eprint(f"sandbox-ctl: failed to query sandboxes: {res.stderr.strip()}")
        return res.returncode
    containers = [
        line.strip()
        for line in res.stdout.splitlines()
        if line.strip().startswith(CONTAINER_PREFIX)
    ]
    if not containers:
        print(f"no active {CONTAINER_PREFIX}* sandboxes")
        return 0
    rm_res = run_docker(["rm", "-f", *containers])
    if rm_res.returncode != 0:
        eprint(f"sandbox-ctl: failed to remove sandboxes: {rm_res.stderr.strip()}")
        return rm_res.returncode
    print("removed:")
    for cname in containers:
        print(cname)
    return 0


def build_parser() -> argparse.ArgumentParser:
    epilog = (
        "Commands:\n"
        "  build [--with-chromium]                Build the virtual desktop sandbox Docker image\n"
        "  up <name> [--vnc] [--vnc-port <port>]  Start an isolated desktop container (ag-desk-<name>)\n"
        "  exec <name> <agent-desktop-args...>    Run agent-desktop-run inside ag-desk-<name>\n"
        "  url <name>                             Print the noVNC live-view URL for a running sandbox\n"
        "  list [--json]                          List ag-desk-* sandboxes (table or JSON)\n"
        "  down <name>                            Stop and remove ag-desk-<name>\n"
        "  down-all                               Stop and remove all ag-desk-* sandboxes\n\n"
        "Environment:\n"
        f"  AG_SANDBOX_IMAGE  Docker image tag (default: {DEFAULT_IMAGE})\n"
    )
    parser = argparse.ArgumentParser(
        prog="sandbox-ctl",
        description="Manage isolated virtual desktop Docker sandboxes (ag-desk-*) with optional --vnc live view.",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p_build = sub.add_parser("build", help="Build the antigravity-desktop-sandbox Docker image")
    p_build.add_argument(
        "--with-chromium",
        action="store_true",
        help="Install Chromium inside the sandbox image (--build-arg INSTALL_CHROMIUM=1)",
    )
    p_build.set_defaults(handler=cmd_build)

    p_up = sub.add_parser("up", help="Start an isolated virtual desktop container (ag-desk-<name>)")
    p_up.add_argument("name", help="Sandbox name (^[a-z0-9][a-z0-9_-]{0,47}$)")
    p_up.add_argument(
        "--vnc",
        action="store_true",
        help="Expose a local loopback noVNC live view (127.0.0.1:6080-6180)",
    )
    p_up.add_argument(
        "--vnc-port",
        type=int,
        default=None,
        metavar="<port>",
        help="Bind noVNC to a specific local loopback port (implies --vnc)",
    )
    p_up.set_defaults(handler=cmd_up)

    p_exec = sub.add_parser("exec", help="Run agent-desktop-run inside ag-desk-<name>")
    p_exec.add_argument("name", help="Sandbox name")
    p_exec.add_argument("args", nargs=argparse.REMAINDER, help="Arguments passed to agent-desktop")

    p_url = sub.add_parser("url", help="Print the noVNC live-view URL for a running sandbox")
    p_url.add_argument("name", help="Sandbox name")
    p_url.set_defaults(handler=cmd_url)

    p_list = sub.add_parser("list", help="List ag-desk-* virtual desktop sandboxes")
    p_list.add_argument("--json", action="store_true", help="Output JSON array")
    p_list.set_defaults(handler=cmd_list)

    p_down = sub.add_parser("down", help="Stop and remove ag-desk-<name>")
    p_down.add_argument("name", help="Sandbox name")
    p_down.set_defaults(handler=cmd_down)

    p_down_all = sub.add_parser("down-all", help="Stop and remove all ag-desk-* sandboxes")
    p_down_all.set_defaults(handler=cmd_down_all)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    if not raw:
        parser.print_help(sys.stderr)
        return 2

    if raw[0] == "help":
        if len(raw) > 1:
            return main([raw[1], "--help"])
        parser.print_help()
        return 0

    if raw[0] == "exec":
        return cmd_exec_raw(raw[1:])

    args = parser.parse_args(raw)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return 2
    return int(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
