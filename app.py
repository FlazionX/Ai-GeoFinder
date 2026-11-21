from __future__ import annotations

import base64
import io
import os
import re
import webbrowser
from threading import Timer
from pathlib import Path

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from PIL import Image, UnidentifiedImageError

load_dotenv()
BASE_DIR = Path(__file__).resolve().parent
MAX_UPLOAD = 12 * 1024 * 1024
ALLOWED = {"JPEG", "PNG", "WEBP"}
app = Flask(__name__, template_folder=str(BASE_DIR / "templates"), static_folder=str(BASE_DIR / "static"))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD


def inspect_image(data: bytes) -> dict:
    """Validate and normalize an uploaded image without exposing EXIF coordinates."""
    try:
        image = Image.open(io.BytesIO(data))
        image_format = (image.format or "").upper()
        if image_format not in ALLOWED:
            raise ValueError("Поддерживаются только JPG, PNG и WEBP.")
        width, height = image.size
        if width < 64 or height < 64:
            raise ValueError("Изображение слишком маленькое. Минимум 64×64.")
        has_gps = False
        try:
            exif = image.getexif()
            gps = exif.get_ifd(0x8825) if exif else {}
            has_gps = bool(gps)
        except Exception:
            has_gps = False
        image.thumbnail((1600, 1600))
        clean = Image.new("RGB", image.size)
        if image.mode in ("RGBA", "LA"):
            rgba = image.convert("RGBA")
            clean.paste(rgba, mask=rgba.getchannel("A"))
        else:
            clean.paste(image.convert("RGB"))
        output = io.BytesIO()
        clean.save(output, format="JPEG", quality=88, optimize=True)
        return {"bytes": output.getvalue(), "width": width, "height": height,
                "format": image_format, "has_gps": has_gps}
    except UnidentifiedImageError as exc:
        raise ValueError("Не удалось прочитать изображение.") from exc


def analyze_with_gemini(image_bytes: bytes) -> dict:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Добавьте GEMINI_API_KEY в файл .env, чтобы включить ИИ-анализ.")
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    prompt = """Analyze this image for broad geographic clues. Return ONLY valid JSON with keys:
summary (one concise sentence), country_candidates (array of objects with country, region, confidence, clues),
visual_clues (array of strings), limitations (array of strings).
Confidence must be a rough heuristic from 0 to 100, not a statistical probability.
Never guess an exact address, private residence, person's identity, or precise coordinates.
Do not infer a person's location. Use visible public landmarks, architecture, vegetation, terrain,
road markings and language only. If evidence is weak, say so and keep candidates broad.
Do not use or request EXIF/GPS metadata."""
    payload = {
        "contents": [{"parts": [
            {"text": prompt},
            {"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(image_bytes).decode("ascii")}}
        ]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}
    }
    response = requests.post(endpoint, params={"key": api_key}, json=payload, timeout=90)
    if response.status_code >= 400:
        try:
            message = response.json().get("error", {}).get("message", response.text)
        except Exception:
            message = response.text
        raise RuntimeError(f"Ошибка Gemini API ({response.status_code}): {message[:500]}")
    result = response.json()
    try:
        import json
        raw = result["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError()
        return parsed
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise RuntimeError("ИИ вернул ответ в неожиданном формате. Попробуйте другое изображение.") from exc


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "ai_configured": bool(os.getenv("GEMINI_API_KEY", "").strip())})


@app.post("/api/analyze")
def analyze():
    uploaded = request.files.get("image")
    if not uploaded or not uploaded.filename:
        return jsonify({"error": "Выберите изображение для анализа."}), 400
    data = uploaded.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        return jsonify({"error": "Файл больше 12 МБ."}), 413
    try:
        checked = inspect_image(data)
        result = analyze_with_gemini(checked["bytes"])
        return jsonify({
            "result": result,
            "image": {"width": checked["width"], "height": checked["height"],
                      "format": checked["format"], "has_gps_metadata": checked["has_gps"]},
            "notice": "Это предположения по визуальным признакам, а не точное определение места."
        })
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except requests.RequestException:
        return jsonify({"error": "Не удалось связаться с Gemini API. Проверьте интернет и повторите попытку."}), 502


@app.errorhandler(413)
def too_large(_error):
    return jsonify({"error": "Файл больше 12 МБ."}), 413


def run_desktop():
    port = int(os.getenv("PORT", "5000"))
    url = f"http://127.0.0.1:{port}"
    Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    run_desktop()
