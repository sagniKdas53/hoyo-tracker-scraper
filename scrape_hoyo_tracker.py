#!/usr/bin/env python3
"""Scrape HoYoverse codes and calendars into stable local artifacts."""

# pylint: disable=too-many-lines

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any, Callable, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Safari/537.36"
)
DEFAULT_GAMES = ("genshin", "starrail")
DEFAULT_INCLUDE = "all"
DEFAULT_TIMEZONE = "UTC"
ENV_PREFIX = "HOYO_TRACKER_"
NEXT_PUSH_PATTERN = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', flags=re.S)
CODE_VARIANT_SPLIT_RE = re.compile(r"\s*(?:,|/|;|\||\n)\s*")

GAME_CONFIG: dict[str, dict[str, str]] = {
    "genshin": {
        "label": "Genshin Impact",
        "ennead_codes_url": "https://api.ennead.cc/mihoyo/genshin/codes",
        "ennead_calendar_url": "https://api.ennead.cc/mihoyo/genshin/calendar",
        "crimson_codes_url": "https://www.crimsonwitch.com/codes/Genshin_Impact",
        "redemption_url_template": "https://genshin.hoyoverse.com/en/gift?code={code}",
    },
    "starrail": {
        "label": "Honkai: Star Rail",
        "ennead_codes_url": "https://api.ennead.cc/mihoyo/starrail/codes",
        "ennead_calendar_url": "https://api.ennead.cc/mihoyo/starrail/calendar",
        "crimson_codes_url": "https://www.crimsonwitch.com/codes/Honkai_Star_Rail",
        "redemption_url_template": "https://hsr.hoyoverse.com/gift?code={code}",
    },
}

RECORD_TYPES = ("codes", "events", "banners", "challenges")


@dataclass
class Provenance:
    fetched_at_utc: str
    sources: dict[str, Any]
    extraction_method: str
    notes: list[str]


def env_value(name: str, default: str | None = None) -> str | None:
    return os.getenv(f"{ENV_PREFIX}{name}", default)


