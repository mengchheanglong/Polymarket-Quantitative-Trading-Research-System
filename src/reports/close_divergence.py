from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any


def build_close_divergence_report(
    *,
    strategy: str,
    source_filter: str | None,
    session_id: str | None,
    active_only: bool,
    tiny: bool,
    mark_rows: list[Any],
    approx_rows: list[Any],
) -> str:
    lines = [
        "Close mode divergence report",
        f"Strategy: {strategy}",
        f"Source filter: {source_filter or 'all'}",
        f"Session ID: {session_id or 'none'}",
        f"Active only: {active_only}",
        f"Tiny: {tiny}",
    ]
    mark_map = {_trade_key(row): row for row in mark_rows}
    approx_map = {_trade_key(row): row for row in approx_rows}
    shared_keys = sorted(set(mark_map) & set(approx_map))
    if not shared_keys:
        lines.append("No matching trades were present in both close modes.")
        return "\n".join(lines)

    both_win = []
    both_loss = []
    mark_win_approx_loss = []
    mark_loss_approx_win = []
    for key in shared_keys:
        mark = mark_map[key]
        approx = approx_map[key]
        mark_win = _is_win(mark)
        approx_win = _is_win(approx)
        if mark_win and approx_win:
            both_win.append((mark, approx))
        elif (not mark_win) and (not approx_win):
            both_loss.append((mark, approx))
        elif mark_win and (not approx_win):
            mark_win_approx_loss.append((mark, approx))
        else:
            mark_loss_approx_win.append((mark, approx))

    divergent = mark_win_approx_loss + mark_loss_approx_win
    lines.extend(
        [
            f"Trades in both close modes: {len(shared_keys)}",
            f"Winning in both: {len(both_win)}",
            f"Losing in both: {len(both_loss)}",
            f"Winning in mark-to-market but losing at approximate-expiry: {len(mark_win_approx_loss)}",
            f"Losing in mark-to-market but winning at approximate-expiry: {len(mark_loss_approx_win)}",
            f"Average entry price of divergent trades: {_fmt_float(_avg([float(pair[0]['entry_price']) for pair in divergent]))}",
            f"Average seconds-to-expiry of divergent trades: {_fmt_float(_avg([_seconds_to_expiry(pair[0]) for pair in divergent]))}",
            f"Divergent side breakdown: {_fmt_map(_breakdown(divergent, lambda pair: str(pair[0]['direction'])))}",
            f"Divergent asset breakdown: {_fmt_map(_breakdown(divergent, lambda pair: str(pair[0]['asset'])))}",
            f"Divergent duration breakdown: {_fmt_map(_breakdown(divergent, lambda pair: _duration_label(pair[0])))}",
        ]
    )
    for label, pairs in (
        ("mark-win / approx-loss", mark_win_approx_loss),
        ("mark-loss / approx-win", mark_loss_approx_win),
    ):
        if not pairs:
            continue
        lines.append(label + ":")
        for mark, approx in pairs[:10]:
            lines.append(
                " | ".join(
                    [
                        str(mark["market_slug"]),
                        f"asset={mark['asset']}",
                        f"side={mark['direction']}",
                        f"duration={_duration_label(mark)}",
                        f"entry_price={float(mark['entry_price']):.4f}",
                        f"seconds_to_expiry={_seconds_to_expiry(mark):.1f}",
                        f"mark_pnl={_fmt_money(mark['pnl'])}",
                        f"approx_pnl={_fmt_money(approx['pnl'])}",
                    ]
                )
            )
    return "\n".join(lines)


def _trade_key(row: Any) -> tuple[str, str, str]:
    return (str(row["market_slug"]), str(row["direction"]), str(row["opened_at"]))


def _is_win(row: Any) -> bool:
    pnl = float(row["pnl"] or 0.0)
    return pnl > 0


def _seconds_to_expiry(row: Any) -> float:
    opened = _parse_iso(str(row["opened_at"]))
    expiry = _parse_iso(str(row["window_end"]))
    return max(0.0, (expiry - opened).total_seconds())


def _duration_label(row: Any) -> str:
    if not row["window_start"] or not row["window_end"]:
        return "unknown"
    start = _parse_iso(str(row["window_start"]))
    end = _parse_iso(str(row["window_end"]))
    minutes = (end - start).total_seconds() / 60.0
    if 4.0 <= minutes <= 6.0:
        return "5m"
    if 14.0 <= minutes <= 16.0:
        return "15m"
    return "unknown"


def _breakdown(pairs: list[tuple[Any, Any]], selector) -> dict[str, int]:
    counts = Counter()
    for pair in pairs:
        counts[str(selector(pair))] += 1
    return dict(counts)


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _fmt_map(values: dict[str, int]) -> str:
    if not values:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in values.items())


def _fmt_float(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.4f}"


def _fmt_money(value) -> str:
    if value is None:
        return "n/a"
    return f"${float(value):.2f}"


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
