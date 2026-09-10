from __future__ import annotations

import gzip
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urljoin

from constellation_control.adapters.reviewed_http_fetch import fetch_reviewed_url

BKG_IGS_BRDC_ROOT = "https://igs.bkg.bund.de/root_ftp/IGS/BRDC"
WHU_IGS_DAILY_ROOT = "ftp://igs.gnsswhu.cn/pub/gps/data/daily"
_MAX_RINEX_GZIP_BYTES = 64 * 1024 * 1024
_SYSTEM_SUFFIX = {
    "GLONASS": "RN",
    "GPS": "GN",
    "Galileo": "EN",
    "BeiDou": "CN",
}
_SYSTEM_RECORD_PREFIX = {
    "GLONASS": "R",
    "GPS": "G",
    "Galileo": "E",
    "BeiDou": "C",
}


@dataclass(frozen=True)
class IgsSourceCandidate:
    key: str
    provider: str
    region: str
    role: str
    status: str
    endpoint: str


IGS_SOURCE_CANDIDATES: tuple[IgsSourceCandidate, ...] = (
    IgsSourceCandidate(
        "bkg",
        "BKG / IGS GNSS Data Center",
        "DE",
        "global_broadcast_nav",
        "active_auto",
        BKG_IGS_BRDC_ROOT,
    ),
    IgsSourceCandidate(
        "whu",
        "Wuhan University IGS Data Center",
        "CN",
        "global_broadcast_nav",
        "active_auto",
        WHU_IGS_DAILY_ROOT,
    ),
    IgsSourceCandidate(
        "ga",
        "Geoscience Australia GNSS Data Centre",
        "AU",
        "station_rinex_nav",
        "candidate",
        "https://data.gnss.ga.gov.au/api/rinexFiles",
    ),
    IgsSourceCandidate(
        "hartrao",
        "HartRAO IGS Regional Data Center",
        "ZA",
        "igs_broadcast_ephemeris",
        "candidate",
        "https://geodesy.hartrao.ac.za/",
    ),
    IgsSourceCandidate(
        "ibge",
        "IBGE RBMC",
        "BR",
        "station_rinex_observation",
        "candidate_observation_only",
        "https://geoftp.ibge.gov.br/informacoes_sobre_posicionamento_geodesico/rbmc/",
    ),
    IgsSourceCandidate(
        "kasi",
        "KASI IGS Data Center",
        "KR",
        "igs_data_center",
        "candidate_field_unreachable",
        "ftp://nfs.kasi.re.kr/",
    ),
    IgsSourceCandidate(
        "cas",
        "CAS / BDsmart",
        "CN",
        "igs_data_center",
        "candidate_field_unreachable",
        "https://data.bdsmart.cn/pub/",
    ),
)


@dataclass(frozen=True)
class CachedRinexNav:
    source_url: str
    source_date: date
    source_filename: str
    source_sha256: str
    rinex_sha256: str
    gzip_path: Path
    rinex_path: Path
    manifest_path: Path
    transport: str


def bkg_gnss_daily_url(day: date, system: str) -> str:
    try:
        suffix = _SYSTEM_SUFFIX[system]
    except KeyError as exc:
        raise ValueError(f"unsupported GNSS system: {system}") from exc
    doy = day.timetuple().tm_yday
    filename = f"BRDC00WRD_R_{day.year:04d}{doy:03d}0000_01D_{suffix}.rnx.gz"
    return f"{BKG_IGS_BRDC_ROOT}/{day.year:04d}/{doy:03d}/{filename}"


def bkg_glonass_daily_url(day: date) -> str:
    return bkg_gnss_daily_url(day, "GLONASS")


def whu_gnss_daily_directory(day: date) -> str:
    doy = day.timetuple().tm_yday
    yy = day.year % 100
    return f"{WHU_IGS_DAILY_ROOT}/{day.year:04d}/{doy:03d}/{yy:02d}p/"


def _validate_rinex_nav(raw: bytes, system: str | None = None) -> None:
    head = raw[:32768].decode("ascii", errors="replace")
    if "RINEX VERSION / TYPE" not in head:
        raise ValueError("downloaded payload is not a RINEX file")
    lines = head.splitlines()
    first = lines[0] if lines else ""
    if "NAVIGATION DATA" not in first.upper() and "NAV" not in first.upper():
        raise ValueError("downloaded RINEX file is not navigation data")
    if "END OF HEADER" not in head:
        raise ValueError("RINEX navigation header is incomplete")
    if system is None:
        return
    try:
        version = float(first[:9].strip())
    except ValueError:
        version = 3.0
    if version < 3.0:
        if system != "GPS":
            raise ValueError(f"RINEX {version:g} cannot establish {system} coverage")
        return
    prefix = _SYSTEM_RECORD_PREFIX[system]
    end_header = next((i for i, line in enumerate(lines) if "END OF HEADER" in line), -1)
    records = lines[end_header + 1 :]
    if records and not any(line.startswith(prefix) for line in records if line):
        # A 32 KiB header probe may end before the requested system appears. Full validation
        # is performed after decompression by callers that have the complete payload.
        return


