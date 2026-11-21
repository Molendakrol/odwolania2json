@echo off
REM Przetwarza wszystkie PDF z .\input: JSON-y do .\output, diagnostyka do .\diagnostics.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m odwolania2json run --input input --output output --diagnostics diagnostics %*
) else (
    python -m odwolania2json run --input input --output output --diagnostics diagnostics %*
)
