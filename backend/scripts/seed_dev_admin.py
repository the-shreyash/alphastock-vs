"""Seed a development admin user (PH1.1).

Replaces the removed auto-login endpoint and the removed startup admin
seeding. This is the ONLY sanctioned way to create a privileged account in a
development environment; it refuses to run when APP_ENV=production.

Usage:
    cd backend
    python scripts/seed_dev_admin.py

Configuration (env vars, with dev-only defaults):
    APP_ENV          must not be "production"
    MONGO_URL        Mongo connection string (default mongodb://localhost:27017)
    DB_NAME          database name (default alphapartner)
    ADMIN_EMAIL      admin email (default admin@alphapartner.com)
    ADMIN_PASSWORD   admin password (default admin123 — dev only)

The script is idempotent: if the user already exists it leaves the account
untouched (it never resets an existing password).
"""
import asyncio
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bcrypt
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


async def main() -> int:
    app_env = os.environ.get("APP_ENV", "development").lower()
    if app_env == "production":
        print("ERROR: seed_dev_admin.py must never run in production. "
              "Create admin accounts through legitimate registration and role assignment.")
        return 1

    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "alphapartner")
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@alphapartner.com").lower().strip()
    admin_password = os.environ.get("ADMIN_PASSWORD", "admin123")

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]
    try:
        existing = await db.users.find_one({"email": admin_email})
        if existing:
            print(f"Admin user already exists: {admin_email} — leaving it untouched.")
            return 0

        password_hash = bcrypt.hashpw(admin_password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        await db.users.insert_one({
            "name": "Admin",
            "email": admin_email,
            "password_hash": password_hash,
            "role": "admin",
            "capital": 500000,
            "risk_level": "aggressive",
            "max_daily_loss": 25000,
            "max_trades_per_day": 10,
            "telegram_chat_id": None,
            "notification_prefs": {
                "push": True, "email": True, "morning_report": True,
                "trade_alerts": True, "exit_reminder": True,
                "portfolio_alerts": True, "email_alerts": True,
                "telegram_alerts": False,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        print(f"Dev admin seeded: {admin_email}")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
