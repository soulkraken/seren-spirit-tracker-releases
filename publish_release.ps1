param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version,

    [string]$Notes = "Seren Spirit Tracker v$Version"
)

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BuildScript = Join-Path $ProjectDir "build_release.ps1"
$PortableZip = Join-Path $ProjectDir "dist\Seren-Spirit-Tracker-Portable-v$Version.zip"
$UpdateZip = Join-Path $ProjectDir "dist\Seren-Spirit-Tracker-Update-v$Version.zip"
$Checksum = "$UpdateZip.sha256"
$Tag = "v$Version"

Push-Location $ProjectDir
try {
    $GhCommand = Get-Command gh -ErrorAction SilentlyContinue
    $Gh = if ($GhCommand) {
        $GhCommand.Source
    }
    elseif (Test-Path -LiteralPath "C:\Program Files\GitHub CLI\gh.exe") {
        "C:\Program Files\GitHub CLI\gh.exe"
    }
    else {
        $null
    }

    if (-not $Gh) {
        throw "GitHub CLI (gh) is not installed or is not available on PATH."
    }

    & $Gh auth status
    if ($LASTEXITCODE -ne 0) {
        throw "GitHub CLI is not authenticated. Run 'gh auth login' first."
    }

    $PendingChanges = & git status --porcelain
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect the Git working tree."
    }
    if ($PendingChanges) {
        throw "Commit or discard the current source changes before publishing a release."
    }

    & git fetch origin --tags
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to fetch the current repository state."
    }

    & git rev-parse --verify --quiet "refs/tags/$Tag" | Out-Null
    if ($LASTEXITCODE -eq 0) {
        throw "Tag $Tag already exists. Choose a newer version."
    }

    & $BuildScript -Version $Version
    if ($LASTEXITCODE -ne 0) {
        throw "The release build failed."
    }

    foreach ($File in @($PortableZip, $UpdateZip, $Checksum)) {
        if (-not (Test-Path -LiteralPath $File -PathType Leaf)) {
            throw "Expected release file was not created: $File"
        }
    }

    & git push origin main
    if ($LASTEXITCODE -ne 0) {
        throw "The main branch could not be pushed."
    }

    & $Gh release create $Tag `
        $PortableZip `
        $UpdateZip `
        $Checksum `
        --repo "soulkraken/seren-spirit-tracker-releases" `
        --target main `
        --title "Seren Spirit Tracker $Tag" `
        --notes $Notes

    if ($LASTEXITCODE -ne 0) {
        throw "GitHub release creation failed."
    }

    Write-Host "Published $Tag successfully."
}
finally {
    Pop-Location
}
