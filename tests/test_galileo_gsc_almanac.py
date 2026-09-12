from datetime import date
from math import isclose, pi, radians, sqrt
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from constellation_control.adapters.galileo_gsc_almanac import (
    GALILEO_NOMINAL_SEMI_MAJOR_AXIS_M,
    GSC_ALMANAC_INDEX_URL,
    GSC_DAILY_FILE_PREFIX,
    discover_latest_gsc_almanac_url,
    fetch_latest_galileo_gsc_almanac,
    gsc_daily_almanac_url,
    parse_galileo_gsc_almanac,
)
from constellation_control.adapters.reviewed_http_fetch import ReviewedHttpResponse
from constellation_control.preview.consolidated_release_app import create_preview_app, render_preview_page_for_test


_SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<gal:Almanac xmlns:gal="urn:galileo:test">
  <gal:SV>
    <gal:SVID>11</gal:SVID>
    <gal:aSqRoot>12.5</gal:aSqRoot>
    <gal:ecc>0.00125</gal:ecc>
    <gal:deltai>0.0005</gal:deltai>
    <gal:omega0>-0.25</gal:omega0>
    <gal:omegaDot>-1.25E-9</gal:omegaDot>
    <gal:w>0.125</gal:w>
    <gal:m0>-0.375</gal:m0>
    <gal:af0>2.0E-4</gal:af0>
    <gal:af1>-1.0E-11</gal:af1>
    <gal:iod>7</gal:iod>
    <gal:t0a>86400</gal:t0a>
    <gal:wna>2</gal:wna>
    <gal:statusE5a>0</gal:statusE5a>
    <gal:statusE5b>1</gal:statusE5b>
    <gal:statusE1B>0</gal:statusE1B>
  </gal:SV>
</gal:Almanac>
"""

_NESTED_SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<gal:Almanac xmlns:gal="urn:galileo:test">
  <gal:SV>
    <gal:SVID>11</gal:SVID>
    <gal:orbital>
      <gal:aSqRoot>12.5</gal:aSqRoot>
      <gal:ecc>0.00125</gal:ecc>
      <gal:deltai>0.0005</gal:deltai>
      <gal:omega0>-0.25</gal:omega0>
      <gal:omegaDot>-1.25E-9</gal:omegaDot>
      <gal:w>0.125</gal:w>
      <gal:m0>-0.375</gal:m0>
    </gal:orbital>
    <gal:clock>
      <gal:af0>2.0E-4</gal:af0>
      <gal:af1>-1.0E-11</gal:af1>
      <gal:iod>7</gal:iod>
      <gal:t0a>86400</gal:t0a>
      <gal:wna>2</gal:wna>
    </gal:clock>
    <gal:status>
      <gal:statusE5a>0</gal:statusE5a>
      <gal:statusE5b>1</gal:statusE5b>
      <gal:statusE1B>0</gal:statusE1B>
    </gal:status>
  </gal:SV>
</gal:Almanac>
"""


def test_discovers_latest_public_gsc_xml_from_allowlisted_index() -> None:
    html = """
    <a href="https://evil.example/x.xml">bad</a>
    <a href="/sites/default/files/sites/all/files/2026-08-25.xml">old</a>
    <a href="/sites/default/files/sites/all/files/2026-08-28.xml">current</a>
    <a href="/sites/default/files/sites/all/files/GalileoGSCAlmanac_20260829120000_dailyproductabc.xml">daily</a>
    """
    assert discover_latest_gsc_almanac_url(html).endswith("GalileoGSCAlmanac_20260829120000_dailyproductabc.xml")


def test_direct_daily_url_matches_field_observed_gsc_path() -> None:
    assert gsc_daily_almanac_url(date(2026, 9, 8)) == GSC_DAILY_FILE_PREFIX + "2026-09-08.xml"


