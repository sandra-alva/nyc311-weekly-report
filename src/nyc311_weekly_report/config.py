from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"

NYC311_FIELDS = [
    "unique_key",
    "created_date",
    "complaint_type",
    "descriptor",
    "borough",
    "incident_zip",
    "status",
]
