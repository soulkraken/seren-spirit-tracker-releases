param(
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version = "1.0.0"
)

$ErrorActionPreference = "Stop"

$GitHubRepository = "soulkraken/seren-spirit-tracker-releases"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = if ($env:SEREN_PYTHON) {
    $env:SEREN_PYTHON
}
else {
    (Get-Command python -ErrorAction Stop).Source
}
$TesseractInstall = if ($env:SEREN_TESSERACT_DIR) {
    $env:SEREN_TESSERACT_DIR
}
else {
    "C:\Program Files\Tesseract-OCR"
}
$StageDir = Join-Path $ProjectDir "release_staging"
$BuildDir = Join-Path $ProjectDir "build"
$DistDir = Join-Path $ProjectDir "dist"
$PortableDir = Join-Path $DistDir "Seren Spirit Tracker"
$AppDir = Join-Path $PortableDir "app"
$PortableZip = Join-Path $DistDir "Seren-Spirit-Tracker-Portable-v$Version.zip"
$UpdateZip = Join-Path $DistDir "Seren-Spirit-Tracker-Update-v$Version.zip"
$ChecksumPath = "$UpdateZip.sha256"

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Python was not found at $Python"
}
if (-not (Test-Path -LiteralPath (Join-Path $TesseractInstall "tesseract.exe") -PathType Leaf)) {
    throw "Tesseract was not found at $TesseractInstall"
}
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Version must use major.minor.patch format."
}

foreach ($Target in @($StageDir, $BuildDir, $DistDir)) {
    $ResolvedProject = [IO.Path]::GetFullPath($ProjectDir).TrimEnd('\')
    $ResolvedTarget = [IO.Path]::GetFullPath($Target).TrimEnd('\')
    if (-not $ResolvedTarget.StartsWith($ResolvedProject + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a path outside the project: $ResolvedTarget"
    }
    if (Test-Path -LiteralPath $ResolvedTarget) {
        Remove-Item -LiteralPath $ResolvedTarget -Recurse -Force
    }
}

# Stage the minimal Tesseract runtime used by the main application.
$TesseractStage = Join-Path $StageDir "Tesseract-OCR"
New-Item -ItemType Directory -Path (Join-Path $TesseractStage "tessdata") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $TesseractStage "doc") -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $TesseractInstall "tesseract.exe") -Destination $TesseractStage
Get-ChildItem -LiteralPath $TesseractInstall -File -Filter "*.dll" |
    Copy-Item -Destination $TesseractStage
Copy-Item -LiteralPath (Join-Path $TesseractInstall "tessdata\eng.traineddata") -Destination (Join-Path $TesseractStage "tessdata")
Copy-Item -LiteralPath (Join-Path $TesseractInstall "doc\LICENSE") -Destination (Join-Path $TesseractStage "doc")
Copy-Item -LiteralPath (Join-Path $TesseractInstall "doc\AUTHORS") -Destination (Join-Path $TesseractStage "doc")

# Build the independently replaceable main tracker under app\.
$MainDist = Join-Path $StageDir "main_dist"
& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onedir `
    --contents-directory "runtime" `
    --name "Seren Spirit Tracker App" `
    --icon (Join-Path $ProjectDir "seren_spirit.ico") `
    --distpath $MainDist `
    --workpath (Join-Path $BuildDir "main") `
    --specpath (Join-Path $BuildDir "main") `
    --additional-hooks-dir (Join-Path $ProjectDir "pyinstaller_hooks") `
    --exclude-module pandas `
    --exclude-module numpy `
    --add-data "$(Join-Path $ProjectDir 'seren_spirit.ico');." `
    --add-data "$(Join-Path $ProjectDir 'seren_spirit_icon.png');." `
    --add-data "$(Join-Path $ProjectDir 'seren_spirit_title_icon.png');." `
    --add-data "$TesseractStage;Tesseract-OCR" `
    (Join-Path $ProjectDir "seren_watcher_gui.py")
if ($LASTEXITCODE -ne 0) {
    throw "Main application build failed with exit code $LASTEXITCODE"
}

$MainOutput = Join-Path $MainDist "Seren Spirit Tracker App"
New-Item -ItemType Directory -Path $AppDir -Force | Out-Null
Copy-Item -Path (Join-Path $MainOutput "*") -Destination $AppDir -Recurse -Force

$VersionManifest = [ordered]@{
    version = $Version
    built_at_utc = [DateTime]::UtcNow.ToString("o")
}
$VersionManifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $AppDir "version.json") -Encoding UTF8

# Build the small stable launcher/updater at the installation root.
$LauncherDist = Join-Path $StageDir "launcher_dist"
& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onefile `
    --name "Seren Spirit Tracker" `
    --icon (Join-Path $ProjectDir "seren_spirit.ico") `
    --distpath $LauncherDist `
    --workpath (Join-Path $BuildDir "launcher") `
    --specpath (Join-Path $BuildDir "launcher") `
    (Join-Path $ProjectDir "launcher.py")
if ($LASTEXITCODE -ne 0) {
    throw "Launcher build failed with exit code $LASTEXITCODE"
}
Copy-Item -LiteralPath (Join-Path $LauncherDist "Seren Spirit Tracker.exe") -Destination $PortableDir

$UpdaterConfig = [ordered]@{
    github_repository = $GitHubRepository
    check_for_updates = $true
}
$UpdaterConfig | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PortableDir "updater_config.json") -Encoding UTF8

Copy-Item -LiteralPath (Join-Path $ProjectDir "README - START HERE.txt") -Destination $PortableDir
Copy-Item -LiteralPath (Join-Path $ProjectDir "THIRD-PARTY-NOTICES.txt") -Destination $PortableDir

$DataDir = Join-Path $PortableDir "data"
New-Item -ItemType Directory -Path $DataDir -Force | Out-Null
Set-Content -LiteralPath (Join-Path $DataDir "README.txt") -Encoding UTF8 -Value @"
This folder contains your personal tracker settings and drop history.

Keep this folder when installing updates. Back it up to protect your data.
The release package never contains another user's database or settings.
"@

& (Join-Path $AppDir "runtime\Tesseract-OCR\tesseract.exe") --version | Select-Object -First 1
if ($LASTEXITCODE -ne 0) {
    throw "The bundled Tesseract smoke test failed."
}

# The full portable archive is for new installs; the updater archive contains
# only app\ and therefore cannot overwrite launcher settings or personal data.
Compress-Archive -LiteralPath $PortableDir -DestinationPath $PortableZip -CompressionLevel Optimal
Compress-Archive -LiteralPath $AppDir -DestinationPath $UpdateZip -CompressionLevel Optimal

$UpdateHash = (Get-FileHash -LiteralPath $UpdateZip -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath $ChecksumPath -Encoding ASCII -Value "$UpdateHash  $([IO.Path]::GetFileName($UpdateZip))"

Write-Host "Portable release: $PortableZip"
Write-Host "Updater payload: $UpdateZip"
Write-Host "Update checksum: $ChecksumPath"
