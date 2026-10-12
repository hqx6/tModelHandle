param([Parameter(ValueFromRemainingArguments=$true)][string[]]$ReaArgs)

$ErrorActionPreference = 'Stop'
$taskToolRoot = Join-Path $env:USERPROFILE '.local\share\rea-tools'
$taskManifest = Join-Path $taskToolRoot 'engine.json'
if (-not (Test-Path -LiteralPath $taskManifest)) {
    throw "REA engine configuration missing: $taskManifest"
}
$taskEngine = Get-Content -LiteralPath $taskManifest -Raw -Encoding UTF8 | ConvertFrom-Json
$taskOldGhidra = $env:GHIDRA_INSTALL_DIR
$taskOldJava = $env:JAVA_HOME
$taskOldProvider = $env:REA_ANALYSIS_PROVIDER
try {
    $env:GHIDRA_INSTALL_DIR = $taskEngine.ghidra
    $env:JAVA_HOME = $taskEngine.java
    $env:REA_ANALYSIS_PROVIDER = 'ghidra'
    & $taskEngine.node $taskEngine.entry @ReaArgs
    if ($LASTEXITCODE -ne 0) { throw "REA exited with code $LASTEXITCODE" }
} finally {
    $env:GHIDRA_INSTALL_DIR = $taskOldGhidra
    $env:JAVA_HOME = $taskOldJava
    $env:REA_ANALYSIS_PROVIDER = $taskOldProvider
}
