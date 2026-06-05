$ErrorActionPreference = "Stop"

$BootstrapDir = "C:\Users\lugia19\PycharmProjects\pygo-bootstrap"
$DistDir = "$PSScriptRoot\dist\http_dist"
$Icon = "$PSScriptRoot\icon.png"

# Clean and create dist directory
if (Test-Path $DistDir) { Remove-Item -Recurse -Force $DistDir }
New-Item -ItemType Directory -Force $DistDir | Out-Null

# Compile Go launcher with icon (requires go and rsrc for icon embedding)
Write-Host "Compiling Go launcher..."
Push-Location $BootstrapDir
go build -ldflags "-s -w" -o "$DistDir\comfyui-image-gen-mcp.exe" launcher.go
Pop-Location

# Copy install.py from pygo-bootstrap
Copy-Item "$BootstrapDir\install.py" "$DistDir\install.py"

# Download uv.exe if not already cached
$UvCache = "$PSScriptRoot\dist\uv.exe"
if (-not (Test-Path $UvCache)) {
    Write-Host "Downloading uv.exe..."
    $UvUrl = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"
    $UvZip = "$PSScriptRoot\dist\uv.zip"
    Invoke-WebRequest -Uri $UvUrl -OutFile $UvZip
    Expand-Archive -Path $UvZip -DestinationPath "$PSScriptRoot\dist\uv_tmp" -Force
    Copy-Item "$PSScriptRoot\dist\uv_tmp\uv.exe" $UvCache
    Remove-Item -Recurse -Force "$PSScriptRoot\dist\uv_tmp"
    Remove-Item $UvZip
}
Copy-Item $UvCache "$DistDir\uv.exe"

# Write repo.json (no BOM — Go's JSON parser can't handle it)
$repoJson = @'
{
  "repo_url": "https://github.com/lugia19/comfy-dxt.git",
  "repo_dir": "comfy-dxt",
  "startup_script": "run_http.py",
  "use_pythonw": false,
  "venv_folder": "venv",
  "python_version": "3.12",
  "icon": "icon.ico"
}
'@
[System.IO.File]::WriteAllText("$DistDir\repo.json", $repoJson, [System.Text.UTF8Encoding]::new($false))

# Copy icon if it exists
if (Test-Path $Icon) {
    Copy-Item $Icon "$DistDir\icon.ico"
}

# Create zip
$ZipPath = "$PSScriptRoot\dist\comfyui-image-gen-http.zip"
if (Test-Path $ZipPath) { Remove-Item $ZipPath }
Compress-Archive -Path "$DistDir\*" -DestinationPath $ZipPath

Write-Host "Done! Distribution zip: $ZipPath"
Write-Host "Contents:"
Get-ChildItem $DistDir | Format-Table Name, Length
