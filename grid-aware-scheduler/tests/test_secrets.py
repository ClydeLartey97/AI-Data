from pathlib import Path
import os, pytest
from core import secrets

def test_loads_keys_and_returns_names_never_values(tmp_path):
    f = tmp_path / ".env"
    f.write_text('A_KEY=abc123\n# comment\n\nB_KEY="quoted"\n')
    os.environ.pop("A_KEY", None); os.environ.pop("B_KEY", None)
    names = secrets.load_env_file(f)
    assert set(names) == {"A_KEY", "B_KEY"}
    assert os.environ["A_KEY"] == "abc123"
    assert os.environ["B_KEY"] == "quoted"   # one layer of quotes stripped
    assert "abc123" not in str(names)

def test_existing_environment_wins(tmp_path, monkeypatch):
    f = tmp_path / ".env"; f.write_text("C_KEY=from_file\n")
    monkeypatch.setenv("C_KEY", "from_shell")
    secrets.load_env_file(f)
    assert os.environ["C_KEY"] == "from_shell"

def test_missing_file_is_not_an_error(tmp_path):
    assert secrets.load_env_file(tmp_path / "nope") == []

def test_blank_value_is_not_set(tmp_path):
    f = tmp_path / ".env"; f.write_text("D_KEY=\n")
    os.environ.pop("D_KEY", None)
    assert secrets.load_env_file(f) == []
    assert "D_KEY" not in os.environ
