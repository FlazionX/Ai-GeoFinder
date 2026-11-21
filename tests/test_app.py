import io
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
    assert response.status_code == 200
    assert response.json["ok"] is True


def test_home_page():
    response = project.app.test_client().get("/")
    assert response.status_code == 200
    assert b"AI GEO" in response.data


def test_inspect_valid_jpeg():
    result = project.inspect_image(make_image())
    assert result["format"] == "JPEG"
    assert result["width"] == 128
    assert result["height"] == 96
    assert result["bytes"].startswith(b"\xff\xd8")


def test_inspect_png():
    result = project.inspect_image(make_image("PNG"))
    assert result["format"] == "PNG"


def test_reject_gif():
    with pytest.raises(ValueError):
        project.inspect_image(make_image("GIF"))


def test_reject_tiny_image():
    with pytest.raises(ValueError):
        project.inspect_image(make_image(size=(20, 20)))


def test_analyze_without_file():
    response = project.app.test_client().post("/api/analyze")
    assert response.status_code == 400


def test_analyze_without_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    data = {"image": (io.BytesIO(make_image()), "test.jpg")}
    response = project.app.test_client().post("/api/analyze", data=data, content_type="multipart/form-data")
    assert response.status_code == 503
    assert "GEMINI_API_KEY" in response.json["error"]
