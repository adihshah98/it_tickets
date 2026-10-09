import pytest

from tech.config import Settings, read_env_file


def make_repo(tmp_path, tenant_env: str, root_env: str = ""):
    (tmp_path / "tenant").mkdir()
    (tmp_path / "tenant" / ".env").write_text(tenant_env)
    (tmp_path / ".env").write_text(root_env)
    return tmp_path


TENANT_ENV = """# comment
AZURE_TENANT_ID=t1
AZURE_CLIENT_ID=c1
AZURE_CLIENT_CERTIFICATE_PATH=.secrets/app.pem
M365_DOMAIN=Example.onmicrosoft.com
"""


def test_reads_both_env_files(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    repo = make_repo(tmp_path, TENANT_ENV, "ANTHROPIC_API_KEY=sk-test\n")
    s = Settings.load(repo)
    assert (s.tenant_id, s.client_id, s.domain) == ("t1", "c1", "Example.onmicrosoft.com")
    assert s.certificate_path == repo / "tenant" / ".secrets" / "app.pem"
    assert s.anthropic_api_key == "sk-test"
    assert s.database_url == f"sqlite:///{repo / 'data' / 'tech.db'}"


def test_empty_api_key_is_none(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = Settings.load(make_repo(tmp_path, TENANT_ENV, "ANTHROPIC_API_KEY=\n"))
    assert s.anthropic_api_key is None


def test_missing_tenant_settings_explain_what_to_run(tmp_path, monkeypatch):
    for key in ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_CERTIFICATE_PATH", "M365_DOMAIN"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError, match="setup_app.py"):
        Settings.load(make_repo(tmp_path, ""))


def test_read_env_file_ignores_comments_and_blank_lines(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# x\n\nA=1\nB = two words \n")
    assert read_env_file(p) == {"A": "1", "B": "two words"}
