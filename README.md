# AI GeoFinder 2.0

Native Windows desktop application with a GeoSpy-inspired global map, photo intelligence, public-IP lookup and local investigation history. The EXE opens in its own application window using Microsoft Edge WebView2; it does not open a normal browser tab.

## Desktop interface
- The world map is the main workspace and opens at a global view.
- After photo analysis, candidate regions appear as animated map markers; the map automatically frames the candidate areas.
- The interface runs in a native resizable Windows window through pywebview and Edge WebView2.
- Windows may require the Microsoft Edge WebView2 Runtime to be installed.

## Modules

### Photo Intelligence
- JPG, PNG and WEBP upload with drag-and-drop preview (12 MB maximum).
- Optional crop selection with mouse or touch.
- Gemini visual reasoning for public landmarks, architecture, signs, road markings, vegetation and terrain.
- Deep Scan asks Gemini for a deeper comparison of competing regions, supporting/contradicting clues and a longer report.
- Candidate regions are looked up through OpenStreetMap Nominatim. Map markers are approximate region/country matches, not the photo's verified location.
- Image metadata panel: pixel dimensions, format, file size, SHA-256, camera make/model, capture date and whether GPS metadata exists. GPS coordinates are deliberately not displayed or sent to Gemini.

### IP Lookup
- IPv4 and IPv6 validation.
- Rejects private, loopback, link-local, multicast and reserved IP addresses.
- Public IP lookup returns country, region, city, postal code, ISP, organization, ASN, timezone and approximate coordinates when available.
- Results are mapped and saved to local history.
- IP data comes from ipwho.is. IP geolocation normally identifies an ISP network/exit node, not the exact device or person.

### Investigation History
- Recent photo and IP lookups are stored in a local SQLite database beside the app.
- View and delete individual history entries.

## Setup
Requires Python 3.11+ and a Gemini API key.

1. Download/clone the repository.
2. Run `start.bat`.
3. Open `.env` and set `GEMINI_API_KEY=your_key`.
4. Restart `start.bat`.

The app binds only to `127.0.0.1`.

## Build Windows EXE
Run `build_windows.bat` on Windows. The distributable folder is `dist/AI-GeoFinder/`; keep the entire folder together. Put your `.env` file next to the EXE. The EXE is a native desktop launcher, not a shortcut to Chrome.

GitHub Actions also builds the Windows application on every relevant push to `main` and on manual dispatch. Download the `AI-GeoFinder-Windows` artifact from the successful run under **Actions → Build Windows EXE**.

## Tests
```powershell
pip install -r requirements.txt
pip install pytest
pytest -q
```

## Configuration
```env
GEMINI_API_KEY=your_key
GEMINI_MODEL=gemini-2.5-flash
PORT=5000
```
Never commit a real API key. The `.env` file is ignored by Git.

## Privacy and limitations
- Image pixels are sent to Google Gemini only when the user presses Analyze. Review Google's API terms before use.
- The app reads only non-coordinate EXIF fields for the metadata panel. GPS coordinates are not exposed or transmitted to the AI.
- Photo results are broad visual hypotheses, not verified geolocation. Confidence is heuristic, not a statistical probability.
- The app must not be used to identify a private person's whereabouts or pinpoint a private residence.
- IP geolocation is approximate and may be inaccurate, especially with VPNs, mobile networks, proxies and corporate gateways.
- IP address lookups are sent to ipwho.is. Map tiles/geocoding use OpenStreetMap services.
- Internet access is required for Gemini, IP lookup, geocoding and map tiles.
