"""Typed configuration: environment + cameras.yaml + camera-credentials.yaml."""

from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import yaml
from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def require_https(url: str, what: str, allow_insecure: bool) -> None:
    """Cloud URLs carry consents, revocations and workout facts: refuse cleartext except on loopback."""
    if not url:
        return
    parts = urlsplit(url)
    if parts.scheme == "https":
        return
    if parts.scheme == "http" and (allow_insecure or (parts.hostname or "") in _LOOPBACK_HOSTS):
        return
    raise ValueError(
        f"{what} must use https:// (http is accepted only for loopback hosts or with EDGE_ALLOW_INSECURE_HTTP=1)"
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EDGE_", env_file=".env", extra="ignore")

    device_id: UUID
    branch_id: UUID
    device_secret: str = Field(min_length=16)
    ingest_url: str
    data_dir: Path = Path("./data")
    cameras_file: Path = Path("./config/cameras.yaml")
    camera_secrets_file: Path = Path("./config/camera-credentials.yaml")
    log_level: str = "INFO"
    health_port: int = 8088
    sync_interval_seconds: float = 2.0
    sync_batch_size: int = Field(default=200, ge=1, le=500)
    inference_fps: float = Field(default=10.0, gt=0, le=60)
    heartbeat_interval_seconds: float = 30.0
    biometric_key_b64: str = ""
    # Preferred in production: a root-only file (e.g. a Docker secret) holding the base64 key, kept
    # outside the data volume. Takes precedence over EDGE_BIOMETRIC_KEY_B64.
    biometric_key_file: Path | None = None
    software_version: str = "0.1.0"
    # Cloud configuration sync (device-sync Edge Function). Derived from ingest_url when empty.
    sync_url: str = ""
    config_sync_interval_seconds: float = 300.0
    # Development only: allow http:// cloud URLs on non-loopback hosts.
    allow_insecure_http: bool = False
    # Model files (ONNX). Empty = stage disabled; the pipeline logs which stages are active.
    detector_model_path: Path | None = None
    pose_model_path: Path | None = None
    reid_model_path: Path | None = None
    face_model_path: Path | None = None
    # Output layout of the detector (EDGE_DETECTOR_LAYOUT) and pose adapter (EDGE_POSE_KIND); see
    # models/README.md. Read through Settings so values in .env / the Docker env_file apply.
    detector_layout: Literal["auto", "yolox"] = "auto"
    pose_kind: Literal["auto", "yolo_pose", "rtmpose"] = "auto"
    # `edge-ai simulate` overrides (synthetic test data). Defaults reproduce the seed-based scenario.
    sim_member_id: UUID = UUID("33333333-3333-4333-8333-333333333333")
    sim_consent_id: UUID = UUID("55555555-5555-4555-8555-555555555556")  # cctv_workout_tracking record
    sim_checkin: Literal["qr", "skip"] = "qr"  # skip = already checked in at the kiosk
    sim_checkout: Literal["edge", "skip"] = "edge"  # skip = the member checks out in the app

    @field_validator(
        "biometric_key_file",
        "detector_model_path",
        "pose_model_path",
        "reid_model_path",
        "face_model_path",
        mode="before",
    )
    @classmethod
    def _empty_path_is_none(cls, v: object) -> object:
        # `EDGE_FACE_MODEL_PATH=` in .env means "not configured", not Path(".").
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("detector_layout", "pose_kind", mode="before")
    @classmethod
    def _normalise_choice(cls, v: object) -> object:
        # Same spelling rules as the factories: case-insensitive, 'yolo-pose' == 'yolo_pose', empty = auto.
        if isinstance(v, str):
            v = v.strip().lower().replace("-", "_")
            return v or "auto"
        return v

    @model_validator(mode="after")
    def _cloud_urls_use_tls(self) -> Settings:
        require_https(self.ingest_url, "EDGE_INGEST_URL", self.allow_insecure_http)
        require_https(self.sync_url, "EDGE_SYNC_URL", self.allow_insecure_http)
        return self

    @property
    def effective_sync_url(self) -> str:
        if self.sync_url:
            return self.sync_url
        return self.ingest_url.rsplit("/", 1)[0] + "/device-sync"

    @property
    def biometric_key(self) -> bytes | None:
        if self.biometric_key_file is not None:
            raw = self.biometric_key_file.read_text(encoding="utf-8").strip()
            source = "EDGE_BIOMETRIC_KEY_FILE"
        else:
            raw = self.biometric_key_b64
            source = "EDGE_BIOMETRIC_KEY_B64"
        if not raw:
            return None
        key = base64.b64decode(raw)
        if len(key) != 32:
            raise ValueError(f"{source} must decode to exactly 32 bytes")
        return key


class ZoneConfig(BaseModel):
    id: UUID
    type: Literal["equipment", "entrance", "exit", "exclusion", "free_weights"]
    equipment_id: UUID | None = None
    exercise_slug: str | None = None
    polygon: list[tuple[float, float]] = Field(min_length=3)

    @field_validator("equipment_id")
    @classmethod
    def _equipment_required(cls, v: UUID | None, info):  # type: ignore[no-untyped-def]
        if info.data.get("type") == "equipment" and v is None:
            raise ValueError("equipment zones require equipment_id")
        return v


class CameraConfig(BaseModel):
    id: UUID
    name: str
    purpose: Literal["entrance", "floor", "exit", "mixed"] = "floor"
    # cameras.yaml says `url`; the device-sync snapshot (public.cameras) says `stream_url`.
    url: str = Field(validation_alias=AliasChoices("url", "stream_url"))
    inference_fps: float = 10.0
    zones: list[ZoneConfig] = Field(default_factory=list)

    @field_validator("url")
    @classmethod
    def _no_credentials(cls, v: str) -> str:
        scheme_rest = v.split("://", 1)
        if len(scheme_rest) == 2 and "@" in scheme_rest[1].split("/", 1)[0]:
            raise ValueError("camera URL must not embed credentials; use camera-credentials.yaml")
        return v


class CamerasFile(BaseModel):
    cameras: list[CameraConfig]
    exercise_ids: dict[str, UUID] = Field(default_factory=dict)


class CameraCredential(BaseModel):
    username: str
    password: str


def load_cameras(path: Path) -> CamerasFile:
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return CamerasFile.model_validate(raw)


def load_camera_credentials(path: Path) -> dict[UUID, CameraCredential]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except PermissionError as exc:
        # The container runs as uid 10001: the file must be owned by it (chown 10001:10001, chmod 600),
        # not made world-readable. See docs/deployment/edge-device.md.
        raise PermissionError(
            f"camera credentials file {path} is not readable by uid {_current_uid()}; "
            "chown it to the service user (10001 in the container) and keep mode 600"
        ) from exc
    creds = raw.get("credentials", {}) or {}
    return {UUID(str(k)): CameraCredential.model_validate(v) for k, v in creds.items()}


def _current_uid() -> str:
    getuid = getattr(os, "getuid", None)
    return str(getuid()) if getuid is not None else "?"


def build_authenticated_url(url: str, cred: CameraCredential | None) -> str:
    """Inject credentials into an RTSP/HTTP URL at runtime only (never persisted or logged)."""
    if cred is None:
        return url
    scheme, rest = url.split("://", 1)
    from urllib.parse import quote

    return f"{scheme}://{quote(cred.username, safe='')}:{quote(cred.password, safe='')}@{rest}"


def redact_url(url: str) -> str:
    """Strip credentials for logs."""
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    host_part, _, path = rest.partition("/")
    if "@" in host_part:
        host_part = host_part.split("@", 1)[1]
        return f"{scheme}://***@{host_part}/{path}" if path else f"{scheme}://***@{host_part}"
    return url
