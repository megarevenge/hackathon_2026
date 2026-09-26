"""Real FFmpeg integration + upload protocol failure cases; no 2 GiB RAM fixture."""
import asyncio
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def module(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("APP_PASSWORD", "")
    monkeypatch.setenv("MAX_OUTPUT_BYTES", str(64 * 1024**2))
    monkeypatch.setenv("MIN_FREE_BYTES", "0")
    monkeypatch.setenv("DEMO_DELAY_SECONDS", "0")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://frontend.example")
    import main
    return importlib.reload(main)


@pytest.fixture
def client(module):
    with TestClient(module.app) as client:
        yield client


@pytest.fixture(scope="session")
def video(tmp_path_factory):
    path = tmp_path_factory.mktemp("media") / "sample.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x64:r=5",
                    "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path.read_bytes()  # Only a tiny synthetic fixture, never the uploaded video.


def init(client, size, name="test.mp4"):
    response = client.post("/api/uploads", json={"filename": name, "size": size})
    assert response.status_code == 201, response.text
    return response.json()["job_id"]


def chunk(client, job, data, offset=0):
    return client.post(f"/api/upload?job_id={job}&offset={offset}", content=data,
                       headers={"Content-Type": "application/octet-stream"})


def terminal(client, job):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        result = client.get(f"/api/status/{job}").json()
        if result["status"] in ("completed", "failed"):
            return result
        time.sleep(.05)
    pytest.fail("Processor did not finish")


def test_chunked_video_and_downloads(client, module, video):
    job = init(client, len(video))
    assert client.get(f"/api/download/{job}/mp4").status_code == 409
    first = chunk(client, job, video[:100])
    assert first.status_code == 202 and first.json()["received"] == 100
    assert chunk(client, job, video[:100]).status_code == 409
    status = client.get(f"/api/status/{job}").json()
    assert status["phase"] == "uploading" and status["received"] == 100
    assert chunk(client, job, video[100:], 100).json()["queued"] is True
    state = terminal(client, job)
    assert state["status"] == "completed", state
    assert state["progress"] == 100
    metadata = client.get(f"/api/download/{job}/json")
    assert metadata.json()["mode"] == "demo"
    output = client.get(f"/api/download/{job}/mp4")
    assert output.status_code == 200 and b"ftyp" in output.content[:40]
    assert "processed_video.mp4" in output.headers["content-disposition"]
    part = client.get(f"/api/download/{job}/mp4", headers={"Range": "bytes=0-31"})
    assert part.status_code == 206 and len(part.content) == 32
    assert not module.app.state.downloads


def test_one_shot_and_background_response(client, monkeypatch, video):
    monkeypatch.setenv("DEMO_DELAY_SECONDS", "2")
    before = time.monotonic()
    response = client.post(f"/api/upload?filename=clip.mov&total_size={len(video)}", content=video,
                           headers={"Content-Type": "application/octet-stream"})
    assert response.status_code == 202, response.text
    assert time.monotonic() - before < 1.5
    job = response.json()["job_id"]
    assert client.get("/healthz").status_code == 200
    assert terminal(client, job)["status"] == "completed"


def test_limits_filename_auth_and_cors(client, module, monkeypatch):
    assert client.post("/api/uploads", json={"filename":"x.mp4","size":module.MAX_UPLOAD+1}).status_code == 422
    # Boundary accepted without allocating a 2 GiB bytes object.
    job = init(client, module.MAX_UPLOAD)
    assert client.get(f"/api/status/{job}").json()["size"] == module.MAX_UPLOAD
    for name in ("../x.mp4", "a\\x.mov"):
        assert client.post("/api/uploads", json={"filename":name,"size":1}).status_code == 400
    assert client.post("/api/uploads", json={"filename":"x.txt","size":1}).status_code == 415
    assert client.post("/api/upload", content=b"x", headers={"Content-Length":str(module.MAX_UPLOAD+1)}).status_code == 413
    assert client.get("/api/status/not-a-uuid").status_code == 422
    assert client.get(f"/api/download/{job}/input").status_code == 422
    headers={"Origin":"https://frontend.example","Access-Control-Request-Method":"POST",
             "Access-Control-Request-Headers":"content-type"}
    assert client.options("/api/upload", headers=headers).headers["access-control-allow-origin"] == "https://frontend.example"
    headers["Origin"]="https://hostile.example"
    assert client.options("/api/upload", headers=headers).status_code == 400
    monkeypatch.setattr(module, "PASSWORD", "private")
    assert client.get("/").status_code == 401
    assert client.get("/", auth=("admin","private")).status_code == 200
    assert client.get("/api/config", auth=("admin","wrong")).status_code == 401
    assert client.get("/healthz").status_code == 200


def test_invalid_media_fails_without_downloads(client):
    job = init(client, 3)
    assert chunk(client, job, b"bad").status_code == 202
    result = terminal(client, job)
    assert result["status"] == "failed" and result["error"]
    assert client.get(f"/api/download/{job}/json").status_code == 409


def test_overflow_rollback_and_empty_chunk(client, module):
    job = init(client, 10)
    assert chunk(client, job, b"123").status_code == 202
    assert chunk(client, job, b"").status_code == 409  # stale offset
    assert chunk(client, job, b"", 3).status_code == 400
    # Force streaming/chunked transfer without Content-Length.
    response = chunk(client, job, iter([b"456", b"78901234"]), 3)
    assert response.status_code == 413
    assert (module.job_dir(job)/"input").read_bytes() == b"123"
    assert client.get(f"/api/status/{job}").json()["received"] == 3


def test_admission_capacity_and_disk(client, module, monkeypatch):
    monkeypatch.setattr(module, "MAX_ACTIVE", 1)
    init(client, 10)
    assert client.post("/api/uploads", json={"filename":"x.mp4","size":10}).status_code == 429
    monkeypatch.setattr(module, "MAX_ACTIVE", 4)
    monkeypatch.setattr(module, "MIN_FREE", 10**20)
    assert client.post("/api/uploads", json={"filename":"x.mp4","size":10}).status_code == 507


def test_incomplete_upload_survives_restart(module):
    with TestClient(module.app) as client:
        job = init(client, 20)
        chunk(client, job, b"12345")
    # Simulate process death after write but before DB commit.
    with (module.job_dir(job)/"input").open("ab") as output:
        output.write(b"uncommitted")
    with TestClient(module.app) as client:
        state = client.get(f"/api/status/{job}").json()
        assert state["received"] == 5
        assert (module.job_dir(job)/"input").read_bytes() == b"12345"
        assert chunk(client, job, b"678", 5).status_code == 202


def test_queued_work_recovers(module, video):
    with TestClient(module.app) as client:
        job = init(client, len(video))
    (module.job_dir(job)/"input").write_bytes(video)
    module._db("UPDATE jobs SET state='processing',received=size WHERE id=?", (job,))
    with TestClient(module.app) as client:
        assert terminal(client, job)["status"] == "completed"
    with TestClient(module.app) as client:
        assert client.get(f"/api/status/{job}").json()["status"] == "completed"
        assert client.get(f"/api/download/{job}/mp4").status_code == 200


def test_retention_respects_live_downloads(client, module):
    job = init(client, 3)
    chunk(client, job, b"bad")
    assert terminal(client, job)["status"] == "failed"
    module._db("UPDATE jobs SET updated=0 WHERE id=?", (job,))
    module.app.state.downloads[job] = 1
    client.portal.call(module.cleanup_once, module.app)
    assert client.get(f"/api/status/{job}").status_code == 200
    del module.app.state.downloads[job]
    client.portal.call(module.cleanup_once, module.app)
    assert client.get(f"/api/status/{job}").status_code == 404
    assert not module.job_dir(job).exists()


def test_timeout_terminates_child(client, module, monkeypatch, video):
    monkeypatch.setattr(module, "JOB_TIMEOUT", 1)
    monkeypatch.setenv("DEMO_DELAY_SECONDS", "10")
    job = init(client, len(video))
    chunk(client, job, video)
    result = terminal(client, job)
    assert result["status"] == "failed"
    assert "time limit" in result["error"]
