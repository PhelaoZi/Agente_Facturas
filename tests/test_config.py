# tests/test_config.py
from app import config


def test_project_root_apunta_a_la_raiz():
    # La raíz del proyecto debe contener la carpeta scripts/ ya existente.
    assert (config.PROJECT_ROOT / "scripts").exists()


def test_db_url_apunta_a_la_base_correcta():
    assert config.DB_URL.startswith("postgresql://")
    assert "dte_facturas_chile" in config.DB_URL
