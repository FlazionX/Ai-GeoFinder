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


def test_analyze_without_local_ollama(monkeypatch):
    monkeypatch.setattr(project, "local_ai_status", lambda: {"available": False, "installed": False, "model": "qwen2.5vl:3b"})
    data = {"image": (io.BytesIO(make_image()), "test.jpg")}
    response = project.app.test_client().post("/api/analyze", data=data, content_type="multipart/form-data")
    assert response.status_code == 503
    assert "Ollama" in response.json["error"]


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



def test_gemini_key_is_saved_even_when_google_rejects_it(monkeypatch, tmp_path):
    monkeypatch.setattr(project, "APP_DIR", tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    def reject(_key):
        raise ValueError("Google отклонил ключ (API_KEY_INVALID).")
    monkeypatch.setattr(project, "verify_gemini_api_key", reject)
    api_key = "AQ." + "x" * 40
    response = project.app.test_client().post(
        "/api/settings/gemini", json={"api_key": api_key}
    )
    assert response.status_code == 200
    assert response.json["configured"] is True
    assert response.json["verified"] is False
    assert "API_KEY_INVALID" in response.json["warning"]
    assert "GEMINI_API_KEY" in (tmp_path / ".env").read_text(encoding="utf-8")
    assert project.get_gemini_api_key() == api_key


def test_verify_gemini_key_uses_native_api_key_header(monkeypatch):
    captured = {}
    class FakeResponse:
        status_code = 200
        def json(self):
            return {"status": "completed"}
    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()
    monkeypatch.setattr(project.SESSION, "post", fake_post)
    project.verify_gemini_api_key("AQ." + "x" * 40)
    assert captured["url"].endswith("/v1beta/interactions")
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
            return {"status": "completed", "steps": [{"type": "model_output", "content": [{"type": "text", "text": json.dumps({
                "summary": "Проверочный ответ",
                "country_candidates": [],
                "visual_clues": [],
                "limitations": [],
                "report": ""
            })}]}]}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(project.SESSION, "post", fake_post)
    monkeypatch.setattr(project, "geocode_candidate", lambda item: item)
    result = project.analyze_with_gemini(make_image())
    assert captured["url"].endswith("/v1beta/interactions")
    assert captured["json"]["model"] == "gemini-3.8-flash"
    assert captured["headers"]["Api-Revision"] == "2026-05-20"
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



def test_local_ai_status_endpoint(monkeypatch):
    monkeypatch.setattr(project, "local_ai_status", lambda: {"available": True, "installed": True, "model": "qwen2.5vl:3b"})
    response = project.app.test_client().get("/api/local-ai/status")
    assert response.status_code == 200
    assert response.json["installed"] is True


def test_local_model_analysis_sends_image_and_parses_json(monkeypatch):
    monkeypatch.setattr(project, "local_ai_status", lambda: {"available": True, "installed": True, "model": "qwen2.5vl:3b"})
    captured = {}
    class FakeResponse:
        def raise_for_status(self):
            pass
        def json(self):
            return {"message": {"content": json.dumps({
                "summary": "Похоже на горный регион.",
                "country_candidates": [{"country": "Example", "region": "North", "confidence": 40}],
                "visual_clues": ["Горы"], "limitations": ["Мало признаков"], "report": "Тест"
            }, ensure_ascii=False)}}
    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()
    monkeypatch.setattr(project.SESSION, "post", fake_post)
    monkeypatch.setattr(project, "geocode_candidate", lambda item: item)
    result = project.analyze_with_local_model(make_image())
    assert captured["url"].endswith("/api/chat")
    assert captured["json"]["model"] == "qwen2.5vl:3b"
    assert captured["json"]["stream"] is False
    assert captured["json"]["messages"][0]["images"]
    assert result["engine"] == "local"
    assert result["summary"] == "Похоже на горный регион."


def test_local_model_missing_is_actionable(monkeypatch):
    monkeypatch.setattr(project, "local_ai_status", lambda: {"available": True, "installed": False, "model": "qwen2.5vl:3b"})
    with pytest.raises(RuntimeError, match="Загрузить локальную модель"):
        project.analyze_with_local_model(make_image())



def test_model_storage_api_rejects_empty_path():
    response = project.app.test_client().post("/api/local-ai/storage", json={"path": ""})
    assert response.status_code == 400


def test_model_storage_path_requires_absolute_path(monkeypatch):
    monkeypatch.setattr(project.sys, "platform", "win32")
    with pytest.raises(ValueError, match="полный путь"):
        project.set_model_storage_path("relative-folder")

def test_folder_picker_uses_isolated_windows_dialog(monkeypatch):
    from types import SimpleNamespace
    import subprocess

    monkeypatch.setattr(project.sys, "platform", "win32")
    captured = {}
    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="D:\\\\AI Models\\\\Qwen", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)

    selected = project.DesktopBridge().choose_model_folder()
    assert selected == "D:\\\\AI Models\\\\Qwen"
    assert "-STA" in captured["args"]
    assert captured["kwargs"]["timeout"] == 120


def test_folder_picker_cancel_returns_empty(monkeypatch):
    from types import SimpleNamespace
    import subprocess

    monkeypatch.setattr(project.sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout="", stderr=""))
    assert project.DesktopBridge().choose_model_folder() == ""
