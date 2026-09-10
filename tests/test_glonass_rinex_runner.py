from __future__ import annotations

import gzip
from datetime import date
from pathlib import Path

from constellation_control.adapters import bkg_rinex_nav
from constellation_control.adapters.reviewed_http_fetch import ReviewedHttpResponse
from constellation_control.preview.gravity_release_app import render_preview_page_for_test


def _rinex_nav() -> bytes:
    line1 = "     3.05           N: GNSS NAV DATA    R: GLONASS          RINEX VERSION / TYPE\n"
    end = "                                                            END OF HEADER\n"
    return (line1 + end + "R01 2026 09 09 00 00 00 0.0 0.0 0.0\n").encode("ascii")


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


def test_preview_exposes_glonass_rinex_runner() -> None:
    page = render_preview_page_for_test()
    assert 'id="glonassRinexRunnerCard"' in page
    assert "/api/glonass-rinex-runner/create" in page
    assert "IGS/BKG RINEX NAV" in page
