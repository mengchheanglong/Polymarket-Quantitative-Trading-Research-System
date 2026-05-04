from __future__ import annotations

from src.storage.sqlite import SQLiteStore


def build_trade_ledger(store: SQLiteStore, run_id: str | None = None) -> str:
    lines = ["Simulated trade ledger"]
    if run_id:
        lines.append(f"Run: {run_id}")
    trade_rows = store.trade_rows(run_id=run_id)
    if trade_rows:
        lines.append("Trades:")
        for row in trade_rows:
            lines.append(
                " | ".join(
                    [
                        str(row["market_slug"]),
                        f"asset={row['asset']}",
                        f"side={row['direction']}",
                        f"entry={float(row['entry_price']):.4f}",
                        f"exit={_fmt(row['exit_price'])}",
                        f"shares={float(row['shares']):.4f}",
                        f"entry_fee={float(row['entry_fee']):.4f}",
                        f"exit_fee={_fmt(row['exit_fee'])}",
                        f"slippage={float(row['slippage_cost']):.4f}",
                        f"pnl={_fmt(row['pnl'])}",
                        f"status={row['status']}",
                        f"close_mode={row['close_mode'] or 'n/a'}",
                        f"result={row['result'] or 'OPEN'}",
                        f"note={row['settlement_note'] or 'n/a'}",
                    ]
                )
            )
    else:
        lines.append("Trades: none")

    skipped_rows = store.skipped_opportunity_rows(run_id=run_id)
    if skipped_rows:
        lines.append("Skipped opportunities:")
        for row in skipped_rows:
            lines.append(
                " | ".join(
                    [
                        str(row["market_slug"]),
                        f"asset={row['asset']}",
                        f"side={row['direction']}",
                        f"market_price={_fmt(row['market_price'])}",
                        f"spread={_fmt(row['spread'])}",
                        f"edge={float(row['edge']):.4f}",
                        f"reason={row['reason']}",
                    ]
                )
            )
    else:
        lines.append("Skipped opportunities: none")
    return "\n".join(lines)


def _fmt(value) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.4f}"
