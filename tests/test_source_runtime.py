from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from constellation_control.adapters.bkg_rinex_nav import CachedRinexNav
from constellation_control.preview import source_runtime
from constellation_control.preview.source_settings import DEFAULT_SOURCE_SETTINGS, save_source_settings


def _cached(tmp_path: Path, provider: str) -> CachedRinexNav:
    root = tmp_path / provider
    root.mkdir(parents=True, exist_ok=True)
    gzip_path = root / "source.rnx.gz"
    rinex_path = root / "source.rnx"
    manifest_path = root / "source.rnx.gz.manifest.json"
    gzip_path.write_bytes(b"gzip")
    rinex_path.write_text("rinex", encoding="ascii")
    manifest_path.write_text("{}", encoding="utf-8")
    return CachedRinexNav(
        source_url=f"https://{provider}.example.test/source.rnx.gz",
        source_date=date(2026, 9, 11),
        source_filename="source.rnx.gz",
        source_sha256="a" * 64,
        rinex_sha256="b" * 64,
        gzip_path=gzip_path,
        rinex_path=rinex_path,
        manifest_path=manifest_path,
        transport="test",
    )


def test_auto_dispatch_uses_russian_sources_before_igs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OC_GNSS_SOURCE_SETTINGS", str(tmp_path / "settings.json"))
    save_source_settings(DEFAULT_SOURCE_SETTINGS.model_copy(deep=True))
    calls: list[str] = []

    def iac(*args, **kwargs):
        calls.append("iac_ftp_archive")
        raise OSError("IAC unavailable")

    def fcnd(*args, **kwargs):
        calls.append("fcnd_api")
        return _cached(tmp_path, "fcnd")

    def forbidden(*args, **kwargs):
        calls.append("unexpected_igs")
        raise AssertionError("IGS fallback must not run after FCND success")

    monkeypatch.setattr(source_runtime, "_fetch_iac_ftp_rinex", iac)
    monkeypatch.setattr(source_runtime, "_fetch_fcnd_rinex", fcnd)
    monkeypatch.setattr(source_runtime, "_fetch_configured_bkg", forbidden)
    monkeypatch.setattr(source_runtime, "_fetch_configured_whu", forbidden)

    selected = source_runtime.fetch_selected_broadcast_rinex(
        date(2026, 9, 11),
        "GLONASS",
        tmp_path / "cache",
    )
    assert selected.source_id == "fcnd_api"
    assert calls == ["iac_ftp_archive", "fcnd_api"]
    assert [item.status for item in selected.attempts] == ["failed", "success"]


def test_manual_dispatch_does_not_fall_back(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OC_GNSS_SOURCE_SETTINGS", str(tmp_path / "settings.json"))
    document = DEFAULT_SOURCE_SETTINGS.model_copy(deep=True)
    document.selection["GLONASS"].mode = "manual"
    document.selection["GLONASS"].selected_source_id = "fcnd_api"
    save_source_settings(document)
    calls: list[str] = []

    def fcnd(*args, **kwargs):
        calls.append("fcnd_api")
        raise OSError("FCND unavailable")

    def forbidden(*args, **kwargs):
        calls.append("fallback")
        raise AssertionError("MANUAL mode must be fail-closed")

    monkeypatch.setattr(source_runtime, "_fetch_fcnd_rinex", fcnd)
    monkeypatch.setattr(source_runtime, "_fetch_iac_ftp_rinex", forbidden)
    monkeypatch.setattr(source_runtime, "_fetch_configured_bkg", forbidden)
    monkeypatch.setattr(source_runtime, "_fetch_configured_whu", forbidden)

    with pytest.raises(OSError, match="fcnd_api=failed"):
        source_runtime.fetch_selected_broadcast_rinex(
            date(2026, 9, 11),
            "GLONASS",
            tmp_path / "cache",
        )
    assert calls == ["fcnd_api"]


def test_manual_incompatible_source_is_rejected_before_network(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OC_GNSS_SOURCE_SETTINGS", str(tmp_path / "settings.json"))
    document = DEFAULT_SOURCE_SETTINGS.model_copy(deep=True)
    document.selection["GLONASS"].mode = "manual"
    document.selection["GLONASS"].selected_source_id = "iac_glonass"
    save_source_settings(document)

    with pytest.raises(ValueError, match="selected source is unavailable"):
        source_runtime.fetch_selected_broadcast_rinex(
            date(2026, 9, 11),
            "GLONASS",
            tmp_path / "cache",
        )


def test_requested_day_validation_rejects_wrong_daily_file() -> None:
    rinex = b"     3.05           NAVIGATION DATA     R                   RINEX VERSION / TYPE\nEND OF HEADER\nR01 2026 09 10 00 00 00  0 0 0\n"
    with pytest.raises(ValueError, match="2026-09-11"):
        source_runtime._validate_requested_day(rinex, date(2026, 9, 11), "wrong.rnx")


def test_requested_day_validation_accepts_matching_daily_file() -> None:
    rinex = b"     3.05           NAVIGATION DATA     R                   RINEX VERSION / TYPE\nEND OF HEADER\nR01 2026 09 11 00 00 00  0 0 0\n"
    source_runtime._validate_requested_day(rinex, date(2026, 9, 11), "ok.rnx")
