from __future__ import annotations

import gzip
from datetime import date
from pathlib import Path

import pytest

from constellation_control.adapters.bkg_rinex_nav import CachedRinexNav
from constellation_control.adapters.reviewed_http_fetch import ReviewedHttpResponse
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


def _rinex2_glonass(day: date) -> bytes:
    return (
        "     2.11           GLONASS NAV DATA                         RINEX VERSION / TYPE\n"
        "                                                            END OF HEADER\n"
        f" 1 {day.year % 100:02d} {day.month:2d} {day.day:2d}  0  0  0.0 0.0 0.0 0.0\n"
    ).encode("ascii")


def _rinex3_glonass(day: date) -> bytes:
    return (
        "     3.05           NAVIGATION DATA     R                   RINEX VERSION / TYPE\n"
        "                                                            END OF HEADER\n"
        f"R01 {day.year:04d} {day.month:02d} {day.day:02d} 00 00 00  0 0 0\n"
    ).encode("ascii")


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
    rinex = _rinex3_glonass(date(2026, 9, 10))
    with pytest.raises(ValueError, match="2026-09-11"):
        source_runtime._validate_requested_day(rinex, date(2026, 9, 11), "wrong.rnx")


def test_requested_day_validation_accepts_matching_daily_file() -> None:
    rinex = _rinex3_glonass(date(2026, 9, 11))
    source_runtime._validate_requested_day(rinex, date(2026, 9, 11), "ok.rnx")


def test_rinex2_glonass_broadcast_is_accepted_by_filename_and_header() -> None:
    day = date(2026, 9, 11)
    name = "Brdc2540.26g"
    assert source_runtime._looks_like_rinex_name(name)
    source_runtime._validate_runtime_rinex_system(_rinex2_glonass(day), "GLONASS", source_name=name)
    source_runtime._validate_requested_day(_rinex2_glonass(day), day, name)


def test_iac_mcc_brdc_selects_exact_glonass_daily_file(tmp_path: Path, monkeypatch) -> None:
    day = date(2026, 9, 11)
    setting = next(item for item in DEFAULT_SOURCE_SETTINGS.sources if item.source_id == "iac_ftp_archive")
    calls: list[str] = []

    def fake_fetch(url: str, *, timeout_s: float):
        calls.append(url)
        if url.endswith("/MCC/BRDC/2026/"):
            return ReviewedHttpResponse(raw=b"Brdc2530.26g\nBrdc2540.26g\nBrdc2540.26n\n", content_type=None, transport="curl")
        if url.endswith("/Brdc2540.26g"):
            return ReviewedHttpResponse(raw=_rinex2_glonass(day), content_type=None, transport="curl")
        raise AssertionError(url)

    monkeypatch.setattr(source_runtime, "fetch_reviewed_url", fake_fetch)
    cached = source_runtime._fetch_iac_ftp_rinex(day, "GLONASS", tmp_path, 30.0, setting)
    assert cached.source_filename == "Brdc2540.26g"
    assert cached.rinex_path.read_bytes() == _rinex2_glonass(day)
    assert calls == [
        "ftp://ftp.glonass-iac.ru/MCC/BRDC/2026/",
        "ftp://ftp.glonass-iac.ru/MCC/BRDC/2026/Brdc2540.26g",
    ]


def test_whu_discovers_real_mixed_navigation_directory(tmp_path: Path, monkeypatch) -> None:
    day = date(2026, 9, 11)
    setting = next(item for item in DEFAULT_SOURCE_SETTINGS.sources if item.source_id == "igs_whu")
    payload = gzip.compress(_rinex3_glonass(day))
    calls: list[str] = []

    def fake_fetch(url: str, *, timeout_s: float):
        calls.append(url)
        if url.endswith("/2026/254/"):
            return ReviewedHttpResponse(raw=b"26g\n26m\n26n\n", content_type=None, transport="curl")
        if url.endswith("/2026/254/26m/"):
            return ReviewedHttpResponse(
                raw=b"TEST00AAA_R_20262540000_01D_MM.rnx.gz\n",
                content_type=None,
                transport="curl",
            )
        if url.endswith("/TEST00AAA_R_20262540000_01D_MM.rnx.gz"):
            return ReviewedHttpResponse(raw=payload, content_type="application/gzip", transport="curl")
        raise AssertionError(url)

    monkeypatch.setattr(source_runtime, "fetch_reviewed_url", fake_fetch)
    cached = source_runtime._fetch_configured_whu(day, "GLONASS", tmp_path, 30.0, setting)
    assert cached.source_filename.endswith("_MM.rnx.gz")
    assert calls[0].endswith("/2026/254/")
    assert "/26m/" in calls[1]


def test_fcnd_live_schema_keys_are_recognized() -> None:
    day = date(2026, 9, 11)
    record = {
        "pt_time_begin": "2026-09-11 23:59:50.000051",
        "pk_file_name": "BRDC2540.26g",
        "c_meta_file": {"CollectionShortName": "example"},
    }
    assert source_runtime._fcnd_record_name(record) == "BRDC2540.26g"
    assert source_runtime._fcnd_record_time(record, day) == "2026-09-11 23:59:50.000051"
