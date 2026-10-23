from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.app.seats import router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client


def test_stale_candidates_are_removed(client):
    with patch("backend.app.seats._request", return_value=b'{"status":"ok","age_seconds":8,"chairs":[{"id":1}],"candidate_count":1}'):
        result = client.get("/api/seats/status")
    assert result.status_code == 200
    assert result.json()["status"] == "error"
    assert result.json()["chairs"] == []
    assert result.json()["candidate_count"] == 0


def test_unavailable_detector_returns_actionable_error(client):
    with patch("backend.app.seats._request", side_effect=HTTPException(503, "空位识别服务未连接")):
        result = client.post("/api/seats/find")
    assert result.status_code == 503
    assert "未连接" in result.json()["detail"]


def test_find_forwards_one_request_without_claiming_completion(client):
    with patch("backend.app.seats._request", return_value=b"") as request:
        result = client.post("/api/seats/find")
    request.assert_called_once_with("/api/find", "POST")
    assert result.status_code == 202
    assert result.json()["accepted"] is True


def test_invalid_image_is_rejected_and_jpeg_is_not_cached(client):
    with patch("backend.app.seats._request", return_value=b"not an image"):
        assert client.get("/api/seats/frame").status_code == 502
    with patch("backend.app.seats._request", return_value=b"\xff\xd8test"):
        result = client.get("/api/seats/frame")
    assert result.headers["content-type"] == "image/jpeg"
    assert result.headers["cache-control"] == "no-store"
