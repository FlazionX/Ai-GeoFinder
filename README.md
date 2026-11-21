# AI GeoFinder

AI GeoFinder — desktop-friendly web application that analyzes an uploaded photograph and suggests broad possible geographic regions using Google Gemini.

## Features
- Dark responsive interface with drag-and-drop image upload and preview.
- JPG, PNG and WEBP validation; 12 MB upload limit.
- Gemini visual analysis of architecture, landscape, vegetation and other visible clues.
- Multiple country/region hypotheses with heuristic confidence scores.
- EXIF GPS is detected only as a metadata-presence flag. GPS coordinates are never shown or sent to Gemini.
- Local-only Flask server bound to 127.0.0.1.
- Windows build through PyInstaller and GitHub Actions.

## Requirements
- Python 3.11 or newer
- A Gemini API key from Google AI Studio

## Run on Windows
1. Download or clone this repository.
2. Double-click `start.bat`.
3. Open `.env` and paste your key after `GEMINI_API_KEY=`.
4. Run `start.bat` again. The app opens in your browser.

## Build EXE locally
Run `build_windows.bat`. The application will be created in `dist/AI-GeoFinder/`. Keep the complete folder together; the EXE uses bundled templates and static assets.

## GitHub Actions build
Every push to `main` that changes app files, or a manually started workflow, builds the Windows application on a Windows runner and uploads `AI-GeoFinder-Windows` as a downloadable workflow artifact. Open **Actions → Build Windows EXE → latest successful run → Artifacts**.

## Configuration
Create a `.env` file:

```env
GEMINI_API_KEY=your_key
GEMINI_MODEL=gemini-2.5-flash
PORT=5000
```

Never commit your real API key. The repository ignores `.env`.

## Tests
```powershell
pip install pytest
pytest -q
```

## Important limitations and privacy
This tool produces broad visual hypotheses, not verified geolocation. Confidence values are heuristic scores, not statistical probabilities. It must not be used to identify a private person's exact whereabouts or a private residence. Do not treat visual clues such as shadows as reliable proof of a specific time or place. Uploaded image pixels are sent to Google Gemini when analysis is requested; review Google's API terms and privacy settings before use. EXIF GPS coordinates are not included in the AI request.