def parse_bool_env(name: str, default: bool = False) -> bool:
    raw = env_value(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value for {ENV_PREFIX}{name}: {raw}")


def fetch_text(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(request, timeout=30) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def fetch_json(url: str) -> Any:
    return json.loads(fetch_text(url))


def decode_flight_string(value: str) -> str:
    return bytes(unescape(value), "utf-8").decode("unicode_escape")


def extract_push_payloads(html: str) -> list[str]:
    return NEXT_PUSH_PATTERN.findall(html)


def extract_crimson_initial_codes(html: str) -> list[dict[str, Any]]:
    payloads = extract_push_payloads(html)
    if not payloads:
        raise RuntimeError("No Next.js flight payloads were found in Crimson Witch HTML")

    for payload in payloads:
        decoded = decode_flight_string(payload)
        match = re.search(r'"initialCodes":(\[.*?\]),"slug":"[^"]+"', decoded, flags=re.S)
        if not match:
            continue
        candidate = json.loads(match.group(1))
        if isinstance(candidate, list):
            return candidate

    raise RuntimeError("Crimson Witch initialCodes payload was not found")


def parse_timestamp(value: Any) -> datetime | None:
    if value in (None, "", 0):
        return None

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)

    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    return None


def iso_or_none(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def convert_to_output_tz(value: datetime | None, output_tz: ZoneInfo) -> str | None:
    return value.astimezone(output_tz).isoformat() if value is not None else None


def format_duration(delta_seconds: float) -> str:
    remaining = max(int(delta_seconds), 0)
    days, rem = divmod(remaining, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    return f"{days}d {hours}h {minutes}m"


def load_timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone '{value}'") from exc


def canonicalize_games(value: str) -> list[str]:
    normalized = [part.strip().lower() for part in value.split(",") if part.strip()]
    if not normalized:
        raise ValueError("At least one game must be supplied")

    aliases = {
        "gi": "genshin",
        "genshinimpact": "genshin",
        "genshin_impact": "genshin",
        "hsr": "starrail",
        "star_rail": "starrail",
        "honkai_star_rail": "starrail",
    }
    seen: list[str] = []
    for item in normalized:
        canonical = aliases.get(item.replace("-", "_"), item.replace("-", "_"))
        if canonical not in GAME_CONFIG:
            valid = ", ".join(sorted(GAME_CONFIG))
            raise ValueError(f"Unsupported game '{item}'. Expected one of: {valid}")
        if canonical not in seen:
            seen.append(canonical)
    return seen


def parse_include(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    aliases = {
        "code": "codes",
        "event": "events",
        "banner": "banners",
        "challenge": "challenges",
        "everything": "all",
    }
    resolved = aliases.get(normalized, normalized)
    if resolved not in {"all", "codes", "events", "banners", "challenges"}:
        raise ValueError(
            "Expected include mode to be one of: "
            "all, codes, events, banners, challenges"
        )
    return resolved


def parse_args() -> argparse.Namespace:
    env_games = (
        env_value("GAMES", ",".join(DEFAULT_GAMES))
        or ",".join(DEFAULT_GAMES)
    )
    env_timezone = env_value("TIMEZONE", DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE
    env_include = env_value("INCLUDE", DEFAULT_INCLUDE) or DEFAULT_INCLUDE

    parser = argparse.ArgumentParser(description="Scrape HoYoverse codes and calendars.")
    parser.add_argument(
        "--games",
        default=env_games,
        help=(
            "Comma-separated games to scrape. Supported: "
            f"{', '.join(sorted(GAME_CONFIG))}. Default: {env_games}."
        ),
    )
    parser.add_argument(
        "--include",
        default=env_include,
        help=(
            "Which record types to include: all, codes, events, banners, "
            f"challenges. Default: {env_include}."
        ),
    )
    parser.add_argument(
        "--active-only",
        action="store_true",
        default=parse_bool_env("ACTIVE_ONLY", default=False),
        help="Exclude expired and inactive records from the emitted payloads.",
    )
    parser.add_argument(
        "--timezone",
        default=env_timezone,
        help=f"IANA timezone used for output/display timestamps. Default: {env_timezone}.",
    )
    return parser.parse_args()


def split_code_variants(value: Any) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, list):
        parts = [str(item).strip() for item in value]
    else:
        parts = CODE_VARIANT_SPLIT_RE.split(str(value).strip())

    seen: list[str] = []
    for part in parts:
        if not part:
            continue
        if part not in seen:
            seen.append(part)
    return seen


def code_match_keys(code: str, variants: list[str]) -> set[str]:
    return {normalize_code_key(item) for item in [code, *variants] if item}


def normalize_code_key(value: str) -> str:
    return value.strip().upper()


def rewards_from_ennead(raw_rewards: list[str]) -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = []
    for reward in raw_rewards:
        text = reward.strip()
        match = re.match(r"^(.*?)\s*[x×]\s*([\d,]+)$", text, flags=re.I)
        if match:
            name = match.group(1).strip()
            amount_text = match.group(2).replace(",", "")
            amount: int | str
            amount = int(amount_text) if amount_text.isdigit() else match.group(2)
            parsed.append({"item": name, "qty": amount})
        else:
            parsed.append({"item": text, "qty": None})
    return parsed


def summarize_rewards(rewards: list[dict[str, Any]]) -> list[str]:
    summary: list[str] = []
    for reward in rewards:
        item = reward.get("item") or reward.get("name") or "Unknown"
        qty = reward.get("qty", reward.get("amount"))
        if qty in (None, ""):
            summary.append(str(item))
        else:
            summary.append(f"{item} x{qty}")
    return summary


def determine_code_status(
    ennead_status: str | None,
    start_at_utc: datetime | None,
    end_at_utc: datetime | None,
    now_utc: datetime,
) -> tuple[str, bool]:
    if start_at_utc is not None and start_at_utc > now_utc:
        return ("scheduled", False)
    if end_at_utc is not None and end_at_utc <= now_utc:
        return ("inactive", False)
    if ennead_status == "inactive":
        return ("inactive", False)
    return ("active", True)


def build_redemption_url(game: str, code: str) -> str:
    template = GAME_CONFIG[game]["redemption_url_template"]
    return template.format(code=quote_plus(code))


def normalize_crimson_code(
    game: str,
    record: dict[str, Any],
    output_tz: ZoneInfo,
    now_utc: datetime,
) -> dict[str, Any]:
    code = str(record.get("code") or "").strip()
    start_at_utc = parse_timestamp(record.get("start_date"))
    end_at_utc = parse_timestamp(record.get("expires"))
    added_at_utc = parse_timestamp(record.get("added"))
    status, is_redeemable_now = determine_code_status(None, start_at_utc, end_at_utc, now_utc)
    rewards = [
        {"item": reward.get("item"), "qty": reward.get("qty")}
        for reward in (record.get("rewards") or [])
        if isinstance(reward, dict)
    ]
    return {
        "game": game,
        "game_label": GAME_CONFIG[game]["label"],
        "record_type": "code",
        "source_name": "Crimson Witch",
        "source_url": GAME_CONFIG[game]["crimson_codes_url"],
        "code": code,
        "code_variants": split_code_variants(record.get("code_variants")),
        "redemption_url": build_redemption_url(game, code),
        "status": status,
        "is_redeemable_now": is_redeemable_now,
        "has_expired": status == "inactive",
        "expires_in": (
            format_duration((end_at_utc - now_utc).total_seconds())
            if end_at_utc is not None
            else None
        ),
        "start_at_utc": iso_or_none(start_at_utc),
        "end_at_utc": iso_or_none(end_at_utc),
        "start_at_output_tz": convert_to_output_tz(start_at_utc, output_tz),
        "end_at_output_tz": convert_to_output_tz(end_at_utc, output_tz),
        "added_at_utc": iso_or_none(added_at_utc),
        "added_at_output_tz": convert_to_output_tz(added_at_utc, output_tz),
        "rewards": rewards,
        "raw_rewards": summarize_rewards(rewards),
    }


def normalize_ennead_code(
    game: str,
    record: dict[str, Any],
    ennead_status: str,
    now_utc: datetime,
) -> dict[str, Any]:
    code = str(record.get("code") or "").strip()
    rewards = rewards_from_ennead([str(item) for item in (record.get("rewards") or [])])
    status, is_redeemable_now = determine_code_status(ennead_status, None, None, now_utc)
    return {
        "game": game,
        "game_label": GAME_CONFIG[game]["label"],
        "record_type": "code",
        "source_name": "Ennead",
        "source_url": GAME_CONFIG[game]["ennead_codes_url"],
        "code": code,
        "code_variants": [],
        "redemption_url": build_redemption_url(game, code),
        "status": status,
        "is_redeemable_now": is_redeemable_now,
        "has_expired": status == "inactive",
        "expires_in": None,
        "start_at_utc": None,
        "end_at_utc": None,
        "start_at_output_tz": None,
        "end_at_output_tz": None,
        "added_at_utc": None,
        "added_at_output_tz": None,
        "rewards": rewards,
        "raw_rewards": [str(item) for item in (record.get("rewards") or [])],
    }


def merge_code_records(
    ennead_records: list[dict[str, Any]],
    crimson_records: list[dict[str, Any]],
    now_utc: datetime,
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    removed_indices: set[int] = set()

    def rebuild_index() -> dict[str, int]:
        index_by_key: dict[str, int] = {}
        for position, record in enumerate(merged):
            if position in removed_indices:
                continue
            for key in code_match_keys(record["code"], record.get("code_variants", [])):
                index_by_key[key] = position
        return index_by_key

    def register(record: dict[str, Any]) -> None:
        merged.append(record)

    for record in ennead_records:
        register(record)

    for record in crimson_records:
        index_by_key = rebuild_index()
        primary_key = normalize_code_key(record["code"])
        variant_keys = [normalize_code_key(item) for item in record.get("code_variants", [])]

        primary_index = index_by_key.get(primary_key)
        matched_indices = []
        if primary_index is not None:
            matched_indices.append(primary_index)
        for key in variant_keys:
            match_index = index_by_key.get(key)
            if match_index is not None and match_index not in matched_indices:
                matched_indices.append(match_index)

        if not matched_indices:
            register(record)
            continue

        current = merged[matched_indices[0]]
        alias_pool = []
        for index in matched_indices[1:]:
            alias_pool.append(merged[index]["code"])
            alias_pool.extend(split_code_variants(merged[index].get("code_variants")))
        alias_pool.extend(split_code_variants(current.get("code_variants")))
        alias_pool.extend(split_code_variants(record.get("code_variants")))
        deduped_variants: list[str] = []
        for item in alias_pool:
            if item == current["code"]:
                continue
            if item not in deduped_variants:
                deduped_variants.append(item)

        for index in matched_indices[1:]:
            removed_indices.add(index)

        current_source = current.get("source_name") or "Ennead"
        if "Crimson Witch" not in current_source:
            current["source_name"] = "Ennead + Crimson Witch"
        else:
            current["source_name"] = current_source

        current["code_variants"] = deduped_variants
        if record.get("rewards"):
            current["rewards"] = record["rewards"]
            current["raw_rewards"] = record["raw_rewards"]
        for field in (
            "added_at_utc",
            "added_at_output_tz",
            "start_at_utc",
            "start_at_output_tz",
            "end_at_utc",
            "end_at_output_tz",
        ):
            if record.get(field) is not None:
                current[field] = record[field]

        start_at = parse_timestamp(current.get("start_at_utc"))
        end_at = parse_timestamp(current.get("end_at_utc"))
        status, is_redeemable_now = determine_code_status(
            "inactive" if current.get("status") == "inactive" else "active",
            start_at,
            end_at,
            now_utc,
        )
        current["status"] = status
        current["is_redeemable_now"] = is_redeemable_now
        current["has_expired"] = status == "inactive"
        current["expires_in"] = (
            format_duration((end_at - now_utc).total_seconds())
            if end_at is not None
            else None
        )

    return sorted(
        [record for index, record in enumerate(merged) if index not in removed_indices],
        key=lambda row: (
            1 if row["status"] == "inactive" else 0,
            row.get("end_at_utc") or "9999-12-31T23:59:59+00:00",
            row.get("added_at_utc") or "9999-12-31T23:59:59+00:00",
            row["code"],
        ),
    )


def normalize_calendar_record(
    game: str,
    record_type: str,
    record: dict[str, Any],
    output_tz: ZoneInfo,
    now_utc: datetime,
) -> dict[str, Any]:
    start_at_utc = parse_timestamp(record.get("start_time"))
    end_at_utc = parse_timestamp(record.get("end_time"))
    has_expired = end_at_utc <= now_utc if end_at_utc is not None else False
    expires_in = (
        format_duration((end_at_utc - now_utc).total_seconds())
        if end_at_utc is not None
        else None
    )

    payload = {
        "game": game,
        "game_label": GAME_CONFIG[game]["label"],
        "record_type": record_type[:-1] if record_type.endswith("s") else record_type,
        "source_name": "Ennead Calendar",
        "source_url": GAME_CONFIG[game]["ennead_calendar_url"],
        "id": record.get("id"),
        "name": record.get("name"),
        "description": record.get("description") or None,
        "type_name": record.get("type_name"),
        "version": record.get("version"),
        "image_url": record.get("image_url"),
        "start_at_utc": iso_or_none(start_at_utc),
        "end_at_utc": iso_or_none(end_at_utc),
        "start_at_output_tz": convert_to_output_tz(start_at_utc, output_tz),
        "end_at_output_tz": convert_to_output_tz(end_at_utc, output_tz),
        "has_expired": has_expired,
        "expires_in": expires_in,
        "rewards": record.get("rewards") or [],
        "special_reward": record.get("special_reward"),
    }
    if "characters" in record:
        payload["characters"] = record.get("characters") or []
    if "weapons" in record:
        payload["weapons"] = record.get("weapons") or []
    if "light_cones" in record:
        payload["light_cones"] = record.get("light_cones") or []
    return payload


def filter_code_records(rows: list[dict[str, Any]], active_only: bool) -> list[dict[str, Any]]:
    if not active_only:
        return rows
    return [
        row
        for row in rows
        if row.get("status") == "active"
        and row.get("is_redeemable_now") is True
    ]


def filter_calendar_records(rows: list[dict[str, Any]], active_only: bool) -> list[dict[str, Any]]:
    if not active_only:
        return rows
    return [row for row in rows if row.get("has_expired") is not True]


def sort_calendar_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            1 if row.get("has_expired") else 0,
            row.get("end_at_utc") or "9999-12-31T23:59:59+00:00",
            row.get("name") or "",
        ),
    )


def selected_record_types(include: str) -> set[str]:
    return set(RECORD_TYPES) if include == "all" else {include}


def collect_game_data(
    game: str,
    output_tz: ZoneInfo,
    now_utc: datetime,
) -> dict[str, list[dict[str, Any]]]:
    config = GAME_CONFIG[game]

    ennead_codes_raw = fetch_json(config["ennead_codes_url"])
    crimson_codes_raw = extract_crimson_initial_codes(fetch_text(config["crimson_codes_url"]))
    calendar_raw = fetch_json(config["ennead_calendar_url"])

    ennead_codes: list[dict[str, Any]] = []
    for status_name in ("active", "inactive"):
        for record in ennead_codes_raw.get(status_name, []):
            ennead_codes.append(
                normalize_ennead_code(
                    game,
                    record,
                    status_name,
                    now_utc,
                )
            )

    crimson_codes = [
        normalize_crimson_code(game, record, output_tz, now_utc)
        for record in crimson_codes_raw
    ]
    codes = merge_code_records(ennead_codes, crimson_codes, now_utc)

    events = sort_calendar_records(
        [
            normalize_calendar_record(game, "events", record, output_tz, now_utc)
            for record in calendar_raw.get("events", [])
        ]
    )
    banners = sort_calendar_records(
        [
            normalize_calendar_record(game, "banners", record, output_tz, now_utc)
            for record in calendar_raw.get("banners", [])
        ]
    )
    challenges = sort_calendar_records(
        [
            normalize_calendar_record(
                game, "challenges", record, output_tz, now_utc
            )
            for record in calendar_raw.get("challenges", [])
        ]
    )

    return {
        "codes": codes,
        "events": events,
        "banners": banners,
        "challenges": challenges,
    }


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def flatten_code_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "game": row.get("game"),
        "game_label": row.get("game_label"),
        "record_type": row.get("record_type"),
        "source_name": row.get("source_name"),
        "source_url": row.get("source_url"),
        "code": row.get("code"),
        "code_variants": json.dumps(row.get("code_variants") or [], ensure_ascii=True),
        "redemption_url": row.get("redemption_url"),
        "status": row.get("status"),
        "is_redeemable_now": row.get("is_redeemable_now"),
        "has_expired": row.get("has_expired"),
        "expires_in": row.get("expires_in"),
        "added_at_utc": row.get("added_at_utc"),
        "added_at_output_tz": row.get("added_at_output_tz"),
        "start_at_utc": row.get("start_at_utc"),
        "end_at_utc": row.get("end_at_utc"),
        "start_at_output_tz": row.get("start_at_output_tz"),
        "end_at_output_tz": row.get("end_at_output_tz"),
        "rewards": json.dumps(row.get("rewards") or [], ensure_ascii=True),
        "raw_rewards": json.dumps(row.get("raw_rewards") or [], ensure_ascii=True),
    }


def flatten_calendar_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "game": row.get("game"),
        "game_label": row.get("game_label"),
        "record_type": row.get("record_type"),
        "source_name": row.get("source_name"),
        "source_url": row.get("source_url"),
        "id": row.get("id"),
        "name": row.get("name"),
        "description": row.get("description"),
        "type_name": row.get("type_name"),
        "version": row.get("version"),
        "image_url": row.get("image_url"),
        "start_at_utc": row.get("start_at_utc"),
        "end_at_utc": row.get("end_at_utc"),
        "start_at_output_tz": row.get("start_at_output_tz"),
        "end_at_output_tz": row.get("end_at_output_tz"),
        "has_expired": row.get("has_expired"),
        "expires_in": row.get("expires_in"),
        "rewards": json.dumps(row.get("rewards") or [], ensure_ascii=True),
        "special_reward": json.dumps(row.get("special_reward"), ensure_ascii=True),
        "characters": json.dumps(row.get("characters") or [], ensure_ascii=True),
        "weapons": json.dumps(row.get("weapons") or [], ensure_ascii=True),
        "light_cones": json.dumps(row.get("light_cones") or [], ensure_ascii=True),
    }


def build_mode_suffix(active_only: bool, include: str) -> str:
    parts: list[str] = []
    if active_only:
        parts.append("active_only")
    if include != "all":
        parts.append(include)
    return "_".join(parts) if parts else "all"


def compute_counts(payload_games: dict[str, dict[str, list[dict[str, Any]]]]) -> dict[str, int]:
    counts = {record_type: 0 for record_type in RECORD_TYPES}
    for game_payload in payload_games.values():
        for record_type in RECORD_TYPES:
            counts[record_type] += len(game_payload.get(record_type, []))
    counts["total"] = sum(counts.values())
    return counts


def write_summary(
    path: Path,
    payload: dict[str, Any],
) -> None:
    lines = [
        "# Hoyo Tracker Scrape Summary",
        "",
        f"- Generated at UTC: {payload['scraped_at_utc']}",
        f"- Games: {', '.join(payload['filters']['games'])}",
        f"- Output timezone: {payload['filters']['timezone']}",
        f"- Include mode: {payload['filters']['include']}",
        f"- Active-only mode: {payload['filters']['active_only']}",
        f"- Codes: {payload['counts']['codes']}",
        f"- Events: {payload['counts']['events']}",
        f"- Banners: {payload['counts']['banners']}",
        f"- Challenges: {payload['counts']['challenges']}",
        f"- Total records: {payload['counts']['total']}",
        "",
        "## Current extraction notes",
        "",
        "- Ennead is used as the primary JSON source for codes and calendars.",
        (
            "- Crimson Witch is parsed from embedded Next.js flight payload "
            "content to enrich code metadata."
        ),
        "- Code outputs include direct HoYoverse redemption URLs.",
        "- Calendar timestamps are emitted in UTC and the requested output timezone.",
        "",
        "## Next items to expire",
        "",
    ]

    preview: list[dict[str, Any]] = []
    for game_payload in payload["games"].values():
        preview.extend(game_payload.get("codes", []))
        preview.extend(game_payload.get("events", []))
        preview.extend(game_payload.get("banners", []))
        preview.extend(game_payload.get("challenges", []))

    def preview_key(row: dict[str, Any]) -> tuple[int, str, str]:
        if row.get("record_type") == "code":
            inactive = row.get("status") != "active"
            end_at = row.get("end_at_utc") or "9999-12-31T23:59:59+00:00"
            return (1 if inactive else 0, end_at, row.get("code") or "")
        return (
            1 if row.get("has_expired") else 0,
            row.get("end_at_utc") or "9999-12-31T23:59:59+00:00",
            row.get("name") or "",
        )

    for row in sorted(preview, key=preview_key)[:12]:
        label = row.get("code") if row.get("record_type") == "code" else row.get("name")
        lines.append(
            (
                f"- [{row.get('game')}] [{row.get('record_type')}] {label} | "
                f"ends {row.get('end_at_output_tz')} | time left "
                f"{row.get('expires_in')} | "
                f"{row.get('redemption_url', row.get('source_url'))}"
            )
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_run_outputs(
    output_dir: Path,
    payload: dict[str, Any],
    selected_games: dict[str, dict[str, list[dict[str, Any]]]],
) -> None:
    suffix = build_mode_suffix(payload["filters"]["active_only"], payload["filters"]["include"])

    write_json(output_dir / "latest.json", payload)
    write_json(output_dir / f"latest_{suffix}.json", payload)

    csv_specs: dict[
        str,
        tuple[list[str], Callable[[dict[str, Any]], dict[str, Any]]],
    ] = {
        "codes": (
            [
                "game",
                "game_label",
                "record_type",
                "source_name",
                "source_url",
                "code",
                "code_variants",
                "redemption_url",
                "status",
                "is_redeemable_now",
                "has_expired",
                "expires_in",
                "added_at_utc",
                "added_at_output_tz",
                "start_at_utc",
                "end_at_utc",
                "start_at_output_tz",
                "end_at_output_tz",
                "rewards",
                "raw_rewards",
            ],
            flatten_code_row,
        ),
        "events": (
            [
                "game",
                "game_label",
                "record_type",
                "source_name",
                "source_url",
                "id",
                "name",
                "description",
                "type_name",
                "version",
                "image_url",
                "start_at_utc",
                "end_at_utc",
                "start_at_output_tz",
                "end_at_output_tz",
                "has_expired",
                "expires_in",
                "rewards",
                "special_reward",
                "characters",
                "weapons",
                "light_cones",
            ],
            flatten_calendar_row,
        ),
        "banners": (
            [
                "game",
                "game_label",
                "record_type",
                "source_name",
                "source_url",
                "id",
                "name",
                "description",
                "type_name",
                "version",
                "image_url",
                "start_at_utc",
                "end_at_utc",
                "start_at_output_tz",
                "end_at_output_tz",
                "has_expired",
                "expires_in",
                "rewards",
                "special_reward",
                "characters",
                "weapons",
                "light_cones",
            ],
            flatten_calendar_row,
        ),
        "challenges": (
            [
                "game",
                "game_label",
                "record_type",
                "source_name",
                "source_url",
                "id",
                "name",
                "description",
                "type_name",
                "version",
                "image_url",
                "start_at_utc",
                "end_at_utc",
                "start_at_output_tz",
                "end_at_output_tz",
                "has_expired",
                "expires_in",
                "rewards",
                "special_reward",
                "characters",
                "weapons",
                "light_cones",
            ],
            flatten_calendar_row,
        ),
    }

    for record_type, (fieldnames, serializer) in csv_specs.items():
        if payload["filters"]["include"] not in {"all", record_type}:
            continue
        rows: list[dict[str, Any]] = []
        serializer_fn = cast(
            Callable[[dict[str, Any]], dict[str, Any]],
            serializer,
        )
        for game_payload in selected_games.values():
            rows.extend(
                serializer_fn(row)  # pylint: disable=not-callable
                for row in game_payload.get(record_type, [])
            )
        write_csv(output_dir / f"{record_type}.csv", rows, fieldnames)


def main() -> int:
    try:
        args = parse_args()
        games = canonicalize_games(args.games)
        include = parse_include(args.include)
        output_tz = load_timezone(args.timezone)
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    base_dir = Path(__file__).resolve().parent
    output_dir = base_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    now_utc = datetime.now(timezone.utc)
    include_types = selected_record_types(include)
    unfiltered_games: dict[str, dict[str, list[dict[str, Any]]]] = {}

    try:
        for game in games:
            unfiltered_games[game] = collect_game_data(game, output_tz, now_utc)
    except (HTTPError, URLError) as exc:
        print(f"Network error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Extraction error: {exc}", file=sys.stderr)
        return 1

    selected_games_payload: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for game, game_payload in unfiltered_games.items():
        selected_games_payload[game] = {}
        for record_type in RECORD_TYPES:
            rows = game_payload[record_type]
            if record_type == "codes":
                filtered = filter_code_records(rows, args.active_only)
            else:
                filtered = filter_calendar_records(rows, args.active_only)
            selected_games_payload[game][record_type] = (
                filtered if record_type in include_types else []
            )

    payload = {
        "scraped_at_utc": now_utc.isoformat(),
        "sources": {
            "ennead": {
                "codes": {game: GAME_CONFIG[game]["ennead_codes_url"] for game in games},
                "calendar": {game: GAME_CONFIG[game]["ennead_calendar_url"] for game in games},
            },
            "crimson_witch": {
                "codes": {game: GAME_CONFIG[game]["crimson_codes_url"] for game in games},
            },
        },
        "filters": {
            "games": games,
            "include": include,
            "active_only": args.active_only,
            "timezone": args.timezone,
        },
        "counts": compute_counts(selected_games_payload),
        "unfiltered_counts": compute_counts(
            {
                game: {
                    record_type: rows if record_type in include_types else []
                    for record_type, rows in game_payload.items()
                }
                for game, game_payload in unfiltered_games.items()
            }
        ),
        "games": selected_games_payload,
    }

    provenance = Provenance(
        fetched_at_utc=payload["scraped_at_utc"],
        sources=payload["sources"],
        extraction_method=(
            "Fetch Ennead JSON endpoints for codes and calendars, then parse Crimson Witch "
            "embedded self.__next_f.push() payloads for code enrichment and scheduled codes."
        ),
        notes=[
            "All sources used by this scraper are unofficial community-maintained sources.",
            (
                "Crimson Witch code metadata is extracted from embedded "
                "Next.js flight payload data, not a documented public JSON "
                "endpoint."
            ),
            (
                "Genshin and Star Rail outputs include direct official "
                "redemption URLs for each normalized code."
            ),
            f"Output timestamps were emitted in UTC and the requested timezone '{args.timezone}'.",
            (
                "If either source adds stronger bot protection later, a "
                "browser-backed fallback may be needed."
            ),
        ],
    )

    write_run_outputs(output_dir, payload, selected_games_payload)
    write_json(output_dir / "provenance.json", asdict(provenance))
    write_summary(output_dir / "summary.md", payload)

    print(f"Wrote scrape artifacts to {output_dir}")
    print(f"Games: {', '.join(games)}")
    print(f"Active-only mode: {args.active_only}")
    print(f"Output timezone: {args.timezone}")
    print(f"Include mode: {include}")
    print(f"Codes: {payload['counts']['codes']}")
    print(f"Events: {payload['counts']['events']}")
    print(f"Banners: {payload['counts']['banners']}")
    print(f"Challenges: {payload['counts']['challenges']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
