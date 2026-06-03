from skyward.config import load_config


def test_supabase_db_url_loaded(monkeypatch):
    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://u:p@host:6543/postgres")
    cfg = load_config()
    assert cfg.supabase_db_url == "postgresql://u:p@host:6543/postgres"


def test_supabase_db_url_defaults_none(monkeypatch):
    monkeypatch.delenv("SUPABASE_DB_URL", raising=False)
    cfg = load_config()
    assert cfg.supabase_db_url is None
