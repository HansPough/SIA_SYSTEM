import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-only-change-me")
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", "postgresql+psycopg2://postgres:password@localhost:5432/inventory_db"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # Company name shown in the header, login page, printed documents and PDFs.
    COMPANY_NAME = os.getenv("COMPANY_NAME", "3T Tire Supply")
    # Role / user picker for development ONLY. Off unless DEV_LOGIN=1 is set in .env.
    # (It used to default to ON, which would let anyone open /dev/role/owner on a server
    # that was missing this setting.)
    DEV_LOGIN = os.getenv("DEV_LOGIN", "0") == "1"

    # Browser session hardening. Set SESSION_COOKIE_SECURE=1 when the site is
    # served over HTTPS; keep it 0 for local http://127.0.0.1.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "0") == "1"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)
    MAX_CONTENT_LENGTH = 8 * 1024 * 1024      # 8 MB upload cap (attachments)
