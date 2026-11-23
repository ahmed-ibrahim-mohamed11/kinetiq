import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from edge_ai.config import CameraConfig, CameraCredential, build_authenticated_url, load_cameras, redact_url
from edge_ai.reps.profiles import RepProfile
from edge_ai.tools.default_profiles import DEFAULT_PROFILES

ROOT = Path(__file__).resolve().parents[1]
SUPABASE = ROOT.parents[1] / "supabase"
# The catalog is reference data in a migration (0007) so production gets it without seed.sql.
CATALOG_SOURCES = [SUPABASE / "seed.sql", *sorted((SUPABASE / "migrations").glob("*exercise_catalog*.sql"))]


def test_default_profiles_are_valid():
    for p in DEFAULT_PROFILES.values():
        RepProfile.from_catalog(p)


def test_default_profiles_match_catalog_sql_slugs():
    sql = "\n".join(p.read_text(encoding="utf-8") for p in CATALOG_SOURCES if p.exists())
    slugs = set(re.findall(r"^\('([a-z0-9-]+)', '[^']+', '[a-z_]+', '[a-z_]+',\s*$", sql, flags=re.M))
    assert slugs, "could not parse exercise slugs from the exercise catalog SQL"
    assert slugs == set(DEFAULT_PROFILES)


def test_profile_validation():
    with pytest.raises(ValidationError):
        RepProfile.from_catalog({"signal": "x", "top_threshold": 50, "bottom_threshold": 90})
    with pytest.raises(ValidationError):
        RepProfile.from_catalog({"signal": "x"})  # missing thresholds


def test_cameras_example_loads_and_rejects_embedded_credentials():
    cams = load_cameras(ROOT / "config" / "cameras.example.yaml")
    assert len(cams.cameras) == 3
    assert "leg-press" in cams.exercise_ids
    with pytest.raises(ValidationError):
        CameraConfig(id="c0000000-0000-4000-8000-000000000001", name="x", url="rtsp://admin:pw@10.0.0.1/live")  # type: ignore[arg-type]


def test_credential_injection_and_redaction():
    url = build_authenticated_url(
        "rtsp://10.0.0.1:554/live", CameraCredential(username="view er", password="p@ss/word")
    )
    assert url == "rtsp://view%20er:p%40ss%2Fword@10.0.0.1:554/live"
    assert redact_url(url) == "rtsp://***@10.0.0.1:554/live"
    assert redact_url("rtsp://10.0.0.1/live") == "rtsp://10.0.0.1/live"
