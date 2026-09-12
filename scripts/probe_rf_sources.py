from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def http_get(url: str, timeout: float = 20.0) -> bytes:
    request = Request(url, headers={"User-Agent": "OC-GNSS-STRUCT-CONTROL/0.2.15-source-probe"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed reviewed URLs only
        return response.read()


def _shape(value: Any, *, depth: int = 0, max_depth: int = 3) -> Any:
    if depth >= max_depth:
        if isinstance(value, dict):
            return {"type": "dict", "keys": list(value)[:20], "len": len(value)}
        if isinstance(value, list):
            return {"type": "list", "len": len(value), "sample": value[:1]}
        return value
    if isinstance(value, dict):
        return {key: _shape(child, depth=depth + 1, max_depth=max_depth) for key, child in list(value.items())[:30]}
    if isinstance(value, list):
        return [_shape(child, depth=depth + 1, max_depth=max_depth) for child in value[:3]]
    return value


def _matching_strings(value: Any, pattern: re.Pattern[str], path: str = "$") -> list[str]:
    matches: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            matches.extend(_matching_strings(child, pattern, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            matches.extend(_matching_strings(child, pattern, f"{path}[{index}]"))
    elif isinstance(value, str) and pattern.search(value):
        matches.append(f"{path}={value}")
    return matches


def print_json_probe(label: str, url: str, *, inspect_contract: bool = False) -> None:
    print(f"\n=== {label} ===")
    print(url)
    try:
        raw = http_get(url)
        print(f"bytes={len(raw)} prefix={raw[:180]!r}")
        payload = json.loads(raw.decode("utf-8"))
        print(f"json_type={type(payload).__name__}")
        if isinstance(payload, dict):
            print("top_keys=", list(payload)[:30])
            for key, value in payload.items():
                if isinstance(value, list):
                    print(f"list[{key}] len={len(value)} sample={value[:2]!r}")
                elif isinstance(value, dict):
                    print(f"dict[{key}] keys={list(value)[:30]}")
        elif isinstance(payload, list):
            print(f"list_len={len(payload)} sample={payload[:2]!r}")
        if inspect_contract:
            print("shape=", json.dumps(_shape(payload), ensure_ascii=False)[:12000])
            pattern = re.compile(r"gnss|rinex|nav|orbit|ephem|альман|эфем", re.IGNORECASE)
            matches = _matching_strings(payload, pattern)
            print(f"contract_matches={len(matches)}")
            for match in matches[:160]:
                print(match)
    except Exception as exc:  # noqa: BLE001 - diagnostic probe must continue
        print(f"ERROR {type(exc).__name__}: {exc}")


def curl_ftp(label: str, url: str) -> list[str]:
    print(f"\n=== {label} ===")
    print(url)
    command = [
        "curl",
        "--silent",
        "--show-error",
        "--connect-timeout",
        "12",
        "--max-time",
        "30",
        "--user",
        "anonymous:anonymous",
        "--disable-epsv",
        "--list-only",
        url,
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=40, check=False)
    print(f"exit={completed.returncode}")
    lines = completed.stdout.splitlines() if completed.stdout else []
    if lines:
        print(f"lines={len(lines)}")
        for line in lines[:160]:
            print(line)
    if completed.stderr:
        print("stderr:", completed.stderr[:2000])
    return lines


def fcnd_catalogue_url(begin: str, end: str, *, data_type: str | None = None, org: int | None = None) -> str:
    params: list[tuple[str, str]] = [
        ("filter[time_begin]", begin),
        ("filter[time_end]", end),
        ("filter[limit]", "30"),
    ]
    if data_type:
        params.append(("filter[data_type]", data_type))
    if org is not None:
        params.append(("filter[org][]", str(org)))
    return "https://fcnd.ru/api/getData/?" + urlencode(params)


def main() -> None:
    print_json_probe(
        "IAC GLONASS live",
        "https://glonass-iac.ru/glonass/ephemeris/ephemeris_json.php",
    )
    print_json_probe("FCND filters", "https://fcnd.ru/api/getFilter", inspect_contract=True)

    day = (datetime.now(UTC) - timedelta(days=1)).date()
    begin = f"{day.strftime('%d-%m-%Y')} 00:00:00"
    end = f"{day.strftime('%d-%m-%Y')} 23:59:59"
    print_json_probe(
        "FCND previous-day catalogue, no product guess",
        fcnd_catalogue_url(begin, end),
        inspect_contract=True,
    )
    print_json_probe(
        "FCND previous-day catalogue data_type=gnss",
        fcnd_catalogue_url(begin, end, data_type="gnss"),
        inspect_contract=True,
    )
    print_json_probe(
        "FCND documentation example",
        fcnd_catalogue_url("28-03-2022 00:00:00", "28-03-2022 23:59:59", data_type="gnss", org=1),
        inspect_contract=True,
    )

    curl_ftp("IAC FTP root", "ftp://ftp.glonass-iac.ru/")
    curl_ftp("IAC FTP MCC", "ftp://ftp.glonass-iac.ru/MCC/")
    curl_ftp("IAC FTP MCC/BRDC", "ftp://ftp.glonass-iac.ru/MCC/BRDC/")
    curl_ftp("IAC FTP MCC/ALMANAC", "ftp://ftp.glonass-iac.ru/MCC/ALMANAC/")
    curl_ftp("IAC FTP IGS", "ftp://ftp.glonass-iac.ru/IGS/")
    curl_ftp("IAC FTP IGS/BRDC", "ftp://ftp.glonass-iac.ru/IGS/BRDC/")

    year = day.year
    doy = day.timetuple().tm_yday
    yy = year % 100
    whu_root = f"ftp://igs.gnsswhu.cn/pub/gps/data/daily/{year:04d}/{doy:03d}/"
    curl_ftp("WHU day root", whu_root)
    for suffix in ("g", "n", "m", "p"):
        curl_ftp(f"WHU {yy:02d}{suffix}", f"{whu_root}{yy:02d}{suffix}/")


if __name__ == "__main__":
    main()
