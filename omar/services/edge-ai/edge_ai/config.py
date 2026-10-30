"""Typed configuration: environment + cameras.yaml + camera-credentials.yaml."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Literal
from uuid import UUID

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    software_version: str = "0.1.0"
    # Cloud configuration sync (device-sync Edge Function). Derived from ingest_url when empty.
    sync_url: str = ""
    config_sync_interval_seconds: float = 300.0
    # Model files (ONNX). Empty = stage disabled; the pipeline logs which stages are active.
    detector_model_path: Path | None = None
    pose_model_path: Path | None = None
    reid_model_path: Path | None = None
    face_model_path: Path | None = None

    @property
    def effective_sync_url(self) -> str:
        if self.sync_url:
            return self.sync_url
        return self.ingest_url.rsplit("/", 1)[0] + "/device-sync"

    @property
    def biometric_key(self) -> bytes | None:
        if not self.biometric_key_b64:
            return None
        key = base64.b64decode(self.biometric_key_b64)
        if len(key) != 32:
            raise ValueError("EDGE_BIOMETRIC_KEY_B64 must decode to exactly 32 bytes")
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
    url: str
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
    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    creds = raw.get("credentials", {}) or {}
    return {UUID(str(k)): CameraCredential.model_validate(v) for k, v in creds.items()}


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
