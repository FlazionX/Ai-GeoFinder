import io
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
import pytest
import app as project


def make_image(fmt="JPEG", size=(128, 96)):
    image = Image.new("RGB", size, (80, 120, 90))
    out = io.BytesIO()
    image.save(out, format=fmt)
    return out.getvalue()


def test_health_endpoint():
    response = project.app.test_client().get("/api/health")
    assert response.status_code == 200 and response.json["ok"] is True


def test_home_page():
    response = project.app.test_client().get("/")
    assert response.status_code == 200
    assert b"IP Lookup" in response.data


def test_inspect_valid_jpeg():
    result = project.inspect_image(make_image())
    assert result["format"] == "JPEG" and result["width"] == 128
    assert result["height"] == 96 and result["bytes"].startswith(bytes([255, 216]))
    assert len(result["sha256"]) == 64


def test_inspect_png():
    assert project.inspect_image(make_image("PNG"))["format"] == "PNG"


def test_reject_gif():
    with pytest.raises(ValueError):
        project.inspect_image(make_image("GIF"))


def test_reject_tiny_image():
    with pytest.raises(ValueError):
        project.inspect_image(make_image(size=(20, 20)))


def test_analyze_without_file():
    assert project.app.test_client().post("/api/analyze").status_code == 400


def test_analyze_without_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    data = {"image": (io.BytesIO(make_image()), "test.jpg")}
    response = project.app.test_client().post("/api/analyze", data=data, content_type="multipart/form-data")
    assert response.status_code == 503
    assert "API-ключ" in response.json["error"]


@pytest.mark.parametrize("ip", ["127.0.0.1", "192.168.1.1", "::1", "10.0.0.1", "169.254.1.1"])
def test_private_ip_blocked(ip):
    with pytest.raises(ValueError):
        project.lookup_ip(ip)


def test_invalid_ip_rejected():
    with pytest.raises(ValueError):
        project.lookup_ip("not-an-ip")


def test_ip_endpoint_invalid_input():
    response = project.app.test_client().post("/api/ip-lookup", json={"ip": "invalid"})
    assert response.status_code == 400


def test_history_save_and_delete(monkeypatch, tmp_path):
    monkeypatch.setattr(project, "DB_PATH", tmp_path / "history.sqlite3")
    project.save_history("ip", "8.8.8.8", {"country": "Example"})
    client = project.app.test_client()
    response = client.get("/api/history")
    assert response.status_code == 200
    assert len(response.json["items"]) == 1
    item_id = response.json["items"][0]["id"]
    deleted = client.delete(f"/api/history/{item_id}")
    assert deleted.status_code == 200 and deleted.json["deleted"] is True
    assert client.get("/api/history").json["items"] == []


def test_save_and_remove_gemini_key(monkeypatch, tmp_path):
    monkeypatch.setattr(project, "APP_DIR", tmp_path)
    monkeypatch.setattr(project, "verify_gemini_api_key", lambda key: None)
    client = project.app.test_client()
    response = client.post("/api/settings/gemini", json={"api_key": "AIza" + "x" * 36})
    assert response.status_code == 200
    assert "AIza" not in response.get_data(as_text=True)
    assert "GEMINI_API_KEY=" in (tmp_path / ".env").read_text()
    assert project.os.environ["GEMINI_API_KEY"] == "AIza" + "x" * 36
    removed = client.delete("/api/settings/gemini")
    assert removed.status_code == 200
    assert "GEMINI_API_KEY" not in project.os.environ



def test_gemini_key_is_not_saved_when_google_rejects_it(monkeypatch, tmp_path):
    monkeypatch.setattr(project, "APP_DIR", tmp_path)
    def reject(_key):
        raise ValueError("Google отклонил ключ (API_KEY_INVALID).")
    monkeypatch.setattr(project, "verify_gemini_api_key", reject)
    response = project.app.test_client().post(
        "/api/settings/gemini", json={"api_key": "AQ." + "x" * 40}
    )
    assert response.status_code == 400
    assert "API_KEY_INVALID" in response.json["error"]
    assert not (tmp_path / ".env").exists()


def test_verify_gemini_key_uses_native_api_key_header(monkeypatch):
    captured = {}
    class FakeResponse:
        status_code = 200
    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()
    monkeypatch.setattr(project.SESSION, "post", fake_post)
    project.verify_gemini_api_key("AQ." + "x" * 40)
    assert "generativelanguage.googleapis.com" in captured["url"]
    assert captured["headers"]["x-goog-api-key"] == "AQ." + "x" * 40
    assert "Authorization" not in captured["headers"]


def test_reject_invalid_gemini_key():
    response = project.app.test_client().post("/api/settings/gemini", json={"api_key": "short"})
    assert response.status_code == 400



def test_reject_image_over_pixel_limit(monkeypatch):
    monkeypatch.setattr(project, "MAX_IMAGE_PIXELS", 1000)
    with pytest.raises(ValueError, match="40 мегапикселей"):
        project.inspect_image(make_image(size=(64, 64)))


def test_delete_history_when_database_does_not_exist(monkeypatch, tmp_path):
    monkeypatch.setattr(project, "DB_PATH", tmp_path / "new-history.sqlite3")
    response = project.app.test_client().delete("/api/history/123")
    assert response.status_code == 200
    assert response.json == {"deleted": False}


def test_legacy_gemini_model_is_migrated_and_used(monkeypatch, tmp_path):
    monkeypatch.setattr(project, "APP_DIR", tmp_path)
    (tmp_path / ".env").write_text("GEMINI_MODEL=gemini-2.5-flash\\n", encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "AIza" + "x" * 36)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": json.dumps({
                "summary": "Проверочный ответ",
                "country_candidates": [],
                "visual_clues": [],
                "limitations": [],
                "report": ""
            })}]}}]}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(project.SESSION, "post", fake_post)
    monkeypatch.setattr(project, "geocode_candidate", lambda item: item)
    result = project.analyze_with_gemini(make_image())
    assert "models/gemini-3.8-flash:generateContent" in captured["url"]
    assert captured["headers"]["x-goog-api-key"] == "AIza" + "x" * 36
    assert project.os.environ["GEMINI_MODEL"] == "gemini-3.8-flash"
    assert "GEMINI_MODEL=gemini-3.8-flash" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert result["summary"] == "Проверочный ответ"


def test_gemini_api_error_is_reported(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIza" + "x" * 36)
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")

    class FakeResponse:
        status_code = 404
        text = "not found"

        def json(self):
            return {"error": {"message": "model not found"}}

    monkeypatch.setattr(project.SESSION, "post", lambda *args, **kwargs: FakeResponse())
    with pytest.raises(RuntimeError, match="Gemini API \\(404\\): model not found"):
        project.analyze_with_gemini(make_image())



def test_example_gemini_key_is_not_marked_configured(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "your_gemini_api_key_here")
    response = project.app.test_client().get("/api/health")
    assert response.status_code == 200
    assert response.json["ai_configured"] is False
    assert project.get_gemini_api_key() == ""
