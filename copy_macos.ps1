# copy_macos.ps1
# Copy build files to network share for macOS building

$networkPath = "\\TRUENAS\MiscShare\comfyui-image-gen-build"
$subDir = "$networkPath\comfyui-image-gen"

# Clean and create destination
if (Test-Path $networkPath) {
    Write-Host "Cleaning existing files..." -ForegroundColor Yellow
    Remove-Item "$networkPath\*" -Recurse -Force
}
New-Item -ItemType Directory -Path $subDir -Force | Out-Null

# Copy server and model_packs into the subfolder
$subItems = @(
    "comfyui-image-gen\server",
    "comfyui-image-gen\model_packs"
)

foreach ($item in $subItems) {
    $src = Join-Path $PSScriptRoot $item
    if (Test-Path $src) {
        $dest = Join-Path $subDir (Split-Path $item -Leaf)
        Write-Host "Copying $item -> $dest" -ForegroundColor Yellow
        Copy-Item $src -Destination $dest -Recurse -Force
        Write-Host "  OK" -ForegroundColor Green
    } else {
        Write-Host "WARNING: $item not found, skipping" -ForegroundColor Yellow
    }
}

# Copy build script to root (next to comfyui-image-gen/)
$buildScript = Join-Path $PSScriptRoot "build_exe_macos.sh"
if (Test-Path $buildScript) {
    Write-Host "Copying build_exe_macos.sh" -ForegroundColor Yellow
    Copy-Item $buildScript -Destination $networkPath -Force
    Write-Host "  OK" -ForegroundColor Green
}

Write-Host "`nDone! Files copied to $networkPath" -ForegroundColor Green
Write-Host "On the Mac, run:" -ForegroundColor White
Write-Host "  cd /Volumes/MiscShare/comfyui-image-gen-build && chmod +x build_exe_macos.sh && ./build_exe_macos.sh" -ForegroundColor Cyan
