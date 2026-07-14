# build-all.ps1 — build the pygo-bootstrap launcher for Windows and macOS.
#
# Produces:
#   builds\<APP_NAME>-<VERSION>-windows.zip       (App.exe + installer-resources\)
#   builds\<APP_NAME>-<VERSION>-macos-universal.zip  (signed universal .app)
#
# Deployer inputs (required, gitignored):
#   repo.json            — your config (copy from repo-example.json and fill in)
#   resources\icon.png   — your app icon (square, 256x256 or larger)
#
# Build tools are downloaded into resources\ on first run and reused after that.
# Requirements on the build host: Go, and (for the macOS bundle) WSL with `zip`.

[CmdletBinding()]
param(
    [switch]$WindowsOnly,   # skip the macOS bundle
    [switch]$MacOnly        # skip the Windows build
)

$ErrorActionPreference = "Stop"

# ============================ Configuration ============================
# Configured for comfy-dxt. $APP_NAME is also the macOS data-dir name
# (~/Library/Application Support/<APP_NAME>/), so keep it stable.
# Bump $VERSION alongside comfyui-image-gen\manifest.json on release (it only
# labels the standalone artifacts; the mcpb's version comes from the manifest).
$APP_NAME  = "Comfy-Gen-MCP"
$BUNDLE_ID = "com.lugia19.comfy-gen-mcp"
$VERSION   = "2.0.1"

# Pin uv if you want reproducible builds; "latest" tracks the newest release.
$UV_VERSION = "latest"
# ======================================================================

$root      = $PSScriptRoot
$resources = Join-Path $root "resources"
$builds    = Join-Path $root "builds"
$repoJson  = Join-Path $root "repo.json"
$iconPng   = Join-Path $resources "icon.png"

function Ensure-Dir($path) {
    if (-not (Test-Path $path)) { New-Item -ItemType Directory -Path $path -Force | Out-Null }
}

function Get-GitHubAsset {
    # Returns the browser_download_url of the first asset whose name matches
    # $pattern in the given repo's latest release.
    param([string]$repo, [string]$pattern)
    $headers = @{ "User-Agent" = "pygo-bootstrap-build" }
    if ($env:GITHUB_TOKEN) { $headers["Authorization"] = "Bearer $env:GITHUB_TOKEN" }
    $rel = Invoke-RestMethod -Headers $headers -Uri "https://api.github.com/repos/$repo/releases/latest"
    $asset = $rel.assets | Where-Object { $_.name -match $pattern } | Select-Object -First 1
    if (-not $asset) { throw "No asset matching '$pattern' in latest release of $repo" }
    return $asset.browser_download_url
}

function Download-File($url, $dest) {
    Write-Host "  downloading $url" -ForegroundColor DarkGray
    $headers = @{ "User-Agent" = "pygo-bootstrap-build" }
    Invoke-WebRequest -Headers $headers -Uri $url -OutFile $dest
}

# Extract a single named file from a .zip into $destPath (searches recursively).
function Extract-FromZip($zipPath, $innerNamePattern, $destPath) {
    $tmp = Join-Path $env:TEMP ("pgz_" + [System.IO.Path]::GetRandomFileName())
    Ensure-Dir $tmp
    Expand-Archive -Path $zipPath -DestinationPath $tmp -Force
    $found = Get-ChildItem -Path $tmp -Recurse -File | Where-Object { $_.Name -match $innerNamePattern } | Select-Object -First 1
    if (-not $found) { throw "Could not find '$innerNamePattern' inside $zipPath" }
    Copy-Item $found.FullName $destPath -Force
    Remove-Item $tmp -Recurse -Force
}

# Extract a single named file from a .tar.gz into $destPath using Windows tar.
function Extract-FromTarGz($tgzPath, $innerNamePattern, $destPath) {
    $tmp = Join-Path $env:TEMP ("pgt_" + [System.IO.Path]::GetRandomFileName())
    Ensure-Dir $tmp
    tar -xzf $tgzPath -C $tmp
    if ($LASTEXITCODE -ne 0) { throw "tar failed to extract $tgzPath" }
    $found = Get-ChildItem -Path $tmp -Recurse -File | Where-Object { $_.Name -match $innerNamePattern } | Select-Object -First 1
    if (-not $found) { throw "Could not find '$innerNamePattern' inside $tgzPath" }
    Copy-Item $found.FullName $destPath -Force
    Remove-Item $tmp -Recurse -Force
}

