from __future__ import annotations

import base64
import hashlib
from contextlib import contextmanager
import io
import ipaddress
import json
import os
import re
import sqlite3
import sys
import time
import socket
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from werkzeug.serving import make_server

import requests
from dotenv import load_dotenv, set_key, unset_key
from flask import Flask, jsonify, render_template, request
from PIL import Image, UnidentifiedImageError

RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
load_dotenv(APP_DIR / ".env", override=True)  # The key saved in the app settings must take precedence over stale Windows environment variables.
DB_PATH = APP_DIR / "geofinder_history.sqlite3"
MAX_UPLOAD = 12 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
LEGACY_GEMINI_MODELS = {"gemini-2.5-flash": DEFAULT_GEMINI_MODEL}
ALLOWED = {"JPEG", "PNG", "WEBP"}
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "AI-GeoFinder/2.0 (desktop research tool)"})
app = Flask(__name__, template_folder=str(RESOURCE_DIR / "templates"), static_folder=str(RESOURCE_DIR / "static"))
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS investigations (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
            title TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL
        )""")


def save_history(kind: str, title: str, payload: dict):
    init_db()
    with db() as conn:
        conn.execute("INSERT INTO investigations(kind,title,created_at,payload) VALUES(?,?,?,?)",
                     (kind, title[:180], datetime.now(timezone.utc).isoformat(), json.dumps(payload, ensure_ascii=False)))


def get_exif_info(image: Image.Image) -> dict:
    exif = image.getexif()
    def tag(name):
        for key, value in exif.items():
            try:
                from PIL.ExifTags import TAGS
                if TAGS.get(key) == name:
                    return str(value)[:120]
            except Exception:
                pass
        return None
    gps_present = False
    try:
        gps_present = bool(exif.get_ifd(0x8825)) if exif else False
    except Exception:
        gps_present = False
    return {"camera_make": tag("Make"), "camera_model": tag("Model"),
            "captured_at": tag("DateTimeOriginal") or tag("DateTime"),
            "orientation": tag("Orientation"), "gps_metadata_present": gps_present}


def inspect_image(data: bytes) -> dict:
    try:
        image = Image.open(io.BytesIO(data))
        image_format = (image.format or "").upper()
        if image_format not in ALLOWED:
            raise ValueError("Поддерживаются только JPG, PNG и WEBP.")
        width, height = image.size
        if width < 64 or height < 64:
            raise ValueError("Изображение слишком маленькое. Минимум 64×64.")
        if width * height > MAX_IMAGE_PIXELS:
            raise ValueError("Изображение слишком большое по разрешению. Максимум 40 мегапикселей.")
        metadata = get_exif_info(image)
        image.thumbnail((1800, 1800))
        clean = Image.new("RGB", image.size)
        if image.mode in ("RGBA", "LA"):
            rgba = image.convert("RGBA")
            clean.paste(rgba, mask=rgba.getchannel("A"))
        else:
            clean.paste(image.convert("RGB"))
        output = io.BytesIO()
        clean.save(output, format="JPEG", quality=90, optimize=True)
        return {"bytes": output.getvalue(), "width": width, "height": height,
                "format": image_format, "size_bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(), "metadata": metadata}
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Не удалось прочитать изображение или оно повреждено.") from exc


def geocode_candidate(candidate: dict) -> dict:
    country = str(candidate.get("country", "")).strip()[:100]
    region = str(candidate.get("region", "")).strip()[:100]
    if not country and not region:
        return {**candidate, "map_match": None}
    query = ", ".join(x for x in (region, country) if x)
    try:
        time.sleep(1.05)  # Respect the public Nominatim one-request-per-second policy.
        response = SESSION.get("https://nominatim.openstreetmap.org/search",
                               params={"q": query, "format": "jsonv2", "limit": 1,
                                       "addressdetails": 0}, timeout=12)
        response.raise_for_status()
        hits = response.json()
        if hits:
            hit = hits[0]
            return {**candidate, "map_match": {
                "lat": float(hit["lat"]), "lon": float(hit["lon"]),
                "label": str(hit.get("display_name", query))[:220],
                "source": "OpenStreetMap / Nominatim"}}
    except (requests.RequestException, ValueError, KeyError, TypeError):
        pass
    return {**candidate, "map_match": None}


def get_gemini_api_key() -> str:
    """Ignore template placeholders so the UI never reports a fake configured key."""
    key = os.getenv("GEMINI_API_KEY", "").strip()
    if key.lower() in {"your_gemini_api_key_here", "your_api_key_here", "your_key", "replace_me"}:
        return ""
    return key


def get_gemini_model() -> str:
    """Return a supported model and transparently migrate the retired default."""
    configured = os.getenv("GEMINI_MODEL", "").strip()
    model = configured.removeprefix("models/") or DEFAULT_GEMINI_MODEL
    if model in LEGACY_GEMINI_MODELS:
        model = LEGACY_GEMINI_MODELS[model]
        os.environ["GEMINI_MODEL"] = model
        env_path = APP_DIR / ".env"
        if env_path.exists():
            try:
                set_key(str(env_path), "GEMINI_MODEL", model, quote_mode="never")
            except OSError:
                # Runtime migration still works even if the folder is read-only.
                pass
    return model


def interaction_output_text(data: dict) -> str:
    """Extract model text from the REST Interactions API response."""
    chunks = []
    for step in data.get("steps", []):
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        for part in step.get("content", []):
            if isinstance(part, dict) and part.get("type") == "text":
                chunks.append(str(part.get("text", "")))
    if not chunks:
        raise RuntimeError("Gemini не вернул текстовый результат. Попробуйте ещё раз.")
    return "\n".join(chunks).strip()


def analyze_with_gemini(image_bytes: bytes, deep_scan: bool = False) -> dict:
    api_key = get_gemini_api_key()
    if not api_key:
        raise RuntimeError("Добавьте действительный Gemini API-ключ в раздел «Настройка ИИ», чтобы включить анализ.")
    model = get_gemini_model()
    endpoint = "https://generativelanguage.googleapis.com/v1beta/interactions"
    depth = ("Perform a deeper multi-pass style reasoning: compare competing regions, identify supporting and contradicting clues, "
             "give up to 5 broad candidate regions, and provide a detailed report.") if deep_scan else (
             "Perform a focused first-pass analysis and provide up to 3 broad candidate regions.")
    prompt = f"""Analyze this photograph for broad geographic clues. {depth}
