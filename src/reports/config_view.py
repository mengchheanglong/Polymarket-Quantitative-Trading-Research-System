from __future__ import annotations

from collections.abc import Mapping


_CONFIG_LABELS = (
    ("min_edge", "min_edge"),
    ("max_spread", "max_spread"),
    ("pair_cost_threshold", "pair_cost_threshold"),
    ("fee_bps", "fee_bps"),
    ("slippage_bps", "slippage_bps"),
    ("max_position_pct", "max_position_pct"),
    ("max_position_usd", "max_position_usd"),
    ("failed_fill_probability", "failed_fill_probability"),
    ("pair_cost_failed_second_leg_probability", "pair_cost_failed_second_leg_probability"),
    ("source_filter", "source_filter"),
    ("since", "since"),
    ("until", "until"),
    ("close_mode", "close_mode"),
    ("active_only", "active_only"),
    ("min_seconds_to_expiry_filter", "min_seconds_to_expiry_filter"),
    ("max_seconds_to_expiry_filter", "max_seconds_to_expiry_filter"),
)


def parse_config_notes(notes: str | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if not notes:
        return values
    for chunk in str(notes).split(";"):
        item = chunk.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def merged_config_view(notes_values: list[str | None]) -> dict[str, str]:
    parsed = [parse_config_notes(notes) for notes in notes_values if notes is not None]
    if not parsed:
        return {}
    merged: dict[str, str] = {}
    keys = {key for values in parsed for key in values}
    for key in sorted(keys):
        variants = {values.get(key, "n/a") for values in parsed}
        merged[key] = variants.pop() if len(variants) == 1 else "mixed"
    return merged


def format_config_view(values: Mapping[str, str]) -> str:
    items = []
    for key, label in _CONFIG_LABELS:
        value = values.get(key)
        if value is None:
            continue
        items.append(f"{label}={value}")
    extra_keys = [key for key in values if key not in {name for name, _ in _CONFIG_LABELS}]
    for key in sorted(extra_keys):
        items.append(f"{key}={values[key]}")
    return " | ".join(items) if items else "n/a"
