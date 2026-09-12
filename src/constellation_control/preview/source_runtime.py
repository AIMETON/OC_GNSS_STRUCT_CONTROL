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


_SYSTEM_SUFFIX = {
    "GLONASS": "RN",
    "GPS": "GN",
    "Galileo": "EN",
    "BeiDou": "CN",
}
_RINEX3_FILE_RE = re.compile(r"(?i)\.rnx(?:\.gz)?$")
_RINEX2_NAV_RE = re.compile(r"(?i)^brdc(?P<doy>\d{3})0\.(?P<yy>\d{2})(?P<kind>[gfnl])(?:\.z)?$")


def _safe_name(value: str) -> str | None:
    name = value.strip().rstrip("/").split("/")[-1]
    if not name or name in {".", ".."} or Path(name).name != name:
        return None
    return name


def _looks_like_rinex_name(name: str) -> bool:
    lower = name.lower()
    return bool(_RINEX3_FILE_RE.search(name) or _RINEX2_NAV_RE.match(name) or lower.endswith((".nav", ".nav.gz")))


def _decode_rinex_payload(payload: bytes, file_name: str) -> bytes:
    lower = file_name.lower()
    if lower.endswith(".z") and not lower.endswith(".gz"):
        raise ValueError(
            f"{file_name}: Unix-compress .Z payload is not accepted by the portable runtime; "
            "use an uncompressed or .gz source candidate"
        )
    if lower.endswith(".gz") or payload[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(payload)
        except OSError as exc:
            raise ValueError(f"{file_name}: invalid gzip RINEX payload") from exc
    return payload


def _rinex_version_and_first_line(raw: bytes) -> tuple[float, str]:
    first = raw.decode("ascii", errors="replace").splitlines()[0] if raw else ""
    try:
        version = float(first[:9].strip())
    except ValueError:
        version = 3.0
    return version, first


def _validate_runtime_rinex_system(raw: bytes, system: str, *, source_name: str) -> None:
    version, first = _rinex_version_and_first_line(raw)
    if version >= 3.0 or system == "GPS":
        _validate_complete_rinex_system(raw, system)
        return
    if system != "GLONASS":
        raise ValueError(f"RINEX {version:g} cannot establish {system} coverage")

    head = raw[:32768].decode("ascii", errors="replace")
    if "RINEX VERSION / TYPE" not in head or "END OF HEADER" not in head:
        raise ValueError("RINEX navigation header is incomplete")
    first_upper = first.upper()
    name_match = _RINEX2_NAV_RE.match(source_name)
    glonass_file_type = bool(name_match and name_match.group("kind").lower() == "g")
    if "NAV" not in first_upper:
        raise ValueError("downloaded RINEX file is not navigation data")
    if "GLONASS" not in first_upper and not glonass_file_type:
        raise ValueError("RINEX 2 navigation payload cannot establish GLONASS coverage")


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
    directory = cache_root.resolve() / provider_key / "brdc" / f"{source_date.year:04d}" / f"{doy:03d}"
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
        "schema": "oc-gnss-external-rinex-cache-v2",
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
    _validate_runtime_rinex_system(rinex, system, source_name=name)
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


def _whu_day_root(setting: SourceEndpointSetting, day: date) -> str:
    doy = day.timetuple().tm_yday
    return f"{setting.base_url.rstrip('/')}/{day.year:04d}/{doy:03d}/"


def _fetch_configured_whu(
    day: date,
    system: str,
    cache_root: Path,
    timeout_s: float,
    setting: SourceEndpointSetting,
) -> CachedRinexNav:
    day_root = _whu_day_root(setting, day)
    root_response = fetch_reviewed_url(day_root, timeout_s=timeout_s)
    subdirs = {_safe_name(name) for name in _parse_directory_listing(root_response.raw)}
    yy = day.year % 100
    preferred = [f"{yy:02d}m"]
    if system == "GLONASS":
        preferred.append(f"{yy:02d}g")
    elif system == "GPS":
        preferred.append(f"{yy:02d}n")
    available = [name for name in preferred if name in subdirs]
    if not available:
        raise ValueError(
            f"WHU daily root has no compatible navigation directory; "
            f"available={', '.join(sorted(name for name in subdirs if name))}"
        )

    errors: list[str] = []
    for subdir in available:
        directory_url = urljoin(day_root, subdir + "/")
        listing = fetch_reviewed_url(directory_url, timeout_s=timeout_s)
        names = [name for raw in _parse_directory_listing(listing.raw) if (name := _safe_name(raw))]
        if subdir.endswith("m"):
            candidates = sorted(
                (name for name in names if name.lower().endswith(".rnx.gz") and "_01d_" in name.lower()),
                key=lambda name: ("_mm.rnx.gz" not in name.lower(), name),
            )
        else:
            candidates = sorted(name for name in names if _looks_like_rinex_name(name))
        for name in candidates[:24]:
            try:
                url = urljoin(directory_url, name)
                response = fetch_reviewed_url(url, timeout_s=timeout_s)
                rinex = _decode_rinex_payload(response.raw, name)
                _validate_runtime_rinex_system(rinex, system, source_name=name)
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
            except (OSError, ValueError) as exc:
                errors.append(f"{subdir}/{name}: {exc}")
    raise ValueError("WHU candidates did not yield valid RINEX NAV: " + "; ".join(errors[:8]))


def _walk_json_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json_dicts(child)


def _fcnd_record_name(record: dict[str, Any]) -> str | None:
    for key in ("pk_file_name", "file_name", "filename", "name"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return _safe_name(value)
    return None


def _fcnd_record_time(record: dict[str, Any], day: date) -> str:
    for key in ("pt_time_begin", "time_begin", "datetime", "date_time", "time", "date"):
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
        1 if "nav" in lower or "navigation" in text or "навигац" in text else 0,
        1 if any(token.lower() in lower or token.lower() in text for token in date_tokens) else 0,
        1 if _looks_like_rinex_name(name) else 0,
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
        limit=500,
    )
    candidates: list[tuple[str, str, dict[str, Any]]] = []
    for record in _walk_json_dicts(catalogue):
        name = _fcnd_record_name(record)
        if name is None or not _looks_like_rinex_name(name):
            continue
        text = json.dumps(record, ensure_ascii=False).lower()
        if name.lower().endswith(("o", "o.gz", ".obs", ".obs.gz")) or "observation" in text or "измерен" in text:
            continue
        candidates.append((name, _fcnd_record_time(record, day), record))
    if not candidates:
        raise ValueError(
            "FCND catalogue is reachable, but no broadcast-navigation RINEX candidate was identified "
            "for the requested day"
        )
    candidates.sort(key=lambda item: _fcnd_candidate_score(item[0], item[2], day), reverse=True)

    errors: list[str] = []
    for name, time_begin, _record in candidates[:24]:
        try:
            payload = client.download_datafile(time_begin=time_begin, file_name=name)
            rinex = _decode_rinex_payload(payload, name)
            _validate_runtime_rinex_system(rinex, system, source_name=name)
            _validate_requested_day(rinex, day, name)
            query = urlencode([("datafile[time_begin]", time_begin), ("datafile[file_name]", name)])
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
    raise ValueError("FCND candidates did not yield valid broadcast RINEX NAV: " + "; ".join(errors[:8]))


def _iac_expected_rinex2_name(day: date, system: str) -> str:
    kind = {"GLONASS": "g", "GPS": "n"}.get(system)
    if kind is None:
        raise ValueError(f"IAC MCC BRDC RINEX 2 source does not support {system}")
    return f"Brdc{day.timetuple().tm_yday:03d}0.{day.year % 100:02d}{kind}"


def _fetch_iac_ftp_rinex(
    day: date,
    system: str,
    cache_root: Path,
    timeout_s: float,
    setting: SourceEndpointSetting,
) -> CachedRinexNav:
    expected = _iac_expected_rinex2_name(day, system)
    directory_url = f"{setting.base_url.rstrip('/')}/MCC/BRDC/{day.year:04d}/"
    listing = fetch_reviewed_url(directory_url, timeout_s=timeout_s)
    names = [name for raw in _parse_directory_listing(listing.raw) if (name := _safe_name(raw))]
    by_lower = {name.lower(): name for name in names}
    candidates: list[str] = []
    for variant in (expected, expected + ".gz", expected + ".Z"):
        actual = by_lower.get(variant.lower())
        if actual is not None:
            candidates.append(actual)
    if not candidates:
        matching_day = [
            name
            for name in names
            if (match := _RINEX2_NAV_RE.match(name))
            and int(match.group("doy")) == day.timetuple().tm_yday
            and int(match.group("yy")) == day.year % 100
        ]
        candidates.extend(matching_day)
    if not candidates:
        raise ValueError(
            f"IAC MCC/BRDC/{day.year} has no {system} broadcast RINEX for DOY "
            f"{day.timetuple().tm_yday:03d}; expected {expected}"
        )

    errors: list[str] = []
    for name in candidates:
        try:
            url = urljoin(directory_url, name)
            response = fetch_reviewed_url(url, timeout_s=timeout_s)
            rinex = _decode_rinex_payload(response.raw, name)
            _validate_runtime_rinex_system(rinex, system, source_name=name)
            _validate_requested_day(rinex, day, name)
            return _store_external_rinex(
                provider_key="iac-ftp",
                provider="IAC GLONASS MCC broadcast archive",
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
    raise ValueError("IAC MCC BRDC candidates failed validation: " + "; ".join(errors[:8]))


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
                attempts.append(SourceAttempt(source_id, "skip", "source has no broadcast-RINEX runtime adapter"))
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
