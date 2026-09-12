from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "https://fcnd.ru/api/getData/"
COLLECTIONS = (134, 58, 56, 148)
RINEX2_NAV = re.compile(r"(?i)\.(?P<yy>\d{2})(?P<kind>[gfnl])(?:\.z)?$")


def get(url: str, timeout: float = 30.0) -> bytes:
    request = Request(url, headers={"User-Agent": "OC-GNSS-STRUCT-CONTROL/0.2.15-fcnd-probe"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed reviewed host
        return response.read()


def catalogue_url(collection: int, begin: str, end: str) -> str:
    query = urlencode(
        [
            ("filter[time_begin]", begin),
            ("filter[time_end]", end),
            ("filter[meta_collection][]", str(collection)),
            ("filter[limit]", "500"),
        ]
    )
    return BASE + "?" + query


def datafile_url(time_begin: str, file_name: str) -> str:
    query = urlencode(
        [
            ("datafile[time_begin]", time_begin),
            ("datafile[file_name]", file_name),
        ]
    )
    return BASE + "?" + query


def main() -> None:
    day = (datetime.now(UTC) - timedelta(days=1)).date()
    begin = f"{day.strftime('%d-%m-%Y')} 00:00:00"
    end = f"{day.strftime('%d-%m-%Y')} 23:59:59"
    print(f"FCND qualification day={day.isoformat()}")
    for collection in COLLECTIONS:
        url = catalogue_url(collection, begin, end)
        try:
            payload = json.loads(get(url).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"collection={collection} ERROR {type(exc).__name__}: {exc}")
            continue
        print(f"collection={collection} records={len(payload) if isinstance(payload, list) else 'non-list'}")
        if not isinstance(payload, list):
            continue
        nav = []
        for record in payload:
            if not isinstance(record, dict):
                continue
            name = str(record.get("pk_file_name") or "")
            match = RINEX2_NAV.search(name)
            if match or name.lower().endswith((".rnx", ".rnx.gz", ".nav", ".nav.gz")):
                nav.append(record)
        print(f"collection={collection} nav_candidates={len(nav)}")
        for record in nav[:20]:
            print(
                json.dumps(
                    {
                        "pk_file_name": record.get("pk_file_name"),
                        "pt_time_begin": record.get("pt_time_begin"),
                        "fk_meta_collection": record.get("fk_meta_collection"),
                        "c_meta_file": record.get("c_meta_file"),
                    },
                    ensure_ascii=False,
                )[:3000]
            )
        glonass = next(
            (
                record
                for record in nav
                if RINEX2_NAV.search(str(record.get("pk_file_name") or ""))
                and RINEX2_NAV.search(str(record.get("pk_file_name") or "")).group("kind").lower() == "g"
            ),
            None,
        )
        if glonass is not None:
            name = str(glonass["pk_file_name"])
            time_begin = str(glonass["pt_time_begin"])
            raw = get(datafile_url(time_begin, name), timeout=60.0)
            print(f"collection={collection} DOWNLOAD_OK name={name} bytes={len(raw)} prefix={raw[:100]!r}")
            break


if __name__ == "__main__":
    main()