Return ONLY valid JSON with keys:
summary (one concise sentence), country_candidates (array of objects with country, region, confidence, clues, counter_clues),
visual_clues (array of strings), limitations (array of strings), report (string).
Confidence is a rough heuristic from 0 to 100, never a statistical probability.
Use visible public landmarks, architecture, signs, road markings, vegetation and terrain.
Do not invent a landmark, coordinates, exact address, private residence, person's identity or whereabouts.
Do not infer a person's location. If evidence is weak, state that clearly.
Do not use or request EXIF/GPS metadata. Avoid exact/private locations."""
    payload = {"model": model, "input": [
        {"type": "text", "text": prompt},
        {"type": "image", "data": base64.b64encode(image_bytes).decode("ascii"), "mime_type": "image/jpeg"}
    ]}
    response = SESSION.post(endpoint, headers={
        "x-goog-api-key": api_key,
        "Api-Revision": "2026-05-20",
        "Content-Type": "application/json"
    }, json=payload, timeout=100)
    if response.status_code >= 400:
        try:
            message = response.json().get("error", {}).get("message", response.text)
        except Exception:
            message = response.text
        raise RuntimeError(f"Ошибка Gemini API ({response.status_code}): {message[:450]}")
    try:
        data = response.json()
        result = interaction_output_text(data)
        parsed = json.loads(result)
        if not isinstance(parsed, dict):
            raise ValueError()
        candidates = parsed.get("country_candidates", [])
        parsed["country_candidates"] = [geocode_candidate(x) for x in candidates[:5] if isinstance(x, dict)]
        parsed["deep_scan"] = deep_scan
        return parsed
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        raise RuntimeError("ИИ вернул ответ в неожиданном формате. Попробуйте другое изображение.") from exc


# Local-first vision AI powered by Ollama. No cloud API key is required.
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
LOCAL_VISION_MODEL = os.getenv("LOCAL_VISION_MODEL", "qwen2.5vl:3b")


def local_ai_status() -> dict:
    try:
        response = SESSION.get(f"{OLLAMA_HOST}/api/tags", timeout=2.5)
        response.raise_for_status()
        models = response.json().get("models", [])
        installed = any(str(item.get("name", "")).split(":")[0] == LOCAL_VISION_MODEL.split(":")[0]
                        and (":" not in LOCAL_VISION_MODEL or str(item.get("name", "")).endswith(LOCAL_VISION_MODEL.split(":", 1)[1]))
                        for item in models if isinstance(item, dict))
        return {"available": True, "installed": installed, "model": LOCAL_VISION_MODEL,
                "host": OLLAMA_HOST, "message": "Локальная нейросеть готова." if installed else "Ollama запущен. Нужно загрузить модель."}
    except (requests.RequestException, ValueError, TypeError):
        return {"available": False, "installed": False, "model": LOCAL_VISION_MODEL,
                "host": OLLAMA_HOST, "message": "Ollama не найден. Установи и запусти Ollama для Windows."}


def analyze_with_local_model(image_bytes: bytes, deep_scan: bool = False) -> dict:
    status = local_ai_status()
    if not status["available"]:
        raise RuntimeError("Локальная нейросеть не запущена. Установи Ollama для Windows, запусти его и нажми «Проверить подключение».")
    if not status["installed"]:
        raise RuntimeError(f"Модель {LOCAL_VISION_MODEL} ещё не загружена. Открой «Настройка ИИ» и нажми «Загрузить локальную модель» (около 3 ГБ).")
    depth = ("Сделай углублённое сравнение конкурирующих регионов, найди подтверждающие и противоречащие признаки, предложи до 5 широких регионов и подробный отчёт."
             if deep_scan else "Сделай первичный анализ и предложи до 3 широких возможных регионов.")
    prompt = f"""Ты — осторожная система визуального географического анализа. {depth}
