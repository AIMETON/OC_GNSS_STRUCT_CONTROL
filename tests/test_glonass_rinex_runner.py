from __future__ import annotations

import gzip
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from constellation_control.adapters import bkg_rinex_nav
from constellation_control.adapters.reviewed_http_fetch import ReviewedHttpResponse
from constellation_control.preview.glonass_rinex_runner import (
    GlonassRinexRunnerRequest,
    _validated_target_epoch,
)
from constellation_control.preview.gravity_release_app import render_preview_page_for_test


def _rinex_nav() -> bytes:
    line1 = "     3.05           N: GNSS NAV DATA    R: GLONASS          RINEX VERSION / TYPE\n"
    end = "                                                            END OF HEADER\n"
    return (line1 + end + "R01 2026 09 09 00 00 00 0.0 0.0 0.0\n").encode("ascii")


def _request(*, source_date: date, target_epoch: datetime) -> GlonassRinexRunnerRequest:
    return GlonassRinexRunnerRequest(
        source_date=source_date,
        source_scenario_name="orekit_validation_smoke.yaml",
        template_satellite_id="GLO-01",
        target_epoch=target_epoch,
        max_ephemeris_age_s=7200.0,
        glonass_propagation_step_s=60.0,
        target_scenario_name="derived.yaml",
        new_scenario_id="derived",
    )


def test_bkg_glonass_url_uses_rinex_rn_daily_contract() -> None:
    assert bkg_rinex_nav.bkg_glonass_daily_url(date(2026, 9, 9)).endswith(
        "/2026/252/BRDC00WRD_R_20262520000_01D_RN.rnx.gz"
    )


def test_bkg_glonass_download_is_cached_with_hashes(tmp_path: Path, monkeypatch) -> None:
    payload = gzip.compress(_rinex_nav())
    calls = 0

    def fake_fetch(url: str, *, timeout_s: float):
        nonlocal calls
        calls += 1
        return ReviewedHttpResponse(
            raw=payload,
            content_type="application/gzip",
            transport="urllib",
        )

    monkeypatch.setattr(bkg_rinex_nav, "fetch_reviewed_url", fake_fetch)

    cached = bkg_rinex_nav.fetch_bkg_glonass_daily(date(2026, 9, 9), tmp_path)

    assert cached.gzip_path.read_bytes() == payload
    assert cached.rinex_path.read_bytes() == _rinex_nav()
    assert cached.manifest_path.is_file()
    assert len(cached.source_sha256) == 64
    assert len(cached.rinex_sha256) == 64
    assert cached.transport == "urllib"
    assert calls == 1

    def network_must_not_be_called(url: str, *, timeout_s: float):
        raise AssertionError("valid immutable cache must be used before network access")

    monkeypatch.setattr(bkg_rinex_nav, "fetch_reviewed_url", network_must_not_be_called)
    cached_again = bkg_rinex_nav.fetch_bkg_glonass_daily(date(2026, 9, 9), tmp_path)

    assert cached_again.source_sha256 == cached.source_sha256
    assert cached_again.rinex_sha256 == cached.rinex_sha256
    assert cached_again.transport == "cache"


def test_rinex_target_epoch_must_be_timezone_aware() -> None:
    request = _request(
        source_date=date(2026, 9, 11),
        target_epoch=datetime(2026, 9, 11, 12, 0, 0),
    )
    with pytest.raises(ValueError, match="explicit UTC offset"):
        _validated_target_epoch(request)


def test_rinex_target_epoch_must_match_source_day_in_utc() -> None:
    request = _request(
        source_date=date(2026, 9, 11),
        target_epoch=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="UTC date must match source_date"):
        _validated_target_epoch(request)


def test_rinex_target_epoch_accepts_same_source_day() -> None:
    request = _request(
        source_date=date(2026, 9, 11),
        target_epoch=datetime(2026, 9, 11, 12, 0, tzinfo=UTC),
    )
    assert _validated_target_epoch(request) == datetime(2026, 9, 11, 12, 0, tzinfo=UTC)


def test_preview_exposes_source_driven_glonass_rinex_runner() -> None:
    page = render_preview_page_for_test()
    assert 'id="glonassRinexRunnerCard"' in page
    assert 'id="gloRinexAuthority"' in page
    assert "/api/glonass-rinex-runner/probe" in page
    assert "/api/glonass-rinex-runner/create" in page
    assert "RINEX NAV → GLONASS ScenarioConfig" in page
    assert "Выберите modelling authority явно" in page
    assert "syncGlonassRinexEpochToDate(true)" in page
    assert "T12:00:00Z" in page
    assert "target epoch remains tied to RINEX date" in page
    assert "if(!gloRinexEpoch.value&&normalized.epoch)" not in page
