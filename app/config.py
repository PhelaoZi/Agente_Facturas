"""Carga de entorno y constantes de la app (patrón _load_env del proyecto)."""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _load_env() -> None:
    """Carga .env de la raíz sin depender de python-dotenv."""
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

DB_URL = "postgresql://{user}:{pwd}@{host}:{port}/{db}".format(
    user=os.environ.get("DB_USER", "postgres"),
    pwd=os.environ.get("DB_PASSWORD", "postgres"),
    host=os.environ.get("DB_HOST", "localhost"),
    port=os.environ.get("DB_PORT", "5432"),
    db=os.environ.get("DB_NAME", "dte_facturas_chile"),
)