Верни ТОЛЬКО корректный JSON со следующими ключами:
summary (одно короткое предложение), country_candidates (массив объектов country, region, confidence, clues, counter_clues),
visual_clues (массив строк), limitations (массив строк), report (строка).
confidence — эвристическая оценка от 0 до 100, НЕ статистическая вероятность.
Используй только видимые публичные ориентиры, архитектуру, надписи, дорожную разметку, растительность и рельеф.
Не выдумывай ориентиры, координаты, точный адрес, частное жильё, личность или местонахождение человека.
Не делай выводов о местонахождении человека. Если признаков мало — прямо скажи об этом.
Не используй EXIF/GPS и не пытайся определить точное или частное местоположение."""
    payload = {"model": LOCAL_VISION_MODEL, "messages": [{"role": "user", "content": prompt,
                "images": [base64.b64encode(image_bytes).decode("ascii")]}],
               "stream": False, "format": "json", "keep_alive": "10m",
               "options": {"temperature": 0.2, "num_ctx": 4096}}
    try:
        response = SESSION.post(f"{OLLAMA_HOST}/api/chat", json=payload, timeout=(10, 240))
        response.raise_for_status()
        raw = response.json().get("message", {}).get("content", "")
    except requests.Timeout as exc:
        raise RuntimeError("Локальная нейросеть отвечает слишком долго. Попробуй ещё раз или отключи Deep Scan.") from exc
    except requests.RequestException as exc:
        raise RuntimeError("Не удалось связаться с Ollama. Убедись, что Ollama запущен на этом компьютере.") from exc
    try:
        raw = str(raw).strip()
        if raw.startswith("```"):
            raw = re.sub(r"^\\```(?:json)?\\s*|\\s*\\```$", "", raw, flags=re.IGNORECASE)
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError()
        candidates = parsed.get("country_candidates", [])
        parsed["country_candidates"] = [geocode_candidate(x) for x in candidates[:5] if isinstance(x, dict)]
        parsed["deep_scan"] = deep_scan
        parsed["engine"] = "local"
        parsed["model"] = LOCAL_VISION_MODEL
        return parsed
    except (ValueError, TypeError, AttributeError) as exc:
        raise RuntimeError("Локальная нейросеть вернула ответ в неожиданном формате. Повтори анализ.") from exc


def lookup_ip(value: str) -> dict:
    value = value.strip()
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError("Введите корректный IPv4 или IPv6 адрес.") from exc
    if not parsed.is_global:
        raise ValueError("Частные, локальные и зарезервированные IP-адреса нельзя геолоцировать.")
    response = SESSION.get(f"https://ipwho.is/{value}", timeout=15)
    response.raise_for_status()
    data = response.json()
    if not data.get("success", False):
        raise ValueError(data.get("message", "Сервис не смог определить этот IP-адрес."))
    connection = data.get("connection") or {}
    timezone_data = data.get("timezone") or {}
    return {
        "ip": value, "type": "IPv4" if parsed.version == 4 else "IPv6",
        "country": data.get("country"), "country_code": data.get("country_code"),
        "region": data.get("region"), "city": data.get("city"),
        "postal": data.get("postal"), "latitude": data.get("latitude"),
        "longitude": data.get("longitude"), "isp": connection.get("isp"),
        "organization": connection.get("org"), "asn": connection.get("asn"),
        "timezone": timezone_data.get("id"),
        "map_url": (f"https://www.openstreetmap.org/?mlat={data.get('latitude')}&mlon={data.get('longitude')}#map=8/{data.get('latitude')}/{data.get('longitude')}"
                    if data.get("latitude") is not None and data.get("longitude") is not None else None),
        "accuracy_note": "IP-геолокация приблизительна: обычно показывает сеть или узел провайдера, а не точное местоположение устройства.",
        "source": "ipwho.is"
    }


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "ai_configured": bool(get_gemini_api_key()), "local_ai": local_ai_status()})


def verify_gemini_api_key(api_key: str) -> None:
    """Verify the key using Google's current Interactions API before saving it."""
    model = get_gemini_model()
    endpoint = "https://generativelanguage.googleapis.com/v1beta/interactions"
    try:
        response = SESSION.post(
            endpoint,
            headers={"x-goog-api-key": api_key, "Api-Revision": "2026-05-20",
                     "Content-Type": "application/json"},
            json={"model": model, "input": "Reply with OK only."},
            timeout=12,
        )
    except requests.Timeout as exc:
        raise RuntimeError("Проверка ключа заняла слишком много времени. Проверь интернет и повтори попытку.") from exc
    except requests.RequestException as exc:
        raise RuntimeError("Не удалось связаться с Google Gemini. Проверь интернет и повтори попытку.") from exc
    if response.status_code >= 400:
        try:
            error = response.json().get("error", {})
            message = str(error.get("message", response.text))
            details = error.get("details", [])
            reasons = [str(item.get("reason")) for item in details if isinstance(item, dict) and item.get("reason")]
        except (ValueError, AttributeError, TypeError):
            message, reasons = response.text, []
        lowered = " ".join(reasons + [message]).lower()
        if "api_key_invalid" in lowered or "api key not valid" in lowered:
            raise ValueError("Google отклонил ключ (API_KEY_INVALID). Убедись, что скопирован полный ключ из AI Studio. Если ключ начинается с AQ., он должен использоваться через Interactions API; приложение теперь проверяет именно этот интерфейс.")
        if response.status_code == 403 or "api_key_service_blocked" in lowered:
            raise ValueError("Google распознал ключ, но запретил доступ к Gemini API. Проверь настройки проекта и ограничения ключа в Google AI Studio.")
        if response.status_code in (401,):
            raise ValueError("Google не принял этот тип авторизации. Создай новый ключ через Google AI Studio → API Keys и используй именно его.")
        if response.status_code == 404:
            raise RuntimeError(f"Модель {model} или Interactions API недоступны этому проекту: {message[:220]}")
        raise RuntimeError(f"Проверка Gemini не пройдена ({response.status_code}): {message[:300]}")
    try:
        data = response.json()
        if data.get("status") not in {"completed", "in_progress", "queued"}:
            raise ValueError()
    except (ValueError, AttributeError):
        raise RuntimeError("Google получил ключ, но вернул неожиданный ответ при проверке. Попробуй ещё раз.")

