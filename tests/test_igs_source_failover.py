from datetime import date
from pathlib import Path

from constellation_control.adapters import bkg_rinex_nav
from constellation_control.adapters.bkg_rinex_nav import CachedRinexNav


def test_igs_source_registry_keeps_only_qualified_broadcast_sources_automatic() -> None:
    by_key = {source.key: source for source in bkg_rinex_nav.IGS_SOURCE_CANDIDATES}
    assert by_key["bkg"].status == "active_auto"
    assert by_key["whu"].status == "active_auto"
    assert by_key["whu"].region == "CN"
    assert by_key["ga"].region == "AU"
    assert by_key["ga"].status == "candidate"
    assert by_key["hartrao"].region == "ZA"
    assert by_key["ibge"].region == "BR"
    assert by_key["ibge"].status == "candidate_observation_only"
    assert by_key["kasi"].status == "candidate_field_unreachable"
    assert by_key["cas"].status == "candidate_field_unreachable"


def test_whu_daily_navigation_directory_uses_mixed_navigation_partition() -> None:
    assert bkg_rinex_nav.whu_gnss_daily_directory(date(2026, 9, 10)) == (
        "ftp://igs.gnsswhu.cn/pub/gps/data/daily/2026/253/26p/"
    )


def test_whu_file_selection_prefers_system_specific_then_mixed() -> None:
    day = date(2026, 9, 10)
    names = [
        "BRDC00IGS_R_20262530000_01D_MN.rnx.gz",
        "BRDC00WRD_R_20262530000_01D_RN.rnx.gz",
        "unrelated.txt",
    ]
    assert bkg_rinex_nav._select_whu_navigation_file(names, day, "GLONASS").endswith("_RN.rnx.gz")
    assert bkg_rinex_nav._select_whu_navigation_file(
        ["BRDC00IGS_R_20262530000_01D_MN.rnx.gz"], day, "Galileo"
    ).endswith("_MN.rnx.gz")


def test_bkg_failure_falls_back_to_whu_without_station_source_substitution(
    tmp_path: Path, monkeypatch
) -> None:
    day = date(2026, 9, 10)
    fake = CachedRinexNav(
        source_url="ftp://igs.gnsswhu.cn/pub/gps/data/daily/2026/253/26p/source.rnx.gz",
        source_date=day,
        source_filename="source.rnx.gz",
        source_sha256="a" * 64,
        rinex_sha256="b" * 64,
        gzip_path=tmp_path / "source.rnx.gz",
        rinex_path=tmp_path / "source.rnx",
        manifest_path=tmp_path / "source.rnx.gz.manifest.json",
        transport="curl",
    )

    monkeypatch.setattr(bkg_rinex_nav, "_cached_whu_if_valid", lambda *_args: None)

    def fail_bkg(*_args, **_kwargs):
        raise OSError("field TLS reset")

    monkeypatch.setattr(bkg_rinex_nav, "_fetch_bkg_only", fail_bkg)
    monkeypatch.setattr(bkg_rinex_nav, "_fetch_whu_only", lambda *_args, **_kwargs: fake)

    result = bkg_rinex_nav.fetch_bkg_gnss_daily(day, "GLONASS", tmp_path)
    assert result.source_url.startswith("ftp://igs.gnsswhu.cn/")
    assert result is fake
