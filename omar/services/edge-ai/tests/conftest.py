import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPO_ROOT = ROOT.parents[1]
FIXTURES = REPO_ROOT / "packages" / "api-contracts" / "fixtures" / "events_v1_valid.json"
