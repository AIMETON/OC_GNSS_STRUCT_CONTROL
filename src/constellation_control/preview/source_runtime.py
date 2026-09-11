from __future__ import annotations

import gzip
import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode, urljoin

from constellation_control.adapters.bkg_rinex_nav import (
    CachedRinexNav,
    _parse_directory_listing,
    _select_whu_navigation_file,
    _validate_complete_rinex_system,
)
from constellation_control.adapters.fcnd_api import FcndApiClient
from constellation_control.adapters.reviewed_http_fetch import fetch_reviewed_url
from constellation_control.preview.source_settings import (
    GNSSSystem,
    SourceEndpointSetting,
    load_source_settings,
    selected_source_sequence,
)


@dataclass(frozen=True)
class SourceAttempt:
    source_id: str
    status: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"source_id": self.source_id, "status": self.status, "detail": self.detail}


@dataclass(frozen=True)
class SelectedRinexNav:
    cached: CachedRinexNav
    source_id: str
    attempts: tuple[SourceAttempt, ...]


_RINEX_FILE_SUFFIXES = (
    ".rnx",
    ".rnx.gz",
    ".nav",
    ".nav.gz",
    ".n",
    ".n.gz",
    ".g",
    ".g.gz",
)
_SYSTEM_SUFFIX = {
    "GLONASS": "RN",
    "GPS": "GN",
    "Galileo": "EN",
    "BeiDou": "CN",
}


def _safe_name(value: str) -> str | None:
    name = value.strip().rstrip("/").split("/")[-1]
    if not name or name in {".", ".."} or Path(name).name != name:
        return None
    return name


def _looks_like_rinex_name(name: str) -> bool:
    return name.lower().endswith(_RINEX_FILE_SUFFIXES)


def _decode_rinex_payload(payload: bytes, file_name: str) -> bytes:
    if file_name.lower().endswith(".gz") or payload[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(payload)
        except OSError as exc:
            raise ValueError(f"{file_name}: invalid gzip RINEX payload") from exc
    return payload


def _rinex_mentions_day(rinex: bytes, day: date) -> bool:
    text = rinex.decode("ascii", errors="replace")
    patterns = (
        rf"\b{day.year:04d}\s+0?{day.month}\s+0?{day.day}\b",
        rf"\b{day.year % 100:02d}\s+0?{day.month}\s+0?{day.day}\b",
    )
    return any(re.search(pattern, text) is not None for pattern in patterns)


def _validate_requested_day(rinex: bytes, day: date, source_name: str) -> None:
    if not _rinex_mentions_day(rinex, day):
        raise ValueError(f"{source_name}: RINEX payload has no navigation epoch for {day.isoformat()}")


def _store_external_rinex(
    *,
    provider_key: str,
    provider: str,
    source_url: str,
    source_file_name: str,
    source_payload: bytes,
    rinex: bytes,
    source_date: date,
    system: str,
    transport: str,
    cache_root: Path,
) -> CachedRinexNav:
    doy = source_date.timetuple().tm_yday
    directory = (
        cache_root.resolve()
        / provider_key
        / "brdc"
        / f"{source_date.year:04d}"
        / f"{doy:03d}"
    )
    directory.mkdir(parents=True, exist_ok=True)
    normalized_name = source_file_name
    if not normalized_name.lower().endswith(".gz"):
        normalized_name += ".gz"
    gzip_path = directory / normalized_name
    rinex_path = directory / normalized_name.removesuffix(".gz")
    manifest_path = directory / (normalized_name + ".manifest.json")

    cached_gzip = source_payload if source_file_name.lower().endswith(".gz") else gzip.compress(rinex)
    source_sha256 = hashlib.sha256(source_payload).hexdigest()
    cached_gzip_sha256 = hashlib.sha256(cached_gzip).hexdigest()
    rinex_sha256 = hashlib.sha256(rinex).hexdigest()
    if gzip_path.exists() and hashlib.sha256(gzip_path.read_bytes()).hexdigest() != cached_gzip_sha256:
        raise ValueError(f"immutable {provider_key} RINEX gzip cache collision")
    if rinex_path.exists() and hashlib.sha256(rinex_path.read_bytes()).hexdigest() != rinex_sha256:
        raise ValueError(f"immutable {provider_key} RINEX cache collision")
    if not gzip_path.exists():
        gzip_path.write_bytes(cached_gzip)
    if not rinex_path.exists():
        rinex_path.write_bytes(rinex)

    manifest = {
        "schema": "oc-gnss-external-rinex-cache-v1",
        "provider": provider,
        "constellation": system,
        "format": "RINEX NAV",
        "source_url": source_url,
        "source_date": source_date.isoformat(),
        "source_filename": source_file_name,
        "source_sha256": source_sha256,
        "cached_gzip_sha256": cached_gzip_sha256,
        "rinex_sha256": rinex_sha256,
        "gzip_path": str(gzip_path),
        "rinex_path": str(rinex_path),
        "transport": transport,
    }
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing.get("source_sha256") != source_sha256 or existing.get("rinex_sha256") != rinex_sha256:
            raise ValueError(f"immutable {provider_key} RINEX manifest collision")
    else:
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    return CachedRinexNav(
        source_url=source_url,
        source_date=source_date,
        source_filename=source_file_name,
        source_sha256=source_sha256,
        rinex_sha256=rinex_sha256,
        gzip_path=gzip_path,
        rinex_path=rinex_path,
        manifest_path=manifest_path,
        transport=transport,
    )


def _template_values(day: date, system: str) -> dict[str, str]:
    doy = day.timetuple().tm_yday
    return {
        "year": f"{day.year:04d}",
        "yy": f"{day.year % 100:02d}",
        "doy": f"{doy:03d}",
        "system": system,
        "system_suffix": _SYSTEM_SUFFIX[system],
    }


def _render_source_template(setting: SourceEndpointSetting, day: date, system: str) -> str:
    values = {"base_url": setting.base_url, **_template_values(day, system)}
    try:
        return (setting.request_template.strip() or "{base_url}").format(**values)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"invalid request_template for {setting.source_id}: {exc}") from exc


