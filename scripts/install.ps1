[CmdletBinding()]
param(
    [switch]$Upgrade,
    [switch]$Help
)

$ErrorActionPreference = "Stop"
$AgentDesktopVersion = "1.3.1"
$PluginRoot = Split-Path -Parent $PSScriptRoot

function Show-Usage {
    @"
Usage: .\scripts\install.ps1 [-Upgrade] [-Help]

Installs and verifies the antigravity-pilot plugin on Windows (PowerShell 5.1 / 7+):
  1. Locates Python 3.9+ (python or py launcher)
  2. Ensures agent-desktop@$AgentDesktopVersion is installed via npm
  3. Verifies scripts\pilot_ctl.py and runs doctor checks

Options:
  -Upgrade   Force reinstall/upgrade of agent-desktop@$AgentDesktopVersion
  -Help      Show this help message
"@
}

if ($Help) {
    Show-Usage
    exit 0
}

function Find-Python39 {
    $candidates = @()
    if ($env:PYTHON) {
        $candidates += ,@($env:PYTHON)
    }
    $candidates += ,@("python")
    $candidates += ,@("py", "-3")
    $candidates += ,@("py")
    $candidates += ,@("python3")

    $probeCode = "import sys; print(sys.executable); print(f'{sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]}'); sys.exit(0 if sys.version_info >= (3, 9) else 1)"

    foreach ($cand in $candidates) {
        $exe = $cand[0]
        $extraArgs = @()
        if ($cand.Count -gt 1) {
            $extraArgs = $cand[1..($cand.Count - 1)]
        }

        $cmdInfo = Get-Command $exe -ErrorAction SilentlyContinue
        if (-not $cmdInfo) {
            continue
        }

        $prevPref = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $output = & $exe @extraArgs -c $probeCode 2>$null
            $code = $LASTEXITCODE
        } catch {
            $output = $null
            $code = 1
        } finally {
            $ErrorActionPreference = $prevPref
        }

        if ($code -eq 0 -and $output) {
            $lines = @($output | Where-Object { $_ -and $_.Trim() -ne "" })
            if ($lines.Count -ge 2) {
                return @{
                    Executable = $lines[0].Trim()
                    Version    = $lines[1].Trim()
                }
            }
        }
    }

    return $null
}

Write-Host "==> Checking Python 3.9+..."
$pyInfo = Find-Python39
if (-not $pyInfo) {
    Write-Error (
        "Python 3.9+ was not found (checked 'python' and 'py').`n" +
        "Install Python 3.9+ from https://www.python.org/downloads/ (check 'Add python.exe to PATH') " +
        "or via 'winget install Python.Python.3.12', then re-run .\scripts\install.ps1."
    )
    exit 1
}
$PythonCmd = $pyInfo.Executable
Write-Host ("    Python {0} ({1})" -f $pyInfo.Version, $PythonCmd)

Write-Host "==> Checking agent-desktop..."
$agentDesktopCmd = Get-Command agent-desktop -ErrorAction SilentlyContinue
if ($Upgrade -or -not $agentDesktopCmd) {
    $npmCmd = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npmCmd) {
        Write-Error (
            "npm is required to install agent-desktop@$AgentDesktopVersion, but npm was not found on PATH.`n" +
            "Install Node.js LTS from https://nodejs.org or run 'winget install OpenJS.NodeJS.LTS', " +
            "restart your PowerShell window, and re-run .\scripts\install.ps1."
        )
        exit 1
    }
    Write-Host "    Installing agent-desktop@$AgentDesktopVersion globally via npm..."
    & npm install -g "agent-desktop@$AgentDesktopVersion"
    if ($LASTEXITCODE -ne 0) {
        Write-Error "npm install -g agent-desktop@$AgentDesktopVersion failed with exit code $LASTEXITCODE."
        exit $LASTEXITCODE
    }
    $agentDesktopCmd = Get-Command agent-desktop -ErrorAction SilentlyContinue
}

if ($agentDesktopCmd) {
    $adVersion = & agent-desktop --version 2>&1
    Write-Host ("    {0} ({1})" -f $adVersion, $agentDesktopCmd.Source)
} else {
    Write-Warning "agent-desktop was installed, but was not yet resolved on PATH. Restart PowerShell if needed."
}

$PilotCtlPath = Join-Path $PluginRoot "scripts\pilot_ctl.py"
$SandboxCtlPath = Join-Path $PluginRoot "scripts\sandbox_ctl.py"

Write-Host ""
Write-Host "========================================"
Write-Host "  antigravity-pilot Installation Status"
Write-Host "========================================"
Write-Host ""

if (Test-Path -LiteralPath $PilotCtlPath) {
    Write-Host "--- pilot_ctl.py doctor ---"
    $prevPref = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $PythonCmd $PilotCtlPath doctor
    $ErrorActionPreference = $prevPref
} else {
    Write-Warning "scripts\pilot_ctl.py not found at $PilotCtlPath; skipping doctor check."
}

Write-Host ""
Write-Host "Done."
Write-Host "Test desktop doctor:"
Write-Host "  python scripts\pilot_ctl.py doctor"
if (Test-Path -LiteralPath $SandboxCtlPath) {
    Write-Host ""
    Write-Host "Virtual Linux Desktop Sandboxes (Docker Desktop / WSL2):"
    Write-Host "  scripts\sandbox_ctl.py works directly with Docker Desktop (WSL2 backend)."
    Write-Host "  Example:"
    Write-Host "    python scripts\sandbox_ctl.py build"
    Write-Host "    python scripts\sandbox_ctl.py up dev --vnc"
}
