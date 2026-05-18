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
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")

SUPERADMIN_USERNAME = os.getenv("SUPERADMIN_USERNAME", "superadmin")
SUPERADMIN_PASSWORD = os.getenv("SUPERADMIN_PASSWORD", "superadmin123")
SUPERADMIN_EMAIL = os.getenv("SUPERADMIN_EMAIL", "superadmin@geospatial.local")

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
