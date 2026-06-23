# build_mcpb.ps1 — build the comfy-dxt distribution and pack the .mcpb.
#
# The launcher build lives in launcher\ — a vendored, comfy-dxt-configured copy of
# pygo-bootstrap (https://github.com/lugia19/pygo-bootstrap). launcher\build-all.ps1
# produces the standalone downloads:
#   launcher\builds\Comfy-Gen-MCP-<ver>-windows.zip          (exe + installer-resources\)
#   launcher\builds\Comfy-Gen-MCP-<ver>-macos-universal.zip  (signed universal .app; needs WSL+zip)
# This script then stages the Windows launcher into the extension so the packed mcpb
# ships it at <bundle>\bootstrap\. The stdio shim (server/shim.py) copies that into
# ~\.comfy-gen-mcp\runtime and runs the exe, which git-pulls + launches the real server.
#
# The mcpb's self-updating runtime is Windows-only; the macOS .app ships only as a
# standalone download.
#
#   (default)         build launcher(s) -> stage into the extension -> pack the mcpb,
#                     and drop the standalone zips in dist\ as a byproduct.
#   -StandaloneOnly   only build + surface the standalone zips (the "HTTP server" downloads);
#                     skip the bootstrap staging and mcpb pack.
#   -WindowsOnly      skip the macOS .app (faster; no WSL needed). Combine with either mode.

[CmdletBinding()]
param(
    [switch]$WindowsOnly,
    [switch]$StandaloneOnly
)

$ErrorActionPreference = "Stop"

$RuntimeExe   = "comfyui-image-gen-mcp.exe"          # name the shim expects (see server/shim.py)
$launcherDir  = Join-Path $PSScriptRoot "launcher"
$buildAll     = Join-Path $launcherDir "build-all.ps1"
$launcherOut  = Join-Path $launcherDir "builds"

$dist    = Join-Path $PSScriptRoot "dist"
$bootDir = Join-Path $PSScriptRoot "comfyui-image-gen\bootstrap"
New-Item -ItemType Directory -Force $dist | Out-Null

# 1. Build the launcher(s) via the vendored pygo-bootstrap.
Write-Host "=== Building launcher (launcher\build-all.ps1) ===" -ForegroundColor Cyan
if (-not (Test-Path $buildAll)) { throw "Vendored launcher build not found at $buildAll" }
if ($WindowsOnly) { & $buildAll -WindowsOnly } else { & $buildAll }
if ($LASTEXITCODE -ne 0) { throw "build-all.ps1 failed" }

# Locate the produced zips by pattern (version is set in launcher\build-all.ps1).
$winZip = Get-ChildItem -Path $launcherOut -Filter "Comfy-Gen-MCP-*-windows.zip" -ErrorAction SilentlyContinue |
          Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $winZip) { throw "No Comfy-Gen-MCP-*-windows.zip produced in $launcherOut" }
$macZip = Get-ChildItem -Path $launcherOut -Filter "Comfy-Gen-MCP-*-macos-universal.zip" -ErrorAction SilentlyContinue |
          Sort-Object LastWriteTime -Descending | Select-Object -First 1

# 2. Surface the standalone zips ("HTTP server" downloads) at dist\ root for the release page.
Copy-Item $winZip.FullName $dist -Force
if ($macZip) {
    Copy-Item $macZip.FullName $dist -Force
} elseif (-not $WindowsOnly) {
    Write-Host "Note: macOS zip not produced (WSL+zip required) — skipping." -ForegroundColor Yellow
}

if ($StandaloneOnly) {
    $macNote = if ($macZip) { " (+ $($macZip.Name))" } else { "" }
    Write-Host "`nDone (standalone only — mcpb not packed)." -ForegroundColor Green
    Write-Host "  standalone:  $dist\$($winZip.Name)$macNote"
    return
}

# 3. Stage the Windows launcher into the extension (<bundle>\bootstrap\).
Write-Host "=== Staging bootstrap into comfyui-image-gen\bootstrap ===" -ForegroundColor Cyan
$tmp = Join-Path $env:TEMP ("comfy_boot_" + [System.IO.Path]::GetRandomFileName())
Expand-Archive -Path $winZip.FullName -DestinationPath $tmp -Force
try {
    $srcExe = Join-Path $tmp "Comfy-Gen-MCP.exe"
    $srcRes = Join-Path $tmp "installer-resources"
    if (-not (Test-Path $srcExe)) { throw "Missing Comfy-Gen-MCP.exe inside $($winZip.Name)" }
    if (-not (Test-Path $srcRes)) { throw "Missing installer-resources\ inside $($winZip.Name)" }

    if (Test-Path $bootDir) { Remove-Item -Recurse -Force $bootDir }
    New-Item -ItemType Directory -Force $bootDir | Out-Null

    # Rename on copy: the launcher locates its resources via os.Executable(), so the exe
    # name is free — the shim spawns it by the fixed name $RuntimeExe.
    Copy-Item $srcExe (Join-Path $bootDir $RuntimeExe) -Force
    Copy-Item $srcRes (Join-Path $bootDir "installer-resources") -Recurse -Force
} finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

# 4. Pack the mcpb (version comes from comfyui-image-gen\manifest.json).
Write-Host "=== Packing mcpb ===" -ForegroundColor Cyan
$mcpbSrc = Join-Path $PSScriptRoot "comfyui-image-gen"
$mcpbOut = Join-Path $dist "comfyui-image-gen.mcpb"
npx @anthropic-ai/mcpb pack $mcpbSrc $mcpbOut
if ($LASTEXITCODE -ne 0) { throw "mcpb pack failed" }

$macNote = if ($macZip) { " (+ $($macZip.Name))" } else { "" }
Write-Host "`nDone." -ForegroundColor Green
Write-Host "  mcpb:        $mcpbOut"
Write-Host "  standalone:  $dist\$($winZip.Name)$macNote"
