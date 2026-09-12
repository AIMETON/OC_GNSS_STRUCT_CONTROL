from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urljoin

from constellation_control.adapters.fcnd_api import FcndApiClient
from constellation_control.adapters.reviewed_http_fetch import fetch_reviewed_url
from constellation_control.preview.source_runtime import (
    _decode_rinex_payload,
    _fcnd_record_name,
    _fcnd_record_time,
    _walk_json_dicts,
)


def _first_lines(raw: bytes, count: int = 6) -> list[str]:
    return raw.decode("ascii", errors="replace").splitlines()[:count]


def _assert_galileo_nav(raw: bytes, source_name: str) -> None:
    lines = _first_lines(raw, 20)
    if not lines:
        raise RuntimeError(f"{source_name}: empty RINEX")
    first = lines[0].upper()
    if "NAV" not in first:
        raise RuntimeError(f"{source_name}: not navigation RINEX: {lines[0]!r}")
    if "GALILEO" not in first and " E " not in first:
        raise RuntimeError(f"{source_name}: header does not establish Galileo: {lines[0]!r}")


def probe_iac(day) -> None:
    doy = day.timetuple().tm_yday
    yy = day.year % 100
    directory = f"ftp://ftp.glonass-iac.ru/MCC/BRDC/{day.year:04d}/"
    listing = fetch_reviewed_url(directory, timeout_s=60.0)
    names = listing.raw.decode("utf-8", errors="replace").splitlines()
    expected = f"Brdc{doy:03d}0.{yy:02d}l"
    candidates = [name.strip() for name in names if name.strip().lower() in {expected.lower(), (expected + '.z').lower(), (expected + '.gz').lower()}]
    if not candidates:
        raise RuntimeError(f"IAC Galileo candidate missing: expected {expected}")
    name = candidates[0]
    url = urljoin(directory, name)
    response = fetch_reviewed_url(url, timeout_s=60.0)
    rinex = _decode_rinex_payload(response.raw, name)
    print("IAC_GALILEO_FIRST_LINES", json.dumps(_first_lines(rinex), ensure_ascii=False))
    _assert_galileo_nav(rinex, name)
    print("IAC_GALILEO_RUNTIME_OK", name, url, len(rinex))


def probe_fcnd(day) -> None:
    client = FcndApiClient(base_url="https://fcnd.ru", timeout_s=60.0)
    begin = f"{day.strftime('%d-%m-%Y')} 00:00:00"
    end = f"{day.strftime('%d-%m-%Y')} 23:59:59"
    for collection_id in (134, 56, 148, 155):
        catalogue = client.list_data(
            time_begin=begin,
            time_end=end,
            meta_collections=(collection_id,),
            limit=1000,
        )
        candidates: list[tuple[str, str]] = []
        for record in _walk_json_dicts(catalogue):
            name = _fcnd_record_name(record)
            if not name:
                continue
            lower = name.lower()
            if lower.endswith(f".{day.year % 100:02d}l") or lower.endswith(f".{day.year % 100:02d}l.z"):
                candidates.append((name, _fcnd_record_time(record, day)))
        print(f"FCND_GALILEO_COLLECTION collection={collection_id} candidates={len(candidates)}")
        for name, time_begin in candidates[:8]:
            payload = client.download_datafile(time_begin=time_begin, file_name=name)
            rinex = _decode_rinex_payload(payload, name)
            print("FCND_GALILEO_FIRST_LINES", collection_id, name, json.dumps(_first_lines(rinex), ensure_ascii=False))
            try:
                _assert_galileo_nav(rinex, name)
            except RuntimeError as exc:
                print("FCND_GALILEO_REJECT", exc)
                continue
            query = urlencode([("datafile[time_begin]", time_begin), ("datafile[file_name]", name)])
            print("FCND_GALILEO_RUNTIME_OK", collection_id, name, "https://fcnd.ru/api/getData/?" + query, len(rinex))
            return
    raise RuntimeError("FCND returned no qualified Galileo broadcast-navigation candidate")


def main() -> None:
    day = (datetime.now(UTC) - timedelta(days=1)).date()
    print("Galileo RF qualification day", day.isoformat())
    probe_iac(day)
    probe_fcnd(day)


if __name__ == "__main__":
    main()
