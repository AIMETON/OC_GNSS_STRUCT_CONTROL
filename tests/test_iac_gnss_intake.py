from unittest.mock import patch

from fastapi.testclient import TestClient

from constellation_control.adapters.iac_gnss_tables import (
    IAC_DATA_URLS,
    IAC_URLS,
    IacDataset,
    parse_iac_html,
    parse_iac_json,
    parse_iac_text,
)
from constellation_control.preview.consolidated_release_app import create_preview_app, render_preview_page_for_test


def test_html_table_is_canonicalized_to_tsv() -> None:
    source = """<html><body><table><tr><th>PRN</th><th>e</th></tr><tr><td>1</td><td>0.01</td></tr></table></body></html>"""
    table = parse_iac_html(
        IacDataset.BEIDOU_CONSTELLATION,
        source,
        source_url=IAC_URLS[IacDataset.BEIDOU_CONSTELLATION],
    )
    assert table.headers == ("PRN", "e")
    assert table.rows == (("1", "0.01"),)
    assert table.canonical_tsv == "PRN\te\n1\t0.01\n"
    assert len(table.source_sha256) == 64


def test_glonass_live_json_contract_maps_exact_iac_fields() -> None:
    source = """[{"ns":"01","datetime":"01.09.26","Tomega":"3600.00","Tapp":"40544.00","e":"0.0","i":"64.8","Lomega":"180.0","W":"0.0","deltaT2":"0.0001","nl":"1","deltaT":"0.0","color":"#fff"}]"""
    table = parse_iac_json(IacDataset.GLONASS_ALMANAC, source, source_url=IAC_DATA_URLS[IacDataset.GLONASS_ALMANAC])
    assert table.headers == ("NS", "Дата", "TΩ", "Tоб", "e", "i", "LΩ", "ω", "δt2", "nl", "ΔT")
    assert table.rows[0] == (
        "01",
        "01.09.26",
        "3600.00",
        "40544.00",
        "0.0",
        "64.8",
        "180.0",
        "0.0",
        "0.0001",
        "1",
        "0.0",
    )


def test_gps_live_json_contract_maps_exact_iac_fields() -> None:
    source = """[{"PRN":"01","datetime":"09.08.26","t":"61440","e":"0,00186","i":"54,83668","DomegaDT":"-4,59040E-7","A":"26559,08565","Lomega":"14,57309","w":"9,86332","mm":"7,59378","af0":"2,01225E-4","af1":"-1,09139E-11","color":"#FFFFFF"}]"""
    table = parse_iac_json(IacDataset.GPS_ALMANAC, source, source_url=IAC_DATA_URLS[IacDataset.GPS_ALMANAC])
    assert table.headers == ("PRN", "Date", "t", "e", "i", "dΩ/dt", "A", "LΩ", "ω", "m", "af0", "af1")
    assert table.rows[0][0] == "01"
    assert table.rows[0][6] == "26559,08565"


def test_beidou_live_json_contract_skips_datetime_marker_and_prefixes_prn() -> None:
    source = """["26.08.26",{"ID":"01","Health":"000","Eccentricity":0.00055980682373,"Time of Applicability(s)":16384,"Orbital Inclination(rad)":0.0074242273,"Rate of Right Ascen(r/s)":1.63435379e-09,"SQRT(A)  (m 1/2)":6493.561035,"Right Ascen at Week(rad)":0.390737039,"Argument of Perigee(rad)":-3.013041993,"Mean Anom(rad)":-0.022596631,"Af0(s)":-9.536743e-07,"Af1(s/s)":0,"week":1077}]"""
    table = parse_iac_json(IacDataset.BEIDOU_ALMANAC, source, source_url=IAC_DATA_URLS[IacDataset.BEIDOU_ALMANAC])
    assert table.headers == ("PRN", "H", "e", "t", "δi", "Ω", "A", "Ω0", "ω", "m", "af0", "af1", "week")
    assert table.rows[0][0] == "C01"
    assert table.rows[0][1] == "000"
    assert table.rows[0][6] == "6493.561035"
    assert table.rows[0][-1] == "1077"


def test_json_contract_fails_closed_when_required_field_is_missing() -> None:
    source = """[{"ns":"01"}]"""
    try:
        parse_iac_json(IacDataset.GLONASS_ALMANAC, source)
    except ValueError as exc:
        assert "missing fields" in str(exc)
    else:
        raise AssertionError("missing IAC fields must fail closed")


def test_offline_text_accepts_tsv_and_semicolon_tables() -> None:
    tsv = parse_iac_text(IacDataset.GLONASS_ALMANAC, "NS\tTоб\n1\t40544\n")
    semi = parse_iac_text(IacDataset.BEIDOU_ALMANAC, "PRN;A\nC01;27800\n")
    assert tsv.rows[0] == ("1", "40544")
    assert semi.rows[0] == ("C01", "27800")


def test_preview_exposes_iac_card_and_all_qualified_data_sources() -> None:
    page = render_preview_page_for_test()
    assert 'id="iacGnssCard"' in page
    assert "/api/iac-gnss/online/" in page
    assert "beidou-constellation" in page

    client = TestClient(create_preview_app())
    response = client.get("/api/iac-gnss/sources")
    assert response.status_code == 200
    assert response.json() == {dataset.value: url for dataset, url in IAC_DATA_URLS.items()}


def test_offline_preview_preserves_source_hash_and_blocks_promotion() -> None:
    client = TestClient(create_preview_app())
    response = client.post(
        "/api/iac-gnss/offline-preview",
        json={"dataset": "gps-almanac", "filename": "gps.txt", "content_text": "PRN\te\n1\t0.01\n"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["source_filename"] == "gps.txt"
    assert payload["record_count"] == 1
    assert payload["runnable_promotion_allowed"] is False
    assert len(payload["source_sha256"]) == 64


def test_online_failure_is_fail_closed() -> None:
    client = TestClient(create_preview_app())
    with patch(
        "constellation_control.preview.iac_gnss_intake.fetch_iac_table",
        side_effect=ValueError("network unavailable"),
    ):
        response = client.get("/api/iac-gnss/online/glonass-almanac")
    assert response.status_code == 502
    assert "network unavailable" in response.json()["detail"]