def test_gsc_xml_units_are_converted_explicitly() -> None:
    almanac = parse_galileo_gsc_almanac("2026-08-28.xml", _SAMPLE_XML)
    record = almanac.records[0]
    assert record.svid == 11
    expected_sqrt_a = sqrt(GALILEO_NOMINAL_SEMI_MAJOR_AXIS_M) + 12.5
    assert isclose(record.sqrt_a_m_sqrt, expected_sqrt_a)
    assert isclose(record.semi_major_axis_m, expected_sqrt_a**2)
    assert isclose(record.inclination_rad, radians(56.0) + 0.0005 * pi)
    assert isclose(record.raan_rad, -0.25 * pi)
    assert isclose(record.raan_rate_rad_s, -1.25e-9 * pi)
    assert isclose(record.argument_of_perigee_rad, 0.125 * pi)
    assert isclose(record.mean_anomaly_rad, -0.375 * pi)
    assert record.wna_mod4 == 2
    assert record.status_e1b == 0
    assert len(almanac.source_sha256) == 64


def test_gsc_nested_record_structure_is_parsed() -> None:
    almanac = parse_galileo_gsc_almanac("2026-09-08.xml", _NESTED_SAMPLE_XML)
    assert len(almanac.records) == 1
    record = almanac.records[0]
    assert record.svid == 11
    assert record.delta_sqrt_a_m_sqrt == 12.5
    assert record.status_e5a == 0
    assert record.status_e5b == 1
    assert record.status_e1b == 0


def test_gsc_xml_missing_required_field_fails_closed() -> None:
    bad = _SAMPLE_XML.replace("<gal:af1>-1.0E-11</gal:af1>", "")
    with pytest.raises(ValueError, match="missing fields: af1"):
        parse_galileo_gsc_almanac("bad.xml", bad)


def test_gsc_wna_is_not_silently_expanded_from_modulo_4() -> None:
    almanac = parse_galileo_gsc_almanac("x.xml", _SAMPLE_XML)
    assert almanac.records[0].wna_mod4 == 2
    assert not hasattr(almanac.records[0], "galileo_week")


def test_latest_fetch_tries_direct_daily_xml_before_index(monkeypatch) -> None:
    calls: list[str] = []

    def fake_fetch(url: str, timeout_s: float) -> ReviewedHttpResponse:
        calls.append(url)
        if url.endswith("2026-09-10.xml") or url.endswith("2026-09-09.xml"):
            raise OSError("not published")
        if url.endswith("2026-09-08.xml"):
            return ReviewedHttpResponse(raw=_NESTED_SAMPLE_XML.encode(), content_type="application/xml", transport="urllib")
        raise AssertionError(f"index must not be used after direct daily success: {url}")

    monkeypatch.setattr("constellation_control.adapters.galileo_gsc_almanac.fetch_reviewed_url", fake_fetch)
    almanac = fetch_latest_galileo_gsc_almanac(as_of=date(2026, 9, 10), direct_lookback_days=5)
    assert almanac.source_url is not None and almanac.source_url.endswith("2026-09-08.xml")
    assert len(almanac.records) == 1
    assert calls == [
        GSC_DAILY_FILE_PREFIX + "2026-09-10.xml",
        GSC_DAILY_FILE_PREFIX + "2026-09-09.xml",
        GSC_DAILY_FILE_PREFIX + "2026-09-08.xml",
    ]


def test_preview_exposes_galileo_gsc_online_and_offline() -> None:
    page = render_preview_page_for_test()
    assert "Galileo — официальный GSC Almanac" in page
    assert "/api/galileo-gsc/online" in page

    client = TestClient(create_preview_app())
    source = client.get("/api/galileo-gsc/source")
    assert source.status_code == 200
    assert source.json()["index_url"] == GSC_ALMANAC_INDEX_URL

    offline = client.post(
        "/api/galileo-gsc/offline-preview",
        json={"filename": "galileo.xml", "content_text": _NESTED_SAMPLE_XML},
    )
    assert offline.status_code == 200
    payload = offline.json()
    assert payload["record_count"] == 1
    assert payload["records"][0]["svid"] == 11
    assert payload["runnable_promotion_allowed"] is False


def test_gsc_online_failure_is_fail_closed() -> None:
    client = TestClient(create_preview_app())
    with patch(
        "constellation_control.preview.galileo_gsc_input.fetch_latest_galileo_gsc_almanac",
        side_effect=ValueError("GSC unavailable"),
    ):
        response = client.get("/api/galileo-gsc/online")
    assert response.status_code == 502
    assert "GSC unavailable" in response.json()["detail"]
