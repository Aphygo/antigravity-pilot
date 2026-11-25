#!/usr/bin/env bash
set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON:-python3}"
FAILURES=0

pass() {
  echo "[PASS] $1"
}

fail() {
  echo "[FAIL] $1" >&2
  FAILURES=$((FAILURES + 1))
}

# 1. Validate plugin.json: valid JSON with name == "antigravity-pilot" and version == "1.0.0"
if "$PYTHON_BIN" - "${PLUGIN_DIR}/plugin.json" <<'PY'
import json, sys
with open(sys.argv[1], "r", encoding="utf-8") as f:
    doc = json.load(f)
assert doc.get("name") == "antigravity-pilot", f"unexpected name: {doc.get('name')!r}"
assert doc.get("version") == "1.0.0", f"unexpected version: {doc.get('version')!r}"
PY
then
  pass "plugin.json is valid JSON (name=antigravity-pilot, version=1.0.0)"
else
  fail "plugin.json is invalid JSON or has unexpected name/version"
fi

# 2. Check for zero hardcoded macOS home or Homebrew paths across the entire repo (excl. .git)
USER_PATH_RE='/Users''/[^/[:space:]]+/'
BREW_PATH_RE='/opt''/homebrew'
if matches="$(grep -rnE "(${USER_PATH_RE}|${BREW_PATH_RE})" "${PLUGIN_DIR}" \
    --exclude-dir=.git --exclude-dir=__pycache__ 2>/dev/null || true)" && [[ -n "$matches" ]]; then
  fail "Hardcoded machine-specific paths found:"
  echo "$matches" >&2
else
  pass "Zero hardcoded machine-specific user or Homebrew paths in repo"
fi

# 3. Check agent-desktop --version
if command -v agent-desktop >/dev/null 2>&1 && ad_ver="$(agent-desktop --version 2>/dev/null)"; then
  pass "agent-desktop --version (${ad_ver})"
else
  fail "agent-desktop --version failed"
fi

# 4. Check bin/pilot-ctl --help and doctor --json
if "${PLUGIN_DIR}/bin/pilot-ctl" --help >/dev/null 2>&1; then
  pass "bin/pilot-ctl --help"
else
  fail "bin/pilot-ctl --help failed"
fi

if doctor_json="$("${PLUGIN_DIR}/bin/pilot-ctl" doctor --json 2>/dev/null)" && \
   printf '%s' "$doctor_json" | "$PYTHON_BIN" -c 'import json, sys; d = json.load(sys.stdin); assert isinstance(d, dict) and "platform" in d'; then
  pass "bin/pilot-ctl doctor --json returns valid JSON"
else
  fail "bin/pilot-ctl doctor --json did not return valid JSON"
fi

# 5. Check Risk-Guard blocks Swedish send action with exit code 2 + RISK_GUARD_BLOCKED
rg_output=""
rg_status=0
set +e
rg_output="$("${PLUGIN_DIR}/bin/pilot-ctl" guard-check press "Skicka mejl" 2>&1)"
rg_status=$?
set -e
if [[ "$rg_status" -eq 2 ]] && printf '%s' "$rg_output" | grep -q "RISK_GUARD_BLOCKED"; then
  pass "bin/pilot-ctl guard-check blocks 'Skicka mejl' with exit 2 and RISK_GUARD_BLOCKED"
else
  fail "bin/pilot-ctl guard-check failed (exit=${rg_status}, output=${rg_output})"
fi

# 6. Check bin/pilot-ctl recipe list --json
if recipe_json="$("${PLUGIN_DIR}/bin/pilot-ctl" recipe list --json 2>/dev/null)" && \
   printf '%s' "$recipe_json" | "$PYTHON_BIN" -c 'import json, sys; assert isinstance(json.load(sys.stdin), list)'; then
  pass "bin/pilot-ctl recipe list --json returns valid JSON array"
else
  fail "bin/pilot-ctl recipe list --json did not return valid JSON array"
fi

# 7. Check bin/sandbox-ctl --help documents --vnc, url, and build
sandbox_help="$("${PLUGIN_DIR}/bin/sandbox-ctl" --help 2>&1 || true)"
if printf '%s' "$sandbox_help" | grep -q -- "--vnc" && \
   printf '%s' "$sandbox_help" | grep -qE '(^|[[:space:]])url([[:space:]]|$)' && \
   printf '%s' "$sandbox_help" | grep -qE '(^|[[:space:]])build([[:space:]]|$)'; then
  pass "bin/sandbox-ctl --help documents --vnc, url, and build"
else
  fail "bin/sandbox-ctl --help missing --vnc, url, or build"
fi

# 8. Check that sandbox/ contains no loose binaries or tarballs
LOOSE_FILES=()
while IFS= read -r f; do
  [[ -n "$f" ]] && LOOSE_FILES+=("$f")
done < <(find "${PLUGIN_DIR}/sandbox" -maxdepth 1 -type f \( \
  -name 'agent-desktop' -o \
  -name 'agent-desktop-linux-clipboard-owner' -o \
  -name '*.tar.gz' -o \
  -name '*.tgz' -o \
  -name '*.zip' -o \
  -name 'checksums.txt' \
\) | sort)

if [[ ${#LOOSE_FILES[@]} -gt 0 ]]; then
  fail "Loose binaries/tarballs found in sandbox/: ${LOOSE_FILES[*]}"
else
  pass "sandbox/ contains no loose binaries or tarballs"
fi

# 9. Run full zero-dependency Python unit test suite
if (cd "${PLUGIN_DIR}" && "$PYTHON_BIN" -m unittest discover -s tests -v); then
  pass "python3 -m unittest discover -s tests -v (100% PASS)"
else
  fail "python3 -m unittest discover -s tests -v failed"
fi

# 10. On macOS (Darwin), verify optional bin/macos-ctl --help accelerator
if [[ "$(uname -s)" == "Darwin" ]]; then
  if "${PLUGIN_DIR}/bin/macos-ctl" --help >/dev/null 2>&1; then
    pass "bin/macos-ctl --help (macOS accelerator)"
  else
    fail "bin/macos-ctl --help failed on macOS"
  fi
fi

echo ""
if [[ "$FAILURES" -gt 0 ]]; then
  echo "Smoke test FAILED with ${FAILURES} failure(s)." >&2
  exit 1
fi

echo "All antigravity-pilot smoke tests PASSED."
