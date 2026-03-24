Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Optional environment variable overrides:
# - CODEX_HOME
# - CODEX_BINARY
# - CODEX_MODEL
# - CODEX_REASONING_EFFORT
# - CODEX_REVIEWER_FRAMEWORK_ROOT
# - ENABLE_EXA
# - EXA_API_KEY
# - SHRIMP_DATA_DIR
# - CONDA_EXE

function Get-CommandPath {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Names
    )

    foreach ($name in $Names) {
        $command = Get-Command $name -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -eq $command) {
            continue
        }

        if ($command.Path) {
            return $command.Path
        }

        if ($command.Source -and (Test-Path -LiteralPath $command.Source)) {
            return $command.Source
        }
    }

    return $null
}

function Convert-ToTomlPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PathValue
    )

    return ($PathValue -replace "\\", "/")
}

function Add-PathSegmentFront {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Segments,
        [string]$Candidate
    )

    if ([string]::IsNullOrWhiteSpace($Candidate) -or -not (Test-Path -LiteralPath $Candidate -PathType Container)) {
        return ,$Segments
    }

    $normalizedCandidate = $Candidate.TrimEnd("\")
    $filtered = @($Segments | Where-Object { $_.TrimEnd("\") -ine $normalizedCandidate })
    return @($Candidate) + $filtered
}

function Add-PathSegmentBack {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Segments,
        [string]$Candidate
    )

    if ([string]::IsNullOrWhiteSpace($Candidate) -or -not (Test-Path -LiteralPath $Candidate -PathType Container)) {
        return ,$Segments
    }

    $normalizedCandidate = $Candidate.TrimEnd("\")
    if ($Segments | Where-Object { $_.TrimEnd("\") -ieq $normalizedCandidate }) {
        return ,$Segments
    }

    return $Segments + $Candidate
}

function Join-PathList {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Segments
    )

    return (($Segments | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }) -join ";")
}

function Find-LatestCodexDir {
    $roots = @(
        (Join-Path $HOME ".vscode\extensions"),
        (Join-Path $HOME ".vscode-insiders\extensions")
    )

    foreach ($root in $roots) {
        if (-not (Test-Path -LiteralPath $root -PathType Container)) {
            continue
        }

        $extensions = Get-ChildItem -LiteralPath $root -Directory -Filter "openai.chatgpt-*"
        foreach ($extension in ($extensions | Sort-Object LastWriteTimeUtc -Descending)) {
            foreach ($relativeDir in @("bin\win32-x64", "bin\win32-arm64", "bin\win32-ia32")) {
                $candidate = Join-Path $extension.FullName $relativeDir
                if (Test-Path -LiteralPath $candidate -PathType Container) {
                    return $candidate
                }
            }
        }
    }

    return $null
}

function Copy-TreeContent {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SourceDir,
        [Parameter(Mandatory = $true)]
        [string]$DestinationDir
    )

    if (-not (Test-Path -LiteralPath $SourceDir -PathType Container)) {
        throw "Source directory not found: $SourceDir"
    }

    $items = Get-ChildItem -LiteralPath $SourceDir -Force
    foreach ($item in $items) {
        Copy-Item -LiteralPath $item.FullName -Destination $DestinationDir -Recurse -Force
    }
}

$projectRoot = $PSScriptRoot
$codexHomeDir = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME ".codex" }
$configPath = Join-Path $codexHomeDir "config.toml"
$codexBinDir = Join-Path $codexHomeDir "bin"
$codexScriptsDir = Join-Path $codexHomeDir "scripts"
$codexSkillsDir = Join-Path $codexHomeDir "skills"
$userHome = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }

$condaPath = if ($env:CONDA_EXE) { $env:CONDA_EXE } else { Get-CommandPath -Names @("conda.exe", "conda") }
$npxPath = Get-CommandPath -Names @("npx.cmd", "npx.exe", "npx")
$pythonPath = Get-CommandPath -Names @("python.exe", "python3.exe", "python")