function UvUrl($asset) {
    if ($UV_VERSION -eq "latest") {
        return "https://github.com/astral-sh/uv/releases/latest/download/$asset"
    }
    return "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/$asset"
}

# ============================ Preflight ============================
Write-Host "Preflight checks..." -ForegroundColor Cyan
Ensure-Dir $resources
Ensure-Dir $builds

if (-not (Test-Path $repoJson)) {
    throw "repo.json not found. Copy repo-example.json to repo.json and fill in your repository details before building."
}
if (-not (Test-Path $iconPng)) {
    throw "resources\icon.png not found. Provide a square PNG icon (256x256 or larger) at resources\icon.png before building."
}
if (-not (Get-Command go -ErrorAction SilentlyContinue)) {
    throw "Go toolchain not found on PATH. Install Go to build the launcher."
}

$buildMac = -not $WindowsOnly
$buildWin = -not $MacOnly

# ---- Fetch + cache build tools (kept in resources\) ----
$uvWin     = Join-Path $resources "uv.exe"
$uvMacArm  = Join-Path $resources "uv-aarch64-apple-darwin"
$uvMacX64  = Join-Path $resources "uv-x86_64-apple-darwin"
$rcedit    = Join-Path $resources "rcedit.exe"
$rcodesign = Join-Path $resources "rcodesign.exe"
$mkicon    = Join-Path $resources "mkicon.exe"
$lipo      = Join-Path $resources "lipo.exe"

if ($buildWin -and -not (Test-Path $uvWin)) {
    Write-Host "Fetching uv (windows)..." -ForegroundColor Cyan
    $z = Join-Path $env:TEMP "uv-win.zip"
    Download-File (UvUrl "uv-x86_64-pc-windows-msvc.zip") $z
    Extract-FromZip $z "^uv\.exe$" $uvWin
    Remove-Item $z -Force
}
if ($buildWin -and -not (Test-Path $rcedit)) {
    Write-Host "Fetching rcedit..." -ForegroundColor Cyan
    Download-File (Get-GitHubAsset "electron/rcedit" "rcedit-x64\.exe$") $rcedit
}
if ($buildMac -and -not (Test-Path $uvMacArm)) {
    Write-Host "Fetching uv (macOS arm64)..." -ForegroundColor Cyan
    $t = Join-Path $env:TEMP "uv-arm.tar.gz"
    Download-File (UvUrl "uv-aarch64-apple-darwin.tar.gz") $t
    Extract-FromTarGz $t "^uv$" $uvMacArm
    Remove-Item $t -Force
}
if ($buildMac -and -not (Test-Path $uvMacX64)) {
    Write-Host "Fetching uv (macOS x86_64)..." -ForegroundColor Cyan
    $t = Join-Path $env:TEMP "uv-x64.tar.gz"
    Download-File (UvUrl "uv-x86_64-apple-darwin.tar.gz") $t
    Extract-FromTarGz $t "^uv$" $uvMacX64
    Remove-Item $t -Force
}
if ($buildMac -and -not (Test-Path $rcodesign)) {
    Write-Host "Fetching rcodesign..." -ForegroundColor Cyan
    $z = Join-Path $env:TEMP "rcodesign.zip"
    Download-File (Get-GitHubAsset "indygreg/apple-platform-rs" "x86_64-pc-windows-msvc\.zip$") $z
    Extract-FromZip $z "^rcodesign\.exe$" $rcodesign
    Remove-Item $z -Force
}
if ($buildMac -and -not (Test-Path $lipo)) {
    Write-Host "Installing konoui/lipo..." -ForegroundColor Cyan
    $env:GOBIN = $resources
    & go install github.com/konoui/lipo@latest
    if ($LASTEXITCODE -ne 0) { throw "go install konoui/lipo failed" }
    Remove-Item Env:\GOBIN
}

