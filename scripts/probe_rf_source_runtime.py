from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from constellation_control.preview.source_runtime import (
    _fetch_configured_whu,
    _fetch_fcnd_rinex,
    _fetch_iac_ftp_rinex,
)
from constellation_control.preview.source_settings import source_setting


def required_setting(source_id: str):
    setting = source_setting(source_id)
    if setting is None:
        raise RuntimeError(f"missing source setting: {source_id}")
    return setting


def main() -> None:
    day = (datetime.now(UTC) - timedelta(days=1)).date()
    print(f"runtime qualification day={day.isoformat()}")
    with TemporaryDirectory(prefix="oc-gnss-source-probe-") as raw_root:
        root = Path(raw_root)

        iac = _fetch_iac_ftp_rinex(
            day,
            "GLONASS",
            root / "iac",
            60.0,
            required_setting("iac_ftp_archive"),
        )
        print(
            "IAC_FTP_RUNTIME_OK",
            iac.source_filename,
            iac.source_url,
            iac.rinex_sha256,
            iac.rinex_path.stat().st_size,
        )

        whu = _fetch_configured_whu(
            day,
            "GLONASS",
            root / "whu",
            60.0,
            required_setting("igs_whu"),
        )
        print(
            "WHU_RUNTIME_OK",
            whu.source_filename,
            whu.source_url,
            whu.rinex_sha256,
            whu.rinex_path.stat().st_size,
        )

        try:
            fcnd = _fetch_fcnd_rinex(
                day,
                "GLONASS",
                root / "fcnd",
                30.0,
                required_setting("fcnd_api"),
            )
        except (OSError, ValueError) as exc:
            print("FCND_RUNTIME_NOT_YET_QUALIFIED", exc)
        else:
            print(
                "FCND_RUNTIME_OK",
                fcnd.source_filename,
                fcnd.source_url,
                fcnd.rinex_sha256,
                fcnd.rinex_path.stat().st_size,
            )


if __name__ == "__main__":
    main()
