@echo off
setlocal EnableExtensions

set "LATEST_CODEX="
set /a COUNT=0

for %%R in ("%USERPROFILE%\.vscode\extensions" "%USERPROFILE%\.vscode-insiders\extensions") do (
  if exist "%%~R" (
    for /f "delims=" %%F in ('dir /b /s /a:-d /o-d "%%~R\openai.chatgpt-*\bin\win32-*\codex.exe" 2^>nul') do (
      set /a COUNT+=1
      if not defined LATEST_CODEX set "LATEST_CODEX=%%F"
    )
  )
)

if not defined LATEST_CODEX (
  >&2 echo codex-latest: no executable codex.exe found under VS Code extension directories
  exit /b 1
)

if %COUNT% GTR 1 (
  >&2 echo codex-latest: warning: multiple openai.chatgpt extension binaries were found
  >&2 echo codex-latest: selected %LATEST_CODEX%
)

"%LATEST_CODEX%" %*