if ([string]::IsNullOrWhiteSpace($condaPath)) {
    throw "setting.ps1: conda not found. Set CONDA_EXE or add conda.exe to PATH."
}

if ([string]::IsNullOrWhiteSpace($npxPath)) {
    throw "setting.ps1: npx not found. Install Node.js or add npx.cmd to PATH."
}

if ([string]::IsNullOrWhiteSpace($pythonPath)) {
    throw "setting.ps1: python not found. Install Python or add python.exe to PATH."
}

$condaBinDir = Split-Path -Path $condaPath -Parent
$npxBinDir = Split-Path -Path $npxPath -Parent
$pythonBinDir = Split-Path -Path $pythonPath -Parent
$vsCodeCodexDir = Find-LatestCodexDir

$baseEnvSegments = @(
    (Join-Path $env:SystemRoot "System32"),
    (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0"),
    $env:SystemRoot
)
$baseEnvSegments = Add-PathSegmentFront -Segments $baseEnvSegments -Candidate $pythonBinDir
$baseEnvSegments = Add-PathSegmentFront -Segments $baseEnvSegments -Candidate $npxBinDir
$baseEnvSegments = Add-PathSegmentFront -Segments $baseEnvSegments -Candidate $condaBinDir
$mcpEnvSegments = Add-PathSegmentBack -Segments $baseEnvSegments -Candidate $vsCodeCodexDir

$reviewerRoot = if ($env:CODEX_REVIEWER_FRAMEWORK_ROOT) { $env:CODEX_REVIEWER_FRAMEWORK_ROOT } else { $projectRoot }
$codexBinaryPath = if ($env:CODEX_BINARY) { $env:CODEX_BINARY } else { Join-Path $codexHomeDir "bin\codex-latest.cmd" }
$codexModelValue = if ($env:CODEX_MODEL) { $env:CODEX_MODEL } else { "gpt-5.4" }
$codexReasoningEffortValue = if ($env:CODEX_REASONING_EFFORT) { $env:CODEX_REASONING_EFFORT } else { "xhigh" }
$shrimpDataDirValue = if ($env:SHRIMP_DATA_DIR) { $env:SHRIMP_DATA_DIR } else { ".shrimp" }
$enableExaValue = if ($env:ENABLE_EXA) { $env:ENABLE_EXA } else { "0" }
$exaApiKeyValue = if ($env:EXA_API_KEY) { $env:EXA_API_KEY } else { "your-exa-api-key" }

$baseEnvPath = Convert-ToTomlPath (Join-PathList -Segments $baseEnvSegments)
$mcpEnvPath = Convert-ToTomlPath (Join-PathList -Segments $mcpEnvSegments)
$reviewerEnvPath = $baseEnvPath

$codexHomeToml = Convert-ToTomlPath $codexHomeDir
$configTomlPath = Convert-ToTomlPath $configPath
$codexScriptsToml = Convert-ToTomlPath $codexScriptsDir
$condaTomlPath = Convert-ToTomlPath $condaPath
$npxTomlPath = Convert-ToTomlPath $npxPath
$pythonTomlPath = Convert-ToTomlPath $pythonPath
$reviewerRootToml = Convert-ToTomlPath $reviewerRoot
$codexBinaryToml = Convert-ToTomlPath $codexBinaryPath
$userHomeToml = Convert-ToTomlPath $userHome

New-Item -ItemType Directory -Force -Path $codexHomeDir, $codexBinDir, $codexScriptsDir, $codexSkillsDir | Out-Null

$configLines = @(
    "model = `"$codexModelValue`"",
    "model_reasoning_effort = `"$codexReasoningEffortValue`"",
    "",
    "# $codexHomeToml/config.toml multi-codex MCP config for Windows + PowerShell",
    "# Notes:",
    "# 1. MCP servers run through local conda or Python.",
    "# 2. Node-backed MCP servers use the current machine npx.",
    "# 3. code-index still needs uvx and is intentionally not included here.",
    "# 4. codex-reviewer uses the shared wrapper under ~/.codex/scripts/.",
    "# 5. The wrapper writes reviewer artifacts into the target repository .codex/ directory.",
    "",
    "[mcp_servers]",
    "",
    "[mcp_servers.sequential-thinking]",
    "type = `"stdio`"",
    "command = `"$condaTomlPath`"",
    "args = [`"run`", `"--no-capture-output`", `"-n`", `"base`", `"$npxTomlPath`", `"-y`", `"@modelcontextprotocol/server-sequential-thinking`"]",
    "env = { PATH = `"$mcpEnvPath`", HOME = `"$userHomeToml`", USERPROFILE = `"$userHomeToml`" }",
    "",
    "[mcp_servers.shrimp-task-manager]",
    "type = `"stdio`"",
    "command = `"$condaTomlPath`"",
    "args = [`"run`", `"--no-capture-output`", `"-n`", `"base`", `"$npxTomlPath`", `"-y`", `"mcp-shrimp-task-manager`"]",
    "env = { PATH = `"$mcpEnvPath`", HOME = `"$userHomeToml`", USERPROFILE = `"$userHomeToml`", DATA_DIR = `"$shrimpDataDirValue`", TEMPLATES_USE = `"zh`", ENABLE_GUI = `"false`" }",
    "",
    "[mcp_servers.codex-reviewer]",
    "type = `"stdio`"",
    "command = `"$pythonTomlPath`"",
    "args = [`"$codexScriptsToml/codex_reviewer_mcp.py`"]",
    "env = { PATH = `"$reviewerEnvPath`", HOME = `"$userHomeToml`", USERPROFILE = `"$userHomeToml`", CODEX_BINARY = `"$codexBinaryToml`", CODEX_REVIEWER_FRAMEWORK_ROOT = `"$reviewerRootToml`" }",
    "",
    "[mcp_servers.chrome-devtools]",
    "type = `"stdio`"",
    "command = `"$condaTomlPath`"",
    "args = [`"run`", `"--no-capture-output`", `"-n`", `"base`", `"$npxTomlPath`", `"chrome-devtools-mcp@latest`"]",
    "env = { PATH = `"$mcpEnvPath`", HOME = `"$userHomeToml`", USERPROFILE = `"$userHomeToml`" }"
)

