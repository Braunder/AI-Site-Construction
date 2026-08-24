"""encrypt existing LLM provider keys

Revision ID: d5e6f7a8b901
Revises: c4e9a1b7d203
Create Date: 2026-08-24
"""

import base64
import hashlib
import os

from alembic import op
from cryptography.fernet import Fernet
import sqlalchemy as sa

revision = "d5e6f7a8b901"
down_revision = "c4e9a1b7d203"
branch_labels = None
depends_on = None


def _fernet() -> Fernet:
    configured = os.getenv("LLM_SECRETS_KEY", "")
    secret = configured or os.getenv("SECRET_KEY", "change-me-in-production")
    key = configured or base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()).decode()
    return Fernet(key.encode())


def upgrade() -> None:
    bind = op.get_bind()
    cipher = _fernet()
    rows = bind.exec_driver_sql("SELECT id, api_key FROM llm_providers").fetchall()
    for provider_id, api_key in rows:
        if api_key and not api_key.startswith("enc:"):
            encrypted = "enc:" + cipher.encrypt(api_key.encode()).decode()
            bind.execute(
                sa.text("UPDATE llm_providers SET api_key = :api_key WHERE id = :id"),
                {"api_key": encrypted, "id": provider_id},
            )


def downgrade() -> None:
    # Encrypted values are intentionally retained; plaintext restoration is unsafe.
    pass