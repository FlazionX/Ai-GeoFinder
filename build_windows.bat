@echo off
setlocal
cd /d "%~dp0"
py -3 -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
pyinstaller --noconfirm --clean --onedir --windowed --name AI-GeoFinder --add-data "templates;templates" --add-data "static;static" app.py
if errorlevel 1 (echo BUILD FAILED & pause & exit /b 1)
echo Build complete: dist\AI-GeoFinder\AI-GeoFinder.exe
pause
