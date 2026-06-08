import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://geouser:geopass@localhost:5432/geospatial_tracker"
)
DATABASE_URL_SYNC = os.getenv(
    "DATABASE_URL_SYNC",
    "postgresql://geouser:geopass@localhost:5432/geospatial_tracker"
)
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

# Refuse to boot in production without an explicit SECRET_KEY.
# Dev/test fallback is allowed so local runs keep working out of the box.
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    if ENVIRONMENT == "production":
        raise RuntimeError(
            "SECRET_KEY environment variable is required in production. "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
    SECRET_KEY = "dev-secret-key-change-in-production"
if len(SECRET_KEY) < 32:
    raise RuntimeError(f"SECRET_KEY is too short ({len(SECRET_KEY)} bytes); use ≥ 32 bytes.")

SUPERADMIN_USERNAME = os.getenv("SUPERADMIN_USERNAME", "superadmin")
SUPERADMIN_PASSWORD = os.getenv("SUPERADMIN_PASSWORD", "superadmin123")
SUPERADMIN_EMAIL = os.getenv("SUPERADMIN_EMAIL", "superadmin@geospatial.local")

# Symmetric-encryption key for secrets stored in the DB (CommCare password etc.).
# Generate once with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
SYNC_ENCRYPTION_KEY = os.getenv("SYNC_ENCRYPTION_KEY", "")

# Optional reverse-mirror target: when set, the admin panel exposes a
# "Sync to on-prem" button that copies MDA tables from this DB to the
# on-prem Postgres. Intended for dev laptops connected to the VPN —
# leave blank in production so the button is hidden.
ONPREM_BACKUP_DATABASE_URL = os.getenv("ONPREM_BACKUP_DATABASE_URL", "")

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ── Android companion app: OTA self-distribution + version gate ──────────────
# See docs/apk-app-blueprint.md. The Android app (Kotlin/Compose) is served as
# a static APK from this same server and gated by versionCode so an outdated
# install can be force-updated.
#
# versionCode is a monotonic integer (git commit count); versionName is the
# human tag (e.g. "0.1"). The app sends X-App-Version-Code on every request.
#
# MIN_VERSION_CODE == 0 disables the gate entirely (default — gate is opt-in).
# To force an update: publish the new APK to APK_DIR *first*, then raise
# MIN_VERSION_CODE and restart. Never raise it before the APK is live or
# existing installs lock out with no upgrade path.
def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


MIN_VERSION_CODE = _int_env("MIN_VERSION_CODE", 0)          # 0 = gate disabled
LATEST_VERSION_CODE = _int_env("LATEST_VERSION_CODE", 0)
LATEST_VERSION_NAME = os.getenv("LATEST_VERSION_NAME", "0.1")
UPDATE_URL = os.getenv("UPDATE_URL", "/apk")

# Prefix of the app-only API surface. Endpoints under this prefix require the
# X-App-Version-Code header (a missing header is rejected with 426), so they
# are the real force-update enforcement point. The public web dashboard never
# calls these paths, so it is unaffected by the gate.
APP_API_PREFIX = os.getenv("APP_API_PREFIX", "/api/app/")

# Directory the signed APK is uploaded to by the app-build CI pipeline and
# served from. In production this is a host dir bind-mounted into the
# container (see deploy/docker-compose.prod.yml); locally it defaults to a
# repo-relative ./apk folder so `/apk` works out of the box for dev.
APK_DIR = os.getenv(
    "APK_DIR",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "apk"),
)
# Stable filename the CI pipeline writes the latest build to and that /apk
# serves. Versioned copies (e.g. eritas-0.1.apk) may also live in APK_DIR and
# are reachable at /apk/<filename>.
APK_FILENAME = os.getenv("APK_FILENAME", "eritas-latest.apk")