def _fetch_configured_bkg(
    day: date,
    system: str,
    cache_root: Path,
    timeout_s: float,
    setting: SourceEndpointSetting,
) -> CachedRinexNav:
    url = _render_source_template(setting, day, system)
    name = _safe_name(url)
    if name is None or not name.lower().endswith(".gz"):
        raise ValueError("IGS BKG request template must resolve to a gzip RINEX file")
    response = fetch_reviewed_url(url, timeout_s=timeout_s)
    rinex = _decode_rinex_payload(response.raw, name)
    _validate_complete_rinex_system(rinex, system)
    _validate_requested_day(rinex, day, name)
    return _store_external_rinex(
        provider_key="igs-bkg",
        provider="BKG / IGS GNSS Data Center",
        source_url=url,
        source_file_name=name,
        source_payload=response.raw,
        rinex=rinex,
        source_date=day,
        system=system,
        transport=response.transport,
        cache_root=cache_root,
    )


def _fetch_configured_whu(
    day: date,
    system: str,
    cache_root: Path,
    timeout_s: float,
    setting: SourceEndpointSetting,
) -> CachedRinexNav:
    directory_url = _render_source_template(setting, day, system).rstrip("/") + "/"
    listing = fetch_reviewed_url(directory_url, timeout_s=timeout_s)
    names = _parse_directory_listing(listing.raw)
    name = _select_whu_navigation_file(names, day, system)
    url = urljoin(directory_url, name)
    response = fetch_reviewed_url(url, timeout_s=timeout_s)
    rinex = _decode_rinex_payload(response.raw, name)
    _validate_complete_rinex_system(rinex, system)
    _validate_requested_day(rinex, day, name)
    return _store_external_rinex(
        provider_key="igs-whu",
        provider="Wuhan University IGS Data Center",
        source_url=url,
        source_file_name=name,
        source_payload=response.raw,
        rinex=rinex,
        source_date=day,
        system=system,
        transport=response.transport,
        cache_root=cache_root,
    )


