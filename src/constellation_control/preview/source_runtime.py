from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin

from constellation_control.adapters.bkg_rinex_nav import (
    CachedRinexNav,
    _fetch_bkg_only,
    _fetch_whu_only,
    _parse_directory_listing,
    _validate_complete_rinex_system,
)
from constellation_control.adapters.fcnd_api import FcndApiClient
from constellation_control.adapters.reviewed_http_fetch import fetch_reviewed_url
from constellation_control.preview.source_settings import load_source_settings, source_setting


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


def _safe_name(value: str) -> str | None:
    name = value.strip().rstrip("/").split("/")[-1]
    if not name or name in {".", ".."} or Path(name).name != name:
        return None
    return name


def _looks_like_rinex_name(name: str) -> bool:
    lower = name.lower()
    return lower.endswith(_RINEX_FILE_SUFFIXES)


def _decode_rinex_payload(payload: bytes, file_name: str) -> bytes:
    if file_name.lower().endswith(".gz") or payload[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(payload)
        except OSError as exc:
            raise ValueError(f"{file_name}: invalid gzip RINEX payload") from exc
    return payload


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
    directory = cache_root.resolve() / provider_key / "brdc" / f"{source_date.year:04d}" / f"{doy:03d}"
    directory.mkdir(parents=True, exist_ok=True)
    normalized_name = source_file_name
    if not normalized_name.lower().endswith(".gz"):
        normalized_name = normalized_name + ".gz"
    gzip_path = directory / normalized_name
    rinex_name = normalized_name.removesuffix(".gz")
    rinex_path = directory / rinex_name
    manifest_path = directory / (normalized_name + ".manifest.json")

    cached_gzip = source_payload if source_file_name.lower().endswith(".gz") else gzip.compress(rinex)
    source_sha256 = hashlib.sha256(source_payload).hexdigest()
    rinex_sha256 = hashlib.sha256(rinex).hexdigest()
    if gzip_path.exists() and hashlib.sha256(gzip_path.read_bytes()).hexdigest() != hashlib.sha256(cached_gzip).hexdigest():
        raise ValueError(f"immutable {provider_key} RINEX gzip cache collision")
    if rinex_path.exists() and hashlib.sha256(rinex_path.read_bytes()).hexdigest() != rinex_sha256:
        raise ValueError(f"immutable {provider_key} RINEX cache collision")
    if not gzip_path.exists():
        gzip_path.write_bytes(cached_gzip)
    if not rinex_path.exists():
        rinex_path.write_bytes(rinex)

    manifest = {
        "schema": "oc-gnss-rinex-cache-v1",
        "provider": provider,
        "constellation": system,
        "format": "RINEX NAV",
        "source_url": source_url,
        "source_date": source_date.isoformat(),
        "source_filename": source_file_name,
        "source_sha256": source_sha256,
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
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

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


def _fetch_fcnd_rinex(day: date, system: str, cache_root: Path, timeout_s: float, base_url: str) -> CachedRinexNav:
    client = FcndApiClient(base_url=base_url, timeout_s=timeout_s)
    catalogue = client.list_data(
        time_begin=f"{day.strftime('%d-%m-%Y')} 00:00:00",
        time_end=f"{day.strftime('%d-%m-%Y')} 23:59:59",
        data_type="gnss",
        limit=2000,
    )
    candidates: list[tuple[str, str, dict[str, Any]]] = []
    for record in _walk_json_dicts(catalogue):
        raw_name = next((record.get(key) for key in ("file_name", "filename", "name") if isinstance(record.get(key), str)), None)
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
            query = urlencode(
                [("datafile[time_begin]", time_begin), ("datafile[file_name]", name)]
            )
            source_url = base_url.rstrip("/") + "/api/getData/?" + query
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
    raise ValueError("FCND candidates did not yield a valid broadcast RINEX NAV file: " + "; ".join(errors[:8]))


def _rank_archive_name(name: str, day: date) -> tuple[int, int, str]:
    lower = name.lower()
    doy = day.timetuple().tm_yday
    tokens = (day.strftime("%Y%m%d"), f"{day.year:04d}{doy:03d}", day.strftime("%y%j"))
    return (
        1 if any(token.lower() in lower for token in tokens) else 0,
        1 if "brdc" in lower or "nav" in lower else 0,
        name,
    )


def _fetch_iac_ftp_rinex(day: date, system: str, cache_root: Path, timeout_s: float, base_url: str) -> CachedRinexNav:
    queue: list[tuple[str, int]] = [(urljoin(base_url.rstrip("/") + "/", "MCC/"), 0), (urljoin(base_url.rstrip("/") + "/", "IGS/"), 0)]
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
    file_urls.sort(key=lambda item: _rank_archive_name(item[0], day), reverse=True)
    errors: list[str] = []
    for name, url in file_urls[:32]:
        try:
            response = fetch_reviewed_url(url, timeout_s=timeout_s)
            rinex = _decode_rinex_payload(response.raw, name)
            _validate_complete_rinex_system(rinex, system)
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


def _effective_ids(system: str) -> tuple[list[str], str]:
    document = load_source_settings()
    policy = document.selection.get(system)  # type: ignore[arg-type]
    if policy is None:
        ids: list[str] = []
        mode = "auto"
    elif policy.mode == "manual":
        ids = [policy.selected_source_id] if policy.selected_source_id else []
        mode = "manual"
    else:
        ids = list(policy.auto_order)
        mode = "auto"
    if mode == "auto" and system in {"GLONASS", "GPS"} and "fcnd_api" not in ids:
        insert_at = ids.index("iac_ftp_archive") + 1 if "iac_ftp_archive" in ids else 0
        ids.insert(insert_at, "fcnd_api")
    return [item for item in ids if item], mode


def fetch_selected_broadcast_rinex(
    day: date,
    system: str,
    cache_root: Path,
    *,
    timeout_s: float = 30.0,
) -> SelectedRinexNav:
    if timeout_s <= 0.0:
        raise ValueError("timeout_s must be positive")
    ids, mode = _effective_ids(system)
    if not ids:
        raise ValueError(f"no source is configured for {system}")
    attempts: list[SourceAttempt] = []

    for source_id in ids:
        try:
            if source_id == "iac_glonass":
                attempts.append(SourceAttempt(source_id, "skip", "ephemeris-table authority; not a RINEX NAV source"))
                continue
            if source_id == "iac_ftp_archive":
                setting = source_setting(source_id)
                if setting is None or not setting.enabled:
                    attempts.append(SourceAttempt(source_id, "skip", "disabled or missing from settings"))
                    continue
                cached = _fetch_iac_ftp_rinex(day, system, cache_root, timeout_s, setting.base_url)
            elif source_id == "fcnd_api":
                setting = source_setting(source_id)
                base_url = setting.base_url if setting is not None and setting.enabled else "https://fcnd.ru"
                cached = _fetch_fcnd_rinex(day, system, cache_root, timeout_s, base_url)
            elif source_id == "igs_bkg":
                setting = source_setting(source_id)
                if setting is not None and not setting.enabled:
                    attempts.append(SourceAttempt(source_id, "skip", "disabled in settings"))
                    continue
                cached = _fetch_bkg_only(day, system, cache_root, timeout_s)
            elif source_id == "igs_whu":
                setting = source_setting(source_id)
                if setting is not None and not setting.enabled:
                    attempts.append(SourceAttempt(source_id, "skip", "disabled in settings"))
                    continue
                cached = _fetch_whu_only(day, system, cache_root, timeout_s)
            else:
                attempts.append(SourceAttempt(source_id, "skip", "source has no broadcast-RINEX runtime adapter"))
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