@app.get("/api/local-ai/status")
def local_ai_status_endpoint():
    return jsonify(local_ai_status())


@app.post("/api/local-ai/pull")
def pull_local_ai_model():
    status = local_ai_status()
    if not status["available"]:
        return jsonify({"error": "Ollama не запущен. Сначала установи и запусти Ollama для Windows."}), 503
    if status["installed"]:
        return jsonify({"ok": True, "installed": True, "message": "Модель уже загружена."})
    try:
        response = SESSION.post(f"{OLLAMA_HOST}/api/pull", json={"name": LOCAL_VISION_MODEL, "stream": False}, timeout=(10, 1800))
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "success":
            raise RuntimeError(str(data.get("error") or data.get("status") or "Загрузка модели не завершена.")[:250])
        return jsonify({"ok": True, "installed": True, "message": f"Модель {LOCAL_VISION_MODEL} загружена."})
    except requests.Timeout:
        return jsonify({"error": "Загрузка модели превысила лимит ожидания. Проверь Ollama — загрузка могла продолжиться в фоне."}), 504
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        return jsonify({"error": f"Не удалось загрузить модель: {str(exc)[:300]}"}), 502


@app.post("/api/settings/gemini")
def save_gemini_settings():
    """Persist the key locally first; a temporary/Google validation failure must not discard it."""
    payload = request.get_json(silent=True) or {}
    api_key = str(payload.get("api_key", "")).strip().strip('"').strip("'")
    if len(api_key) < 20 or len(api_key) > 512 or any(ch.isspace() for ch in api_key):
        return jsonify({"error": "Проверь API-ключ. Вставь его полностью, без пробелов, кавычек и переносов строк."}), 400

    env_path = APP_DIR / ".env"
    try:
        env_path.parent.mkdir(parents=True, exist_ok=True)
        set_key(str(env_path), "GEMINI_API_KEY", api_key, quote_mode="always")
        load_dotenv(env_path, override=True)
        if get_gemini_api_key() != api_key:
            raise OSError("Не удалось подтвердить запись ключа в .env.")
        os.environ["GEMINI_API_KEY"] = api_key
    except (OSError, PermissionError) as exc:
        return jsonify({"error": f"Не удалось сохранить ключ в {env_path}. Закрой программу и запусти её из папки, где есть права на запись. Детали: {str(exc)[:160]}"}), 500

    # Keep the saved key even when Google rejects it or is temporarily unreachable.
    try:
        verify_gemini_api_key(api_key)
        return jsonify({"ok": True, "configured": True, "verified": True,
                        "message": "Ключ сохранён и проверен Google."})
    except (ValueError, RuntimeError) as exc:
        return jsonify({"ok": True, "configured": True, "verified": False,
                        "message": "Ключ сохранён на компьютере, но Google пока не подтвердил подключение.",
                        "warning": str(exc)[:350]})


