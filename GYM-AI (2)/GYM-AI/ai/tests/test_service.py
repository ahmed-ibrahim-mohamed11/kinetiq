"""P5.4: local FastAPI service. Endpoint behaviour is tested with an injected analyzer (fast);
one slow test runs the real Mode A pipeline end-to-end through HTTP on a dev video."""
import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

from ai.core.schema import AnalysisResult
from ai.service.app import Settings, create_app
from ai.service.jobs import INTERNAL_ERROR
from ai.tests.conftest import REPO_ROOT
from ai.tests.test_schema import _result

VERSIONS = dict(artifact_version="1.0.0", artifact_fingerprint="63d36c31761c95a8" * 4,
                detector="yolo11n-pose", lifter="MotionBERT-Lite")
VIDEO = ("set.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64, "video/mp4")


class Recorder:
    def __init__(self, fn=None):
        self.paths, self.existed, self.fn = [], [], fn

    def __call__(self, path):
        self.paths.append(path)
        self.existed.append(os.path.exists(path))
        if self.fn is not None:
            return self.fn(path)
        return AnalysisResult.model_validate(_result())


def _client(analyzer, tmp_path=None, **settings):
    s = Settings(upload_dir=str(tmp_path) if tmp_path else None, **settings)
    return TestClient(create_app(s, analyzer=analyzer, model_versions=VERSIONS))


def _wait(c, job_id, timeout=10.0):
    t_end = time.time() + timeout
    while time.time() < t_end:
        j = c.get(f"/v1/jobs/{job_id}").json()
        if j["state"] in ("succeeded", "failed"):
            return j
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def test_health_and_model():
    with _client(Recorder()) as c:
        h = c.get("/v1/health").json()
        assert h == dict(status="ok", device="cpu", queue_depth=0)
        m = c.get("/v1/model").json()
        assert m["schema_version"] == "1.0" and m["exercise"] == "seated_row"
        assert m["versions"]["artifact_fingerprint"] == VERSIONS["artifact_fingerprint"]


def test_submit_poll_success_and_upload_deleted(tmp_path):
    rec = Recorder()
    with _client(rec, tmp_path) as c:
        r = c.post("/v1/analyze", files={"video": VIDEO})
        assert r.status_code == 202, r.text
        info = r.json()
        assert info["state"] in ("queued", "running", "succeeded")
        j = _wait(c, info["job_id"])
        assert j["state"] == "succeeded" and j["error"] is None
        AnalysisResult.model_validate(j["result"])
        assert rec.existed == [True]
        assert not os.path.exists(rec.paths[0])
        assert os.listdir(tmp_path) == []
        assert "set.mp4" not in rec.paths[0]             # the client's file name is never used
        assert os.path.dirname(rec.paths[0]) == str(tmp_path)


def test_keep_uploads_keeps_the_file(tmp_path):
    rec = Recorder()
    with _client(rec, tmp_path, keep_uploads=True) as c:
        j = _wait(c, c.post("/v1/analyze", files={"video": VIDEO}).json()["job_id"])
        assert j["state"] == "succeeded"
        assert os.path.exists(rec.paths[0])


def test_input_error_fails_job_with_reason_but_no_server_path(tmp_path):
    def bad(path):
        raise ValueError(f"cannot decode video: {path}")
    with _client(Recorder(bad), tmp_path) as c:
        j = _wait(c, c.post("/v1/analyze", files={"video": VIDEO}).json()["job_id"])
        assert j["state"] == "failed" and j["result"] is None
        assert "cannot decode video" in j["error"]
        assert str(tmp_path) not in j["error"] and ".mp4" not in j["error"]
        assert os.listdir(tmp_path) == []


def test_internal_error_is_opaque_and_upload_deleted(tmp_path):
    def boom(path):
        raise RuntimeError("secret internals")
    with _client(Recorder(boom), tmp_path) as c:
        j = _wait(c, c.post("/v1/analyze", files={"video": VIDEO}).json()["job_id"])
        assert j["state"] == "failed" and j["error"] == INTERNAL_ERROR
        assert os.listdir(tmp_path) == []
        assert c.get("/v1/health").status_code == 200      # the worker survived


def test_unknown_and_deleted_jobs_404():
    with _client(Recorder()) as c:
        assert c.get("/v1/jobs/" + "0" * 32).status_code == 404
        assert c.delete("/v1/jobs/" + "0" * 32).status_code == 404
        jid = c.post("/v1/analyze", files={"video": VIDEO}).json()["job_id"]
        _wait(c, jid)
        assert c.delete(f"/v1/jobs/{jid}").status_code == 204
        assert c.get(f"/v1/jobs/{jid}").status_code == 404


@pytest.mark.parametrize("files,status", [
    ({"video": ("set.mp4", b"", "video/mp4")}, 400),
    ({"video": ("notes.txt", b"hello", "text/plain")}, 415),
    ({"other": VIDEO}, 422),
])
def test_bad_uploads_rejected(tmp_path, files, status):
    rec = Recorder()
    with _client(rec, tmp_path) as c:
        assert c.post("/v1/analyze", files=files).status_code == status
        assert rec.paths == [] and os.listdir(tmp_path) == []


def test_upload_over_limit_413(tmp_path):
    rec = Recorder()
    with _client(rec, tmp_path, max_upload_bytes=1024) as c:
        r = c.post("/v1/analyze", files={"video": ("big.mp4", b"\x00" * 4096, "video/mp4")})
        assert r.status_code == 413
        assert rec.paths == [] and os.listdir(tmp_path) == []
        assert c.post("/v1/analyze", files={"video": VIDEO}).status_code == 202


def test_body_limit_counts_streamed_bytes_without_content_length():
    import asyncio
    from fastapi import HTTPException
    from ai.service.app import _BodyLimit
    chunks = [b"x" * 600, b"x" * 600, b""]

    async def receive():
        body = chunks.pop(0)
        return {"type": "http.request", "body": body, "more_body": bool(chunks)}

    async def downstream(scope, receive, send):
        while (await receive()).get("more_body"):
            pass

    async def send(msg):
        pass
    with pytest.raises(HTTPException) as e:
        asyncio.run(_BodyLimit(downstream, max_bytes=1024)(
            {"type": "http", "method": "POST", "headers": []}, receive, send))
    assert e.value.status_code == 413


def test_chunked_multipart_upload_over_limit_413(tmp_path):
    rec, b = Recorder(), "gymaiboundary"
    body = (f'--{b}\r\nContent-Disposition: form-data; name="video"; filename="big.mp4"\r\n'
            f"Content-Type: video/mp4\r\n\r\n").encode() + b"\x00" * 8192 + f"\r\n--{b}--\r\n".encode()

    def gen():
        for i in range(0, len(body), 1000):
            yield body[i:i + 1000]
    with _client(rec, tmp_path, max_upload_bytes=1024) as c:
        r = c.post("/v1/analyze", content=gen(),
                   headers={"content-type": f"multipart/form-data; boundary={b}"})
        assert r.status_code == 413, r.text
        assert rec.paths == [] and os.listdir(tmp_path) == []


def test_queue_full_503_then_drains_and_running_job_cannot_be_deleted(tmp_path):
    gate, started = threading.Event(), threading.Event()

    def slow(path):
        started.set()
        assert gate.wait(10)
        return AnalysisResult.model_validate(_result())
    with _client(Recorder(slow), tmp_path, max_pending=1) as c:
        j1 = c.post("/v1/analyze", files={"video": VIDEO}).json()["job_id"]
        assert started.wait(5)                                     # j1 is running
        assert c.delete(f"/v1/jobs/{j1}").status_code == 409
        j2 = c.post("/v1/analyze", files={"video": VIDEO}).json()["job_id"]   # fills the queue
        r3 = c.post("/v1/analyze", files={"video": VIDEO})
        assert r3.status_code == 503 and r3.headers.get("retry-after") == "30"
        assert c.get("/v1/health").json()["queue_depth"] == 1
        gate.set()
        assert _wait(c, j1)["state"] == "succeeded"
        assert _wait(c, j2)["state"] == "succeeded"
        assert os.listdir(tmp_path) == []


def test_private_upload_dir_removed_at_shutdown():
    rec = Recorder()
    with _client(rec) as c:
        _wait(c, c.post("/v1/analyze", files={"video": VIDEO}).json()["job_id"])
        d = os.path.dirname(rec.paths[0])
        assert os.path.isdir(d)
    assert not os.path.exists(d)


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("GYM_AI_MAX_UPLOAD_MB", "2")
    monkeypatch.setenv("GYM_AI_KEEP_UPLOADS", "yes")
    monkeypatch.setenv("GYM_AI_MAX_PENDING", "7")
    s = Settings.from_env(device="cuda")
    assert (s.max_upload_bytes, s.keep_uploads, s.max_pending, s.device) == (2 * 1024 * 1024, True, 7, "cuda")


SR_VIDEO = os.path.join(REPO_ROOT, "ai", "data", "seated_row", "asymmetry", "asymmetry_02.mp4")


@pytest.mark.slow
@pytest.mark.skipif(not os.path.exists(SR_VIDEO), reason="raw dev video not present (git-ignored)")
def test_real_pipeline_through_http_matches_direct_call():
    from ai.core.pipeline import analyze_video, load_components
    with TestClient(create_app(Settings(device="cpu"))) as c:
        assert c.get("/v1/model").json()["versions"]["artifact_version"]
        with open(SR_VIDEO, "rb") as f:
            r = c.post("/v1/analyze", files={"video": ("clip.mp4", f.read(), "video/mp4")})
        assert r.status_code == 202
        j = _wait(c, r.json()["job_id"], timeout=900)
    assert j["state"] == "succeeded", j["error"]
    got = AnalysisResult.model_validate(j["result"]).model_dump()
    want = analyze_video(SR_VIDEO, load_components(device="cpu"), name="upload").model_dump()
    got.pop("timings_ms"), want.pop("timings_ms")
    assert got == want
