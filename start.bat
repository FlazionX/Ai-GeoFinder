@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (echo Python 3.11+ is required. & pause & exit /b 1)
py -3 -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if not exist .env copy .env.example .env
echo.
echo Launch the app, open the Настройка ИИ tab and save your Gemini API key.
pause >nul
python app.py
pause
