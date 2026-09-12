from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def http_get(url: str, timeout: float = 20.0) -> bytes:
    request = Request(url, headers={"User-Agent": "OC-GNSS-STRUCT-CONTROL/0.2.15-source-probe"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed reviewed URLs only
        return response.read()


def print_json_probe(label: str, url: str) -> None:
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
    except Exception as exc:  # noqa: BLE001 - diagnostic probe must continue
        print(f"ERROR {type(exc).__name__}: {exc}")


def curl_ftp(label: str, url: str) -> None:
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
    if completed.stdout:
        lines = completed.stdout.splitlines()
        print(f"lines={len(lines)}")
        for line in lines[:120]:
            print(line)
    if completed.stderr:
        print("stderr:", completed.stderr[:2000])


def main() -> None:
    print_json_probe(
        "IAC GLONASS live",
        "https://glonass-iac.ru/glonass/ephemeris/ephemeris_json.php",
    )
    print_json_probe("FCND filters", "https://fcnd.ru/api/getFilter")

    day = (datetime.now(UTC) - timedelta(days=1)).date()
    begin = f"{day.strftime('%d-%m-%Y')} 00:00:00"
    end = f"{day.strftime('%d-%m-%Y')} 23:59:59"
    query = urlencode(
        [
            ("filter[time_begin]", begin),
            ("filter[time_end]", end),
            ("filter[data_type]", "gnss"),
            ("filter[limit]", "20"),
        ]
    )
    print_json_probe("FCND GNSS previous-day catalogue", f"https://fcnd.ru/api/getData/?{query}")

    curl_ftp("IAC FTP root", "ftp://ftp.glonass-iac.ru/")
    curl_ftp("IAC FTP MCC", "ftp://ftp.glonass-iac.ru/MCC/")
    curl_ftp("IAC FTP IGS", "ftp://ftp.glonass-iac.ru/IGS/")

    year = day.year
    doy = day.timetuple().tm_yday
    yy = year % 100
    whu_root = f"ftp://igs.gnsswhu.cn/pub/gps/data/daily/{year:04d}/{doy:03d}/"
    curl_ftp("WHU day root", whu_root)
    for suffix in ("p", "n", "g"):
        curl_ftp(f"WHU {yy:02d}{suffix}", f"{whu_root}{yy:02d}{suffix}/")


if __name__ == "__main__":
    main()
