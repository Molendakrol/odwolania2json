@echo off
REM Uruchamia interfejs graficzny Odwołania2JSON i otwiera go w przeglądarce (http://127.0.0.1:8000).
REM Zamknięcie: Ctrl+C lub zamknięcie tego okna. Dodatkowe opcje, np.: start_gui.bat --port 8080
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -m odwolania2json.web --open %*
) else (
    python -m odwolania2json.web --open %*
)
