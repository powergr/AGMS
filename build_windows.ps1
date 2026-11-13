# Builds the Windows app with PyInstaller, checks it, then builds the setup file with Inno Setup.
#
#   powershell -ExecutionPolicy Bypass -File build_windows.ps1                  app + self-test + setup file
#   powershell -ExecutionPolicy Bypass -File build_windows.ps1 -SkipInstaller   app + self-test only
#   Add -SkipOnlineTest if Chrome is not installed here or you are offline: the self-test then
#   skips the HTTPS request and the Chrome start.
#   powershell -ExecutionPolicy Bypass -File build_windows.ps1 -VersionOnly     print the version and stop
#
# Needs the packages from requirements.txt plus pyinstaller in the active Python.
# The setup file step needs Inno Setup 6 (https://jrsoftware.org/isdl.php).
param(
    [switch]$SkipInstaller,
    [switch]$SkipOnlineTest,
    [switch]$VersionOnly
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Get-SourceVersion($file) {
    $match = Select-String -Path $file -Pattern '^__version__ = "(.+)"' | Select-Object -First 1
    if (-not $match) { throw "No __version__ found in $file" }
    $match.Matches[0].Groups[1].Value
}

$version = Get-SourceVersion "google_maps_scraper_gui.py"
$cliVersion = Get-SourceVersion "google_maps_scraper.py"
if ($version -ne $cliVersion) {
    throw "The GUI is version $version but the CLI is version $cliVersion. Make them match before building."
}

if ($VersionOnly) {
    Write-Output $version
    return
}

Write-Host "Building AGMS $version"

$icon = (Resolve-Path "icon.ico").Path
python -m PyInstaller --noconfirm --clean --noupx --windowed `
    --name AGMS `
    --icon $icon `
    --add-data "$icon;." `
    --distpath "$PSScriptRoot\dist" `
    --workpath "$PSScriptRoot\build\pyinstaller" `
    --specpath "$PSScriptRoot\build" `
    --hidden-import google_maps_scraper `
    --hidden-import openpyxl `
    --hidden-import psycopg2 `
    --collect-submodules sqlalchemy.dialects `
    --collect-submodules selenium.webdriver `
    google_maps_scraper_gui.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# A windowed exe returns at once when called directly, so start it and wait for the exit code
$report = Join-Path $env:TEMP "agms_selftest.txt"
Remove-Item $report -ErrorAction SilentlyContinue
$testArguments = @("--self-test")
if (-not $SkipOnlineTest) { $testArguments += "--online" }
$test = Start-Process -FilePath "$PSScriptRoot\dist\AGMS\AGMS.exe" -ArgumentList $testArguments -Wait -PassThru
if (Test-Path $report) { Get-Content $report }
if ($test.ExitCode -ne 0) { throw "The built app failed its self-test. See the report above." }

if ($SkipInstaller) {
    Write-Host "App built in dist\AGMS"
    return
}

$iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $iscc) {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )
    $iscc = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $iscc) {
    throw "Inno Setup 6 was not found. Install it from https://jrsoftware.org/isdl.php, or run with -SkipInstaller."
}

& $iscc "/DAppVersion=$version" "$PSScriptRoot\installer\agms.iss"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }

Write-Host "Setup file: dist\installer\AGMS-Setup-$version.exe"
