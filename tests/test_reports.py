from datetime import datetime, timezone

from src.reports.summary import build_report
from src.storage.sqlite import SQLiteStore


def test_max_equity_drawdown_uses_equity_not_cash_balance(tmp_path):
    store = SQLiteStore(tmp_path / "paper.sqlite3")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    try:
        store.set_balance(now, 900.0)
        store.log_equity_snapshot(now, cash_balance=900.0, open_position_value=100.0, position_exposure=101.0)
        store.set_balance(now, 990.0)
        store.log_equity_snapshot(now, cash_balance=990.0, open_position_value=0.0, position_exposure=0.0)

        report = build_report(store, starting_balance=1000.0)

        assert report.max_equity_drawdown == 10.0
        assert report.max_position_exposure == 101.0
        assert report.total_equity == 990.0
    finally:
        store.close()
