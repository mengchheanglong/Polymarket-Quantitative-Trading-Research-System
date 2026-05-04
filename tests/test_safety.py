from pathlib import Path

import pytest

from src.config import AgentConfig
from src.main import main
from src.safety import SafetyError, enforce_paper_only


def test_dry_run_defaults_true():
    assert AgentConfig().dry_run is True


def test_live_trading_cannot_be_enabled_with_dry_run_false():
    with pytest.raises(SafetyError):
        enforce_paper_only(dry_run=False, execution_mode="paper")


def test_live_trading_flag_causes_error(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING", "true")

    with pytest.raises(SafetyError):
        enforce_paper_only(dry_run=True, execution_mode="paper")


def test_cli_live_trading_flag_exits_with_safety_error(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("LIVE_TRADING", "true")

    exit_code = main(["--db", str(tmp_path / "paper.sqlite3"), "report"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Safety error:" in captured.err


def test_env_example_has_no_private_key_field():
    env_example = Path(".env.example").read_text(encoding="utf-8")

    assert "PRIVATE_KEY" not in env_example


def test_no_live_execution_dependencies_or_wallet_code():
    forbidden_tokens = [
        "py_clob_client",
        "web3",
        "eth_account",
        "private_key",
        "submit_order",
        "place_order",
        "create_wallet",
        "signing",
        "signer",
        "PRIVATE_KEY",
    ]
    for path in Path("src").rglob("*.py"):
        raw_text = path.read_text(encoding="utf-8")
        text = raw_text.lower()
        for token in forbidden_tokens:
            haystack = raw_text if token == "PRIVATE_KEY" else text
            needle = token if token == "PRIVATE_KEY" else token.lower()
            assert needle not in haystack, f"{token} found in {path}"
    env_text = Path(".env.example").read_text(encoding="utf-8")
    assert "PRIVATE_KEY" not in env_text