# ---- Build (cache) the icon converter and produce app.ico / app.icns ----
if (-not (Test-Path $mkicon)) {
    Write-Host "Building mkicon..." -ForegroundColor Cyan
    $env:GOOS = "windows"; $env:GOARCH = "amd64"
    & go -C (Join-Path $root "tools\mkicon") build -o $mkicon .
    if ($LASTEXITCODE -ne 0) { throw "building mkicon failed" }
}
$appIco  = Join-Path $builds "app.ico"
$appIcns = Join-Path $builds "app.icns"
Write-Host "Converting icon.png -> app.ico / app.icns..." -ForegroundColor Cyan
& $mkicon $iconPng $appIco $appIcns
if ($LASTEXITCODE -ne 0) { throw "icon conversion failed" }

# ============================ Windows build ============================
if ($buildWin) {
    Write-Host "`nBuilding Windows..." -ForegroundColor Cyan
    $winDir = Join-Path $builds "win"
    if (Test-Path $winDir) { Remove-Item $winDir -Recurse -Force }
    Ensure-Dir $winDir
    $instRes = Join-Path $winDir "installer-resources"
    Ensure-Dir $instRes

    $exe = Join-Path $winDir "$APP_NAME.exe"
    $env:GOOS = "windows"; $env:GOARCH = "amd64"
    Push-Location $root
    & go build -o $exe .
    $ok = $LASTEXITCODE -eq 0
    Pop-Location
    if (-not $ok) { throw "Windows build failed" }

    & $rcedit $exe --set-icon $appIco
    if ($LASTEXITCODE -ne 0) { Write-Host "Warning: rcedit icon embed failed" -ForegroundColor Yellow }

    Copy-Item $repoJson (Join-Path $instRes "repo.json") -Force
    Copy-Item (Join-Path $root "install.py") (Join-Path $instRes "install.py") -Force
    Copy-Item $uvWin (Join-Path $instRes "uv.exe") -Force
    Copy-Item $iconPng (Join-Path $instRes "icon.png") -Force

    $zip = Join-Path $builds "$APP_NAME-$VERSION-windows.zip"
    if (Test-Path $zip) { Remove-Item $zip -Force }
    Compress-Archive -Path (Join-Path $winDir "*") -DestinationPath $zip
    Write-Host "Created: $zip" -ForegroundColor Green
}