def _walk_json_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json_dicts(child)


def _fcnd_record_time(record: dict[str, Any], day: date) -> str:
    for key in ("time_begin", "datetime", "date_time", "time", "date"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"{day.isoformat()} 00:00:00"


def _fcnd_candidate_score(name: str, record: dict[str, Any], day: date) -> tuple[int, int, int, str]:
    lower = name.lower()
    text = json.dumps(record, ensure_ascii=False).lower()
    doy = day.timetuple().tm_yday
    date_tokens = (day.strftime("%Y%m%d"), f"{day.year:04d}{doy:03d}", day.strftime("%y%j"))
    return (
        1 if "nav" in lower or "navigation" in text else 0,
        1 if any(token.lower() in lower or token.lower() in text for token in date_tokens) else 0,
        1 if lower.endswith((".rnx.gz", ".rnx")) else 0,
        name,
    )


def _fetch_fcnd_rinex(
    day: date,
    system: str,
    cache_root: Path,
    timeout_s: float,
    setting: SourceEndpointSetting,
) -> CachedRinexNav:
    client = FcndApiClient(base_url=setting.base_url, timeout_s=timeout_s)
    catalogue = client.list_data(
        time_begin=f"{day.strftime('%d-%m-%Y')} 00:00:00",
        time_end=f"{day.strftime('%d-%m-%Y')} 23:59:59",
        data_type="gnss",
        limit=2000,
    )
    candidates: list[tuple[str, str, dict[str, Any]]] = []
    for record in _walk_json_dicts(catalogue):
        raw_name = next(
            (
                record.get(key)
                for key in ("file_name", "filename", "name")
                if isinstance(record.get(key), str)
            ),
            None,
        )
        if not isinstance(raw_name, str):
            continue
        name = _safe_name(raw_name)
        if name is None or not _looks_like_rinex_name(name):
            continue
        candidates.append((name, _fcnd_record_time(record, day), record))
    if not candidates:
        raise ValueError("FCND getData returned no RINEX-like GNSS files for requested day")
    candidates.sort(key=lambda item: _fcnd_candidate_score(item[0], item[2], day), reverse=True)

    errors: list[str] = []
    for name, time_begin, _record in candidates[:32]:
        try:
            payload = client.download_datafile(time_begin=time_begin, file_name=name)
            rinex = _decode_rinex_payload(payload, name)
            _validate_complete_rinex_system(rinex, system)
            _validate_requested_day(rinex, day, name)
            query = urlencode(
                [("datafile[time_begin]", time_begin), ("datafile[file_name]", name)]
            )
            source_url = setting.base_url.rstrip("/") + "/api/getData/?" + query
            return _store_external_rinex(
                provider_key="fcnd",
                provider="Russian Federal Coordinate Network Data Centre (FCND)",
                source_url=source_url,
                source_file_name=name,
                source_payload=payload,
                rinex=rinex,
                source_date=day,
                system=system,
                transport="fcnd-api",
                cache_root=cache_root,
            )
        except (OSError, ValueError) as exc:
            errors.append(f"{name}: {exc}")
    raise ValueError(
        "FCND candidates did not yield a valid broadcast RINEX NAV file: " + "; ".join(errors[:8])
    )


def _rank_archive_url(name: str, url: str, day: date) -> tuple[int, int, str]:
    lower = (name + " " + url).lower()
    doy = day.timetuple().tm_yday
    tokens = (day.strftime("%Y%m%d"), f"{day.year:04d}{doy:03d}", day.strftime("%y%j"))
    return (
        1 if any(token.lower() in lower for token in tokens) else 0,
        1 if "brdc" in lower or "nav" in lower else 0,
        name,
    )


def _fetch_iac_ftp_rinex(
    day: date,
    system: str,
    cache_root: Path,
    timeout_s: float,
    setting: SourceEndpointSetting,
) -> CachedRinexNav:
    base_url = setting.base_url.rstrip("/") + "/"
    queue: list[tuple[str, int]] = [
        (urljoin(base_url, "MCC/"), 0),
        (urljoin(base_url, "IGS/"), 0),
    ]
    visited: set[str] = set()
    file_urls: list[tuple[str, str]] = []
    while queue and len(visited) < 64:
        directory_url, depth = queue.pop(0)
        if directory_url in visited:
            continue
        visited.add(directory_url)
        response = fetch_reviewed_url(directory_url, timeout_s=timeout_s)
        for raw_name in _parse_directory_listing(response.raw):
            name = _safe_name(raw_name)
            if name is None:
                continue
            child = urljoin(directory_url, name)
            if _looks_like_rinex_name(name):
                file_urls.append((name, child))
            elif depth < 3 and "." not in name and len(name) <= 80:
                queue.append((child.rstrip("/") + "/", depth + 1))
    if not file_urls:
        raise ValueError("IAC FTP MCC/IGS discovery found no RINEX-like files")
    file_urls.sort(key=lambda item: _rank_archive_url(item[0], item[1], day), reverse=True)
    errors: list[str] = []
    for name, url in file_urls[:32]:
        try:
            response = fetch_reviewed_url(url, timeout_s=timeout_s)
            rinex = _decode_rinex_payload(response.raw, name)
            _validate_complete_rinex_system(rinex, system)
            _validate_requested_day(rinex, day, name)
            return _store_external_rinex(
                provider_key="iac-ftp",
                provider="IAC GLONASS FTP archive",
                source_url=url,
                source_file_name=name,
                source_payload=response.raw,
                rinex=rinex,
                source_date=day,
                system=system,
                transport=response.transport,
                cache_root=cache_root,
            )
        except (OSError, ValueError) as exc:
            errors.append(f"{name}: {exc}")
    raise ValueError("IAC FTP candidates did not yield valid RINEX NAV: " + "; ".join(errors[:8]))


def fetch_selected_broadcast_rinex(
    day: date,
    system: str,
    cache_root: Path,
    *,
    timeout_s: float = 30.0,
) -> SelectedRinexNav:
    if timeout_s <= 0.0:
        raise ValueError("timeout_s must be positive")
    if system not in _SYSTEM_SUFFIX:
        raise ValueError(f"unsupported GNSS system: {system}")
    typed_system = cast(GNSSSystem, system)
    sources = selected_source_sequence(typed_system, capability="broadcast_rinex_nav")
    policy = load_source_settings().selection.get(typed_system)
    mode = policy.mode if policy is not None else "auto"
    if not sources:
        raise ValueError(f"no broadcast-RINEX source is configured for {system}")
    attempts: list[SourceAttempt] = []

    for setting in sources:
        source_id = setting.source_id
        try:
            if source_id == "iac_ftp_archive":
                cached = _fetch_iac_ftp_rinex(day, system, cache_root, timeout_s, setting)
            elif source_id == "fcnd_api":
                cached = _fetch_fcnd_rinex(day, system, cache_root, timeout_s, setting)
            elif source_id == "igs_bkg":
                cached = _fetch_configured_bkg(day, system, cache_root, timeout_s, setting)
            elif source_id == "igs_whu":
                cached = _fetch_configured_whu(day, system, cache_root, timeout_s, setting)
            else:
                attempts.append(
                    SourceAttempt(source_id, "skip", "source has no broadcast-RINEX runtime adapter")
                )
                if mode == "manual":
                    break
                continue
        except (OSError, ValueError) as exc:
            attempts.append(SourceAttempt(source_id, "failed", str(exc)))
            if mode == "manual":
                break
            continue
        attempts.append(SourceAttempt(source_id, "success", cached.source_url))
        return SelectedRinexNav(cached=cached, source_id=source_id, attempts=tuple(attempts))

    rendered = "; ".join(f"{item.source_id}={item.status}: {item.detail}" for item in attempts)
    raise OSError(f"all configured {system} RINEX sources failed; {rendered}")