def _validate_complete_rinex_system(raw: bytes, system: str) -> None:
    text = raw.decode("ascii", errors="replace")
    lines = text.splitlines()
    _validate_rinex_nav(raw, system)
    first = lines[0] if lines else ""
    try:
        version = float(first[:9].strip())
    except ValueError:
        version = 3.0
    if version < 3.0:
        if system != "GPS":
            raise ValueError(f"RINEX {version:g} cannot establish {system} coverage")
        return
    end_header = next((i for i, line in enumerate(lines) if "END OF HEADER" in line), -1)
    prefix = _SYSTEM_RECORD_PREFIX[system]
    if not any(line.startswith(prefix) for line in lines[end_header + 1 :] if line):
        raise ValueError(f"RINEX navigation payload contains no {system} records")


def _cached_rinex_if_valid(
    *,
    day: date,
    system: str,
    filename: str,
    gzip_path: Path,
    rinex_path: Path,
    manifest_path: Path,
) -> CachedRinexNav | None:
    if not (gzip_path.is_file() and rinex_path.is_file() and manifest_path.is_file()):
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid RINEX cache manifest: {manifest_path}: {exc}") from exc
    if manifest.get("source_date") != day.isoformat():
        raise ValueError("RINEX cache manifest date does not match requested date")
    if manifest.get("constellation") != system:
        raise ValueError("RINEX cache manifest constellation does not match requested system")
    if manifest.get("source_filename") != filename:
        raise ValueError("RINEX cache manifest filename does not match requested source")

    compressed = gzip_path.read_bytes()
    rinex = rinex_path.read_bytes()
    if len(compressed) > _MAX_RINEX_GZIP_BYTES:
        raise ValueError("cached IGS RINEX payload exceeds safety limit")
    try:
        decompressed = gzip.decompress(compressed)
    except OSError as exc:
        raise ValueError("cached IGS source is not valid gzip data") from exc
    if decompressed != rinex:
        raise ValueError("cached RINEX gzip and decompressed payload differ")
    _validate_complete_rinex_system(rinex, system)

    source_sha = hashlib.sha256(compressed).hexdigest()
    rinex_sha = hashlib.sha256(rinex).hexdigest()
    if manifest.get("source_sha256") != source_sha or manifest.get("rinex_sha256") != rinex_sha:
        raise ValueError("cached RINEX SHA-256 does not match manifest")
    return CachedRinexNav(
        source_url=str(manifest["source_url"]),
        source_date=day,
        source_filename=filename,
        source_sha256=source_sha,
        rinex_sha256=rinex_sha,
        gzip_path=gzip_path,
        rinex_path=rinex_path,
        manifest_path=manifest_path,
        transport="cache",
    )


def _cache_paths(cache_root: Path, provider_key: str, day: date, filename: str) -> tuple[Path, Path, Path]:
    doy = day.timetuple().tm_yday
    directory = cache_root.resolve() / f"igs-{provider_key}" / "brdc" / f"{day.year:04d}" / f"{doy:03d}"
    directory.mkdir(parents=True, exist_ok=True)
    gzip_path = directory / filename
    rinex_path = directory / filename.removesuffix(".gz")
    manifest_path = directory / (filename + ".manifest.json")
    return gzip_path, rinex_path, manifest_path


