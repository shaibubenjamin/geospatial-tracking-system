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