if ($enableExaValue -eq "1") {
    $configLines += @(
        "",
        "[mcp_servers.exa]",
        "type = `"stdio`"",
        "command = `"$condaTomlPath`"",
        "args = [`"run`", `"--no-capture-output`", `"-n`", `"base`", `"$npxTomlPath`", `"-y`", `"exa-mcp-server`"]",
        "env = { PATH = `"$mcpEnvPath`", HOME = `"$userHomeToml`", USERPROFILE = `"$userHomeToml`", EXA_API_KEY = `"$exaApiKeyValue`" }"
    )
}

Set-Content -LiteralPath $configPath -Value ($configLines -join [Environment]::NewLine) -Encoding UTF8
Write-Host "Generated $configTomlPath"

$codexLatestSource = Join-Path $projectRoot "base\codex-latest.cmd"
$codexLatestDestination = Join-Path $codexBinDir "codex-latest.cmd"
Copy-Item -LiteralPath $codexLatestSource -Destination $codexLatestDestination -Force
Write-Host ("Copied codex-latest to {0}" -f (Convert-ToTomlPath $codexBinDir))

Copy-TreeContent -SourceDir (Join-Path $projectRoot "skills") -DestinationDir $codexSkillsDir
Write-Host ("Copied skills to {0}" -f (Convert-ToTomlPath $codexSkillsDir))

Copy-TreeContent -SourceDir (Join-Path $projectRoot ".scripts") -DestinationDir $codexScriptsDir
Write-Host ("Copied scripts to {0}" -f (Convert-ToTomlPath $codexScriptsDir))