def _cached_whu_if_valid(day: date, system: str, cache_root: Path) -> CachedRinexNav | None:
    doy = day.timetuple().tm_yday
    directory = cache_root.resolve() / "igs-whu" / "brdc" / f"{day.year:04d}" / f"{doy:03d}"
    if not directory.is_dir():
        return None
    for manifest_path in sorted(directory.glob("*.manifest.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest.get("source_date") != day.isoformat() or manifest.get("constellation") != system:
                continue
            filename = str(manifest["source_filename"])
            cached = _cached_rinex_if_valid(
                day=day,
                system=system,
                filename=filename,
                gzip_path=directory / filename,
                rinex_path=directory / filename.removesuffix(".gz"),
                manifest_path=manifest_path,
            )
            if cached is not None:
                return cached
        except (KeyError, OSError, ValueError, json.JSONDecodeError):
            continue
    return None


def _parse_directory_listing(raw: bytes) -> list[str]:
    text = raw.decode("utf-8", errors="replace")
    names: set[str] = set()
    for match in re.finditer(r'href=["\']([^"\']+)["\']', text, flags=re.IGNORECASE):
        name = match.group(1).rstrip("/").split("/")[-1]
        if name and name not in {".", ".."}:
            names.add(name)
    for line in text.splitlines():
        parts = line.strip().split()
        if parts:
            name = parts[-1].rstrip("/").split("/")[-1]
            if name and name not in {".", ".."}:
                names.add(name)
    return sorted(names)


def _select_whu_navigation_file(names: list[str], day: date, system: str) -> str:
    suffix = f"_{_SYSTEM_SUFFIX[system]}.rnx.gz".lower()
    token = f"{day.year:04d}{day.timetuple().tm_yday:03d}0000"
    candidates = [name for name in names if name.lower().endswith(".rnx.gz")]
    if not candidates:
        raise ValueError("WHU daily navigation directory contains no supported .rnx.gz files")

    def score(name: str) -> tuple[int, int, int, int, str]:
        lower = name.lower()
        return (
            1 if lower.endswith(suffix) else 0,
            1 if lower.endswith("_mn.rnx.gz") else 0,
            1 if name.startswith("BRDC") else 0,
            1 if token in name else 0,
            name,
        )

    selected = max(candidates, key=score)
    lower = selected.lower()
    if not (lower.endswith(suffix) or lower.endswith("_mn.rnx.gz")):
        raise ValueError(f"WHU daily directory has no navigation file suitable for {system}")
    return selected


def _store_cache(
    *,
    provider_key: str,
    provider: str,
    day: date,
    system: str,
    source_url: str,
    filename: str,
    compressed: bytes,
    rinex: bytes,
    transport: str,
    cache_root: Path,
    extra_manifest: dict[str, object] | None = None,
) -> CachedRinexNav:
    gzip_path, rinex_path, manifest_path = _cache_paths(cache_root, provider_key, day, filename)
    source_sha = hashlib.sha256(compressed).hexdigest()
    rinex_sha = hashlib.sha256(rinex).hexdigest()
    if gzip_path.exists() and hashlib.sha256(gzip_path.read_bytes()).hexdigest() != source_sha:
        raise ValueError("immutable RINEX gzip cache collision: existing file has different SHA-256")
    if rinex_path.exists() and hashlib.sha256(rinex_path.read_bytes()).hexdigest() != rinex_sha:
        raise ValueError("immutable RINEX cache collision: existing file has different SHA-256")
    if not gzip_path.exists():
        gzip_path.write_bytes(compressed)
    if not rinex_path.exists():
        rinex_path.write_bytes(rinex)
    manifest: dict[str, object] = {
        "schema": "oc-gnss-rinex-cache-v1",
        "provider": provider,
        "constellation": system,
        "format": "RINEX NAV",
        "source_url": source_url,
        "source_date": day.isoformat(),
        "source_filename": filename,
        "source_sha256": source_sha,
        "rinex_sha256": rinex_sha,
        "gzip_path": str(gzip_path),
        "rinex_path": str(rinex_path),
        "transport": transport,
        "cached_at_utc": datetime.now(UTC).isoformat(),
    }
    if extra_manifest:
        manifest.update(extra_manifest)
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing.get("source_sha256") != source_sha or existing.get("rinex_sha256") != rinex_sha:
            raise ValueError("immutable RINEX manifest collision")
    else:
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return CachedRinexNav(
        source_url=source_url,
        source_date=day,
        source_filename=filename,
        source_sha256=source_sha,
        rinex_sha256=rinex_sha,
        gzip_path=gzip_path,
        rinex_path=rinex_path,
        manifest_path=manifest_path,
        transport=transport,
    )


def _fetch_bkg_only(day: date, system: str, cache_root: Path, timeout_s: float) -> CachedRinexNav:
    url = bkg_gnss_daily_url(day, system)
    filename = url.rsplit("/", 1)[-1]
    gzip_path, rinex_path, manifest_path = _cache_paths(cache_root, "bkg", day, filename)
    cached = _cached_rinex_if_valid(
        day=day,
        system=system,
        filename=filename,
        gzip_path=gzip_path,
        rinex_path=rinex_path,
        manifest_path=manifest_path,
    )
    if cached is not None:
        return cached
    response = fetch_reviewed_url(url, timeout_s=timeout_s)
    compressed = response.raw
    if len(compressed) > _MAX_RINEX_GZIP_BYTES:
        raise ValueError("BKG/IGS RINEX payload exceeds safety limit")
    if not compressed:
        raise ValueError("BKG/IGS RINEX payload is empty")
    if "html" in response.content_type.lower() or compressed[:64].lstrip().lower().startswith(b"<html"):
        raise ValueError("BKG/IGS RINEX URL returned HTML instead of gzip RINEX")
    try:
        rinex = gzip.decompress(compressed)
    except OSError as exc:
        raise ValueError(f"BKG/IGS {system} source is not valid gzip data") from exc
    _validate_complete_rinex_system(rinex, system)
    return _store_cache(
        provider_key="bkg",
        provider="BKG / IGS GNSS Data Center",
        day=day,
        system=system,
        source_url=url,
        filename=filename,
        compressed=compressed,
        rinex=rinex,
        transport=response.transport,
        cache_root=cache_root,
    )


def _fetch_whu_only(day: date, system: str, cache_root: Path, timeout_s: float) -> CachedRinexNav:
    cached = _cached_whu_if_valid(day, system, cache_root)
    if cached is not None:
        return cached
    directory_url = whu_gnss_daily_directory(day)
    listing = fetch_reviewed_url(directory_url, timeout_s=timeout_s)
    filename = _select_whu_navigation_file(_parse_directory_listing(listing.raw), day, system)
    source_url = urljoin(directory_url, filename)
    response = fetch_reviewed_url(source_url, timeout_s=timeout_s)
    compressed = response.raw
    if len(compressed) > _MAX_RINEX_GZIP_BYTES:
        raise ValueError("WHU/IGS RINEX payload exceeds safety limit")
    if not compressed:
        raise ValueError("WHU/IGS RINEX payload is empty")
    try:
        rinex = gzip.decompress(compressed)
    except OSError as exc:
        raise ValueError(f"WHU/IGS {system} source is not valid gzip data") from exc
    _validate_complete_rinex_system(rinex, system)
    return _store_cache(
        provider_key="whu",
        provider="Wuhan University IGS Data Center",
        day=day,
        system=system,
        source_url=source_url,
        filename=filename,
        compressed=compressed,
        rinex=rinex,
        transport=response.transport,
        cache_root=cache_root,
        extra_manifest={
            "listing_url": directory_url,
            "listing_transport": listing.transport,
        },
    )


def fetch_bkg_gnss_daily(
    day: date,
    system: str,
    cache_root: Path,
    *,
    timeout_s: float = 30.0,
) -> CachedRinexNav:
    """Fetch daily broadcast NAV using cache-first independent IGS failover.

    The legacy function name is kept for API compatibility. Automatic fallbacks are
    only sources qualified as global broadcast-navigation authorities. Station-level
    NAV/OBS candidates remain visible in IGS_SOURCE_CANDIDATES but are not silently
    substituted for a global constellation source.
    """
    if timeout_s <= 0.0:
        raise ValueError("timeout_s must be positive")
    if system not in _SYSTEM_SUFFIX:
        raise ValueError(f"unsupported GNSS system: {system}")

    # Cache-first across every currently qualified automatic provider.
    bkg_url = bkg_gnss_daily_url(day, system)
    bkg_filename = bkg_url.rsplit("/", 1)[-1]
    bkg_paths = _cache_paths(cache_root, "bkg", day, bkg_filename)
    bkg_cached = _cached_rinex_if_valid(
        day=day,
        system=system,
        filename=bkg_filename,
        gzip_path=bkg_paths[0],
        rinex_path=bkg_paths[1],
        manifest_path=bkg_paths[2],
    )
    if bkg_cached is not None:
        return bkg_cached
    whu_cached = _cached_whu_if_valid(day, system, cache_root)
    if whu_cached is not None:
        return whu_cached

    errors: list[str] = []
    try:
        return _fetch_bkg_only(day, system, cache_root, timeout_s)
    except (OSError, ValueError) as exc:
        errors.append(f"BKG: {exc}")
    try:
        return _fetch_whu_only(day, system, cache_root, timeout_s)
    except (OSError, ValueError) as exc:
        errors.append(f"WHU: {exc}")
    raise OSError(f"all reviewed IGS {system} broadcast-navigation sources failed; " + "; ".join(errors))


def fetch_bkg_glonass_daily(
    day: date,
    cache_root: Path,
    *,
    timeout_s: float = 30.0,
) -> CachedRinexNav:
    return fetch_bkg_gnss_daily(day, "GLONASS", cache_root, timeout_s=timeout_s)
