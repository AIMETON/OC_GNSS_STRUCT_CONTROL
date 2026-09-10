from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BKG_IGS_BRDC_ROOT = "https://igs.bkg.bund.de/root_ftp/IGS/BRDC"
_MAX_RINEX_GZIP_BYTES = 64 * 1024 * 1024
_SYSTEM_SUFFIX = {
    "GLONASS": "RN",
    "GPS": "GN",
    "Galileo": "EN",
    "BeiDou": "CN",
}


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


def _validate_rinex_nav(raw: bytes) -> None:
    head = raw[:8192].decode("ascii", errors="replace")
    if "RINEX VERSION / TYPE" not in head:
        raise ValueError("downloaded payload is not a RINEX file")
    first = head.splitlines()[0] if head.splitlines() else ""
    if "NAVIGATION DATA" not in first.upper() and "NAV" not in first.upper():
        raise ValueError("downloaded RINEX file is not navigation data")
    if "END OF HEADER" not in head:
        raise ValueError("RINEX navigation header is incomplete")


def fetch_bkg_gnss_daily(
    day: date,
    system: str,
    cache_root: Path,
    *,
    timeout_s: float = 30.0,
) -> CachedRinexNav:
    if timeout_s <= 0.0:
        raise ValueError("timeout_s must be positive")
    url = bkg_gnss_daily_url(day, system)
    filename = url.rsplit("/", 1)[-1]
    doy = day.timetuple().tm_yday
    directory = cache_root.resolve() / "igs-bkg" / "brdc" / f"{day.year:04d}" / f"{doy:03d}"
    directory.mkdir(parents=True, exist_ok=True)
    gzip_path = directory / filename
    rinex_path = directory / filename.removesuffix(".gz")
    manifest_path = directory / (filename + ".manifest.json")

    request = Request(url, headers={"User-Agent": "OC-GNSS-STRUCT-CONTROL/0.2.10"})
    try:
        with urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - fixed reviewed HTTPS origin
            content_type = (response.headers.get("Content-Type") or "").lower()
            compressed = response.read(_MAX_RINEX_GZIP_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise OSError(f"BKG/IGS {system} RINEX download failed: {url}: {exc}") from exc
    if len(compressed) > _MAX_RINEX_GZIP_BYTES:
        raise ValueError("BKG/IGS RINEX payload exceeds safety limit")
    if not compressed:
        raise ValueError("BKG/IGS RINEX payload is empty")
    if "html" in content_type or compressed[:64].lstrip().lower().startswith(b"<html"):
        raise ValueError("BKG/IGS RINEX URL returned HTML instead of gzip RINEX")
    try:
        rinex = gzip.decompress(compressed)
    except OSError as exc:
        raise ValueError(f"BKG/IGS {system} source is not valid gzip data") from exc
    _validate_rinex_nav(rinex)

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

    manifest = {
        "schema": "oc-gnss-rinex-cache-v1",
        "provider": "BKG / IGS GNSS Data Center",
        "constellation": system,
        "format": "RINEX NAV",
        "source_url": url,
        "source_date": day.isoformat(),
        "source_filename": filename,
        "source_sha256": source_sha,
        "rinex_sha256": rinex_sha,
        "gzip_path": str(gzip_path),
        "rinex_path": str(rinex_path),
        "cached_at_utc": datetime.now(UTC).isoformat(),
    }
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing.get("source_sha256") != source_sha or existing.get("rinex_sha256") != rinex_sha:
            raise ValueError("immutable RINEX manifest collision")
    else:
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return CachedRinexNav(
        source_url=url,
        source_date=day,
        source_filename=filename,
        source_sha256=source_sha,
        rinex_sha256=rinex_sha,
        gzip_path=gzip_path,
        rinex_path=rinex_path,
        manifest_path=manifest_path,
    )


def fetch_bkg_glonass_daily(
    day: date,
    cache_root: Path,
    *,
    timeout_s: float = 30.0,
) -> CachedRinexNav:
    return fetch_bkg_gnss_daily(day, "GLONASS", cache_root, timeout_s=timeout_s)
