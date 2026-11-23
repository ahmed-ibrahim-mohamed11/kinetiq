"""Local AI service (Phase 5, P5.4): FastAPI over the Mode A (after-set) pipeline.

LOCAL ONLY: `python -m ai.service` binds to 127.0.0.1 and there is NO authentication — deployment,
auth, DB and UI belong to the deferred closing phases.

  POST   /v1/analyze       multipart field `video` -> 202 JobInfo (state "queued")
  GET    /v1/jobs/{id}     JobInfo; `result` is an AnalysisResult v1.0 once state == "succeeded"
  DELETE /v1/jobs/{id}     forget a queued/finished job (409 while it is running)
  GET    /v1/health        liveness, device, queue depth
  GET    /v1/model         artifact/model versions + result schema version

A job "succeeds" whenever a valid AnalysisResult was produced — including an explicit abstention
status such as NO_PERSON (same contract as the CLI's exit code 0). It "fails" only when the input
could not be analysed (undecodable video, invalid frame rate, too long); the reason never contains
server paths. The uploaded video is deleted when its job finishes unless GYM_AI_KEEP_UPLOADS=1.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Literal, Optional

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from ai.core.schema import SCHEMA_VERSION, AnalysisResult, Versions
from ai.service.jobs import Job, JobManager, JobRunning, QueueFull

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    device: str = "cpu"
    max_upload_bytes: int = 500 * 1024 * 1024
    max_video_s: float = 600.0
    max_pending: int = 4
    job_ttl_s: float = 3600.0
    keep_uploads: bool = False
    upload_dir: Optional[str] = None       # None -> a private temp dir, removed at shutdown

    @classmethod
    def from_env(cls, **override) -> "Settings":
        e = os.environ
        s = dict(device=e.get("GYM_AI_DEVICE", cls.device),
                 max_upload_bytes=int(float(e.get("GYM_AI_MAX_UPLOAD_MB", 500)) * 1024 * 1024),
                 max_video_s=float(e.get("GYM_AI_MAX_VIDEO_S", cls.max_video_s)),
                 max_pending=int(e.get("GYM_AI_MAX_PENDING", cls.max_pending)),
                 job_ttl_s=float(e.get("GYM_AI_JOB_TTL_S", cls.job_ttl_s)),
                 keep_uploads=_env_bool("GYM_AI_KEEP_UPLOADS", cls.keep_uploads),
                 upload_dir=e.get("GYM_AI_UPLOAD_DIR") or None)
        s.update(override)
        return cls(**s)


class JobInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    state: Literal["queued", "running", "succeeded", "failed"]
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    result: Optional[AnalysisResult] = None

    @classmethod
    def of(cls, j: Job) -> "JobInfo":
        return cls(job_id=j.id, state=j.state, created_at=j.created_at, started_at=j.started_at,
                   finished_at=j.finished_at, error=j.error, result=j.result)


class Health(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"] = "ok"
    device: str
    queue_depth: int


class ModelInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    exercise: Literal["seated_row"] = "seated_row"
    modes: List[Literal["after_set", "live"]] = ["after_set"]
    versions: Versions


class _BodyLimit:
    """Reject request bodies above `max_bytes` (413) — by Content-Length up front, and by counting
    while the body streams in (chunked uploads), before anything reaches the disk copy."""

    def __init__(self, app, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)
        cl = dict(scope.get("headers") or []).get(b"content-length")
        if cl is not None and cl.isdigit() and int(cl) > self.max_bytes:
            return await self._too_large()(scope, receive, send)
        seen = 0

        async def limited():
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > self.max_bytes:
                    raise HTTPException(413, self._detail())
            return msg

        await self.app(scope, limited, send)

    def _detail(self) -> str:
        return f"upload larger than the {self.max_bytes // (1024 * 1024)} MB limit"

    def _too_large(self) -> JSONResponse:
        return JSONResponse({"detail": self._detail()}, status_code=413)


def _probe_duration_s(path: str) -> Optional[float]:
    import cv2
    cap = cv2.VideoCapture(path)
    try:
        if not cap.isOpened():
            return None
        n, fps = cap.get(cv2.CAP_PROP_FRAME_COUNT), cap.get(cv2.CAP_PROP_FPS)
        return n / fps if n > 0 and fps > 0 else None
    finally:
        cap.release()


def _pipeline_analyzer(settings: Settings):
    from ai.core.pipeline import analyze_video, load_components
    comp = load_components(device=settings.device)

    def run(path: str) -> AnalysisResult:
        dur = _probe_duration_s(path)
        if dur is not None and dur > settings.max_video_s:
            raise ValueError(f"video is {dur:.0f} s long; the after-set limit is {settings.max_video_s:.0f} s")
        return analyze_video(path, comp, name="upload")

    return run, dict(comp.versions)


def create_app(settings: Optional[Settings] = None,
               analyzer: Optional[Callable[[str], AnalysisResult]] = None,
               model_versions: Optional[dict] = None) -> FastAPI:
    """`analyzer`/`model_versions` are injectable for tests; by default the real Mode A pipeline
    is loaded once at startup (a missing checkpoint or a bad artifact fails startup explicitly)."""
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        run, versions = analyzer, model_versions
        if run is None:
            run, versions = _pipeline_analyzer(settings)
        own_dir = settings.upload_dir is None
        upload_dir = tempfile.mkdtemp(prefix="gym_ai_uploads_") if own_dir else settings.upload_dir
        os.makedirs(upload_dir, exist_ok=True)
        jobs = JobManager(run, max_pending=settings.max_pending, ttl_s=settings.job_ttl_s,
                          keep_uploads=settings.keep_uploads)
        jobs.start()
        app.state.jobs, app.state.upload_dir = jobs, upload_dir
        app.state.model_info = ModelInfo(versions=Versions.model_validate(versions))
        try:
            yield
        finally:
            jobs.stop()
            if own_dir:
                shutil.rmtree(upload_dir, ignore_errors=True)

    app = FastAPI(title="GYM-AI local analysis service", version=SCHEMA_VERSION, lifespan=lifespan)
    app.add_middleware(_BodyLimit, max_bytes=settings.max_upload_bytes)

    @app.post("/v1/analyze", status_code=202, response_model=JobInfo)
    def analyze(request: Request, video: UploadFile = File(...)):
        ctype = (video.content_type or "").lower()
        if ctype and not (ctype.startswith("video/") or ctype == "application/octet-stream"):
            raise HTTPException(415, "expected a video upload (content type video/*)")
        suffix = os.path.splitext(video.filename or "")[1].lower()
        suffix = suffix if suffix in VIDEO_SUFFIXES else ".mp4"   # the client's name is never used
        path = os.path.join(request.app.state.upload_dir, uuid.uuid4().hex + suffix)
        size = 0
        with open(path, "wb") as f:
            while True:
                chunk = video.file.read(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                f.write(chunk)
        if size == 0:
            os.remove(path)
            raise HTTPException(400, "empty upload")
        try:
            job = request.app.state.jobs.submit(path)
        except QueueFull:
            os.remove(path)
            raise HTTPException(503, "analysis queue is full; retry later", headers={"Retry-After": "30"})
        return JobInfo.of(job)

    @app.get("/v1/jobs/{job_id}", response_model=JobInfo)
    def get_job(job_id: str, request: Request):
        job = request.app.state.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "unknown job (never existed, deleted, or expired)")
        return JobInfo.of(job)

    @app.delete("/v1/jobs/{job_id}", status_code=204)
    def delete_job(job_id: str, request: Request):
        try:
            found = request.app.state.jobs.delete(job_id)
        except JobRunning:
            raise HTTPException(409, "job is running; delete it after it finishes")
        if not found:
            raise HTTPException(404, "unknown job (never existed, deleted, or expired)")
        return Response(status_code=204)

    @app.get("/v1/health", response_model=Health)
    def health(request: Request):
        return Health(device=settings.device, queue_depth=request.app.state.jobs.queue_depth())

    @app.get("/v1/model", response_model=ModelInfo)
    def model(request: Request):
        return request.app.state.model_info

    @app.post("/v1/analyze/squat")
    def analyze_squat_endpoint(
        request: Request,
        video: UploadFile = File(...),
        model_choice: str = "ensemble",
    ):
        """Analyze a Barbell Squat video using the full KinetiQ Squat Assessment pipeline (FR1-FR12)."""
        from ai.core.squat_pipeline import analyze_squat_video

        ctype = (video.content_type or "").lower()
        if ctype and not (ctype.startswith("video/") or ctype == "application/octet-stream"):
            raise HTTPException(415, "expected a video upload (content type video/*)")
        suffix = os.path.splitext(video.filename or "")[1].lower()
        suffix = suffix if suffix in VIDEO_SUFFIXES else ".mp4"
        path = os.path.join(request.app.state.upload_dir, uuid.uuid4().hex + suffix)
        size = 0
        with open(path, "wb") as f:
            while True:
                chunk = video.file.read(1 << 20)
                if not chunk:
                    break
                size += len(chunk)
                f.write(chunk)
        if size == 0:
            os.remove(path)
            raise HTTPException(400, "empty upload")
        try:
            res, _, _ = analyze_squat_video(path, device=settings.device, model_choice=model_choice)
            return res.to_dict()
        finally:
            if not settings.keep_uploads and os.path.exists(path):
                os.remove(path)

    return app