# ============================ macOS build ============================
if ($buildMac) {
    Write-Host "`nBuilding macOS (universal)..." -ForegroundColor Cyan

    $lamd64 = Join-Path $builds "launcher-amd64"
    $larm64 = Join-Path $builds "launcher-arm64"
    Push-Location $root
    $env:GOOS = "darwin"; $env:GOARCH = "amd64"; & go build -o $lamd64 .
    $okA = $LASTEXITCODE -eq 0
    $env:GOOS = "darwin"; $env:GOARCH = "arm64"; & go build -o $larm64 .
    $okB = $LASTEXITCODE -eq 0
    Pop-Location
    if (-not ($okA -and $okB)) { throw "macOS cross-compile failed" }

    # Fuse the two arch slices into universal Mach-O binaries (launcher + uv).
    $luniv  = Join-Path $builds "launcher-universal"
    $uvUniv = Join-Path $builds "uv-universal"
    & $lipo -create $lamd64 $larm64 -output $luniv
    if ($LASTEXITCODE -ne 0) { throw "lipo (launcher) failed" }
    & $lipo -create $uvMacX64 $uvMacArm -output $uvUniv
    if ($LASTEXITCODE -ne 0) { throw "lipo (uv) failed" }

    # Assemble the .app bundle.
    $appBundle = Join-Path $builds "$APP_NAME.app"
    if (Test-Path $appBundle) { Remove-Item $appBundle -Recurse -Force }
    $macosDir = Join-Path $appBundle "Contents\MacOS"
    $resDir   = Join-Path $appBundle "Contents\Resources"
    Ensure-Dir $macosDir
    Ensure-Dir $resDir

    Copy-Item $luniv  (Join-Path $macosDir $APP_NAME) -Force
    Copy-Item $uvUniv (Join-Path $resDir "uv") -Force
    Copy-Item (Join-Path $root "install.py") (Join-Path $resDir "install.py") -Force
    Copy-Item $repoJson (Join-Path $resDir "repo.json") -Force
    Copy-Item $iconPng (Join-Path $resDir "icon.png") -Force
    Copy-Item $appIcns (Join-Path $resDir "app.icns") -Force

    $infoPlist = @"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>$APP_NAME</string>
    <key>CFBundleIconFile</key>
    <string>app.icns</string>
    <key>CFBundleIdentifier</key>
    <string>$BUNDLE_ID</string>
    <key>CFBundleName</key>
    <string>$APP_NAME</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleVersion</key>
    <string>$VERSION</string>
    <key>CFBundleShortVersionString</key>
    <string>$VERSION</string>
    <key>LSMinimumSystemVersion</key>
    <string>11.0</string>
</dict>
</plist>
"@
    # Write Info.plist as UTF-8 without BOM.
    [System.IO.File]::WriteAllText((Join-Path $appBundle "Contents\Info.plist"), $infoPlist, (New-Object System.Text.UTF8Encoding($false)))

    # Ad-hoc sign (covers the assembled tree; works from Windows via rcodesign).
    Write-Host "Ad-hoc signing $APP_NAME.app..." -ForegroundColor Cyan
    & $rcodesign sign $appBundle
    if ($LASTEXITCODE -ne 0) { Write-Host "Warning: rcodesign sign failed" -ForegroundColor Yellow }

    # Zip via WSL to preserve the executable bits and bundle structure.
    if (Get-Command wsl -ErrorAction SilentlyContinue) {
        $zipName = "$APP_NAME-$VERSION-macos-universal.zip"
        $zipPath = Join-Path $builds $zipName
        if (Test-Path $zipPath) { Remove-Item $zipPath -Force }

        # wsl may print benign warnings to stderr (e.g. "Nested virtualization is not
        # supported on this machine"), which it does even though wslpath/zip still work.
        # Under EAP=Stop, any native-command stderr is promoted to a *terminating* error
        # (and 2>$null doesn't reliably suppress it in Windows PowerShell 5.1), so relax
        # EAP locally and judge success by the zip's exit code instead.
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $zipOk = $false
        try {
            $buildsWsl = (& wsl wslpath -a ($builds -replace '\\','/')) 2>$null
            if (-not $buildsWsl) {
                # Fallback conversion C:\foo -> /mnt/c/foo
                $p = (Resolve-Path $builds).Path -replace '\\','/'
                if ($p -match '^([A-Za-z]):(.*)') { $buildsWsl = "/mnt/$($matches[1].ToLower())$($matches[2])" }
            }
            $buildsWsl = $buildsWsl.Trim()
            & wsl sh -c "cd '$buildsWsl' && chmod +x '$APP_NAME.app/Contents/MacOS/$APP_NAME' && chmod +x '$APP_NAME.app/Contents/Resources/uv' && rm -f '$zipName' && zip -r -y '$zipName' '$APP_NAME.app'" 2>$null
            $zipOk = ($LASTEXITCODE -eq 0)
        } finally {
            $ErrorActionPreference = $prevEAP
            $global:LASTEXITCODE = 0   # don't let a stray wsl exit code fail the parent build
        }
        if ($zipOk) {
            Write-Host "Created: $zipPath" -ForegroundColor Green
        } else {
            Write-Host "Warning: WSL zip failed; the unzipped .app remains at $appBundle" -ForegroundColor Yellow
        }
    } else {
        Write-Host "WSL not found — skipping zip. The signed bundle is at $appBundle" -ForegroundColor Yellow
        Write-Host "(Zipping with Compress-Archive would drop the executable bit on the binary.)" -ForegroundColor Yellow
    }

    # Clean intermediate arch binaries.
    Remove-Item $lamd64, $larm64, $luniv, $uvUniv -Force -ErrorAction SilentlyContinue
}

Write-Host "`nDone." -ForegroundColor Green