@app.delete("/api/settings/gemini")
def remove_gemini_settings():
    env_path = APP_DIR / ".env"
    try:
        if env_path.exists():
            unset_key(str(env_path), "GEMINI_API_KEY")
        os.environ.pop("GEMINI_API_KEY", None)
        return jsonify({"ok": True, "configured": False, "message": "API-ключ удалён с этого компьютера."})
    except OSError:
        return jsonify({"error": "Не удалось изменить .env. Проверь права на запись в папку программы."}), 500


@app.post("/api/analyze")
def analyze():
    uploaded = request.files.get("image")
    if not uploaded or not uploaded.filename:
        return jsonify({"error": "Выберите изображение для анализа."}), 400
    original_data = uploaded.read(MAX_UPLOAD + 1)
    if len(original_data) > MAX_UPLOAD:
        return jsonify({"error": "Файл больше 12 МБ."}), 413
    analysis_upload = request.files.get("analysis_image")
    analysis_data = analysis_upload.read(MAX_UPLOAD + 1) if analysis_upload else original_data
    if len(analysis_data) > MAX_UPLOAD:
        return jsonify({"error": "Обрезанное изображение слишком большое."}), 413
    deep_scan = request.form.get("deep_scan", "false").lower() == "true"
    try:
        checked = inspect_image(original_data)
        analysis_checked = inspect_image(analysis_data)
        result = analyze_with_local_model(analysis_checked["bytes"], deep_scan)
        response_data = {"result": result, "image": {
            "width": checked["width"], "height": checked["height"],
            "format": checked["format"], "size_bytes": checked["size_bytes"],
            "sha256": checked["sha256"], "metadata": checked["metadata"]},
            "notice": "Анализ выполнен локальной нейросетью на этом компьютере. Это географические гипотезы по визуальным признакам, а не подтверждённая геолокация."}
        save_history("photo", uploaded.filename, response_data)
        return jsonify(response_data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 503
    except requests.RequestException:
        return jsonify({"error": "Не удалось связаться с внешним сервисом. Проверьте интернет и повторите попытку."}), 502


@app.post("/api/ip-lookup")
def ip_lookup():
    value = (request.json or {}).get("ip", "")
    try:
        result = lookup_ip(value)
        save_history("ip", value, result)
        return jsonify({"result": result})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except requests.RequestException:
        return jsonify({"error": "Сервис IP-геолокации временно недоступен."}), 502


@app.get("/api/history")
def history():
    init_db()
    with db() as conn:
        rows = conn.execute("SELECT id,kind,title,created_at,payload FROM investigations ORDER BY id DESC LIMIT 50").fetchall()
    return jsonify({"items": [{"id": r["id"], "kind": r["kind"], "title": r["title"],
                              "created_at": r["created_at"], "payload": json.loads(r["payload"])} for r in rows]})


@app.delete("/api/history/<int:item_id>")
def delete_history(item_id):
    init_db()
    with db() as conn:
        cursor = conn.execute("DELETE FROM investigations WHERE id=?", (item_id,))
    return jsonify({"deleted": cursor.rowcount > 0})


@app.errorhandler(413)
def too_large(_error):
    return jsonify({"error": "Файл больше 12 МБ."}), 413


def run_desktop():
    """Launch Flask privately and show it inside a native Windows WebView window."""
    try:
        import webview
    except ImportError as exc:
        raise RuntimeError("Не найден модуль pywebview. Переустановите приложение.") from exc

    init_db()
    requested_port = int(os.getenv("PORT", "0"))
    server = make_server("127.0.0.1", requested_port, app, threaded=True)
    port = server.server_port
    server_thread = Thread(target=server.serve_forever, name="GeoFinderServer", daemon=True)
    server_thread.start()
    url = f"http://127.0.0.1:{port}"

    try:
        webview.create_window(
            "AI GeoFinder",
            url,
            width=1480,
            height=940,
            min_size=(1024, 680),
            resizable=True,
            text_select=True,
            background_color="#090d12",
        )
        webview.start(gui="edgechromium", debug=False)
    finally:
        server.shutdown()


if __name__ == "__main__":
    run_desktop()
