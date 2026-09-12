from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from constellation_control.preview.source_settings import (
    DEFAULT_SOURCE_SETTINGS,
    install_source_settings_routes,
    load_source_settings,
    resolve_source_url,
    save_source_settings,
    selected_source_sequence,
)
from constellation_control.preview.source_settings_tab import SOURCE_SETTINGS_PANE, SOURCE_SETTINGS_TAB_SCRIPT


def test_settings_tab_exposes_urls_and_request_templates() -> None:
    assert 'data-tab-pane="settings"' in SOURCE_SETTINGS_PANE
    assert 'Настройки / Settings' in SOURCE_SETTINGS_PANE
    assert 'Request template' in SOURCE_SETTINGS_TAB_SCRIPT
    assert "installSourceSettingsTab" in SOURCE_SETTINGS_TAB_SCRIPT
    assert "/api/settings/sources" in SOURCE_SETTINGS_TAB_SCRIPT


def test_settings_persist_and_resolve_template(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "source_endpoints.json"
    monkeypatch.setenv("OC_GNSS_SOURCE_SETTINGS", str(path))
    document = DEFAULT_SOURCE_SETTINGS.model_copy(deep=True)
    navcen = next(item for item in document.sources if item.source_id == "navcen_gps_yuma")
    navcen.base_url = "https://mirror.example.test"
    navcen.request_template = "{base_url}/gps/{prn}.alm"
    save_source_settings(document)
    loaded = load_source_settings()
    assert next(item for item in loaded.sources if item.source_id == "navcen_gps_yuma").base_url == "https://mirror.example.test"
    assert resolve_source_url("navcen_gps_yuma", "https://default.invalid", prn=7) == "https://mirror.example.test/gps/7.alm"


def test_disabled_source_does_not_fall_back_hidden(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OC_GNSS_SOURCE_SETTINGS", str(tmp_path / "source_endpoints.json"))
    document = DEFAULT_SOURCE_SETTINGS.model_copy(deep=True)
    navcen = next(item for item in document.sources if item.source_id == "navcen_gps_yuma")
    navcen.enabled = False
    save_source_settings(document)
    with pytest.raises(ValueError, match="source navcen_gps_yuma is disabled"):
        resolve_source_url("navcen_gps_yuma", "https://hidden-fallback.invalid")


def test_iac_ftp_archive_default_is_operator_configurable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OC_GNSS_SOURCE_SETTINGS", str(tmp_path / "source_endpoints.json"))
    archive = next(item for item in DEFAULT_SOURCE_SETTINGS.sources if item.source_id == "iac_ftp_archive")
    assert archive.base_url == "ftp://ftp.glonass-iac.ru"
    assert archive.request_template == "{base_url}/MCC/BRDC/{year}/"
    assert "archive_discovery" in archive.capabilities
    assert "broadcast_rinex_nav" in archive.capabilities
    assert resolve_source_url("iac_ftp_archive", "ftp://invalid", year=2026) == "ftp://ftp.glonass-iac.ru/MCC/BRDC/2026/"


def test_whu_default_points_to_daily_root_not_nonexistent_p_directory() -> None:
    whu = next(item for item in DEFAULT_SOURCE_SETTINGS.sources if item.source_id == "igs_whu")
    assert whu.request_template == "{base_url}/{year}/{doy}/"
    assert "YYm/YYg/YYn" in whu.notes


def test_fcnd_is_explicit_russian_gnss_source() -> None:
    fcnd = next(item for item in DEFAULT_SOURCE_SETTINGS.sources if item.source_id == "fcnd_api")
    assert fcnd.base_url == "https://fcnd.ru"
    assert set(fcnd.systems) == {"GLONASS", "GPS"}
    assert "gnss_data_api" in fcnd.capabilities
    assert "broadcast_rinex_nav" in fcnd.capabilities


def test_iac_live_gps_and_beidou_sources_are_registered() -> None:
    by_id = {item.source_id: item for item in DEFAULT_SOURCE_SETTINGS.sources}
    assert by_id["iac_gps"].request_template.endswith("/gps/ephemeris/ephemeris_json.php")
    assert "gps_almanac_table" in by_id["iac_gps"].capabilities
    assert by_id["iac_beidou_almanac"].request_template.endswith("/beidou/ephemeris/beidou_almanac_calc.php")
    assert "beidou_almanac_table" in by_id["iac_beidou_almanac"].capabilities
    assert by_id["iac_beidou_constellation"].request_template.endswith("/beidou/sostavOG/")


def test_auto_source_selection_preserves_operator_order(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OC_GNSS_SOURCE_SETTINGS", str(tmp_path / "source_endpoints.json"))
    document = DEFAULT_SOURCE_SETTINGS.model_copy(deep=True)
    document.selection["GLONASS"].mode = "auto"
    document.selection["GLONASS"].auto_order = ["iac_ftp_archive", "fcnd_api", "igs_whu", "igs_bkg"]
    save_source_settings(document)
    assert [item.source_id for item in selected_source_sequence("GLONASS", capability="broadcast_rinex_nav")] == [
        "iac_ftp_archive",
        "fcnd_api",
        "igs_whu",
        "igs_bkg",
    ]


def test_v2_settings_migrate_sources_without_losing_operator_values(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "source_endpoints.json"
    monkeypatch.setenv("OC_GNSS_SOURCE_SETTINGS", str(path))
    old = DEFAULT_SOURCE_SETTINGS.model_copy(deep=True)
    old.version = 2
    old.sources = [
        item
        for item in old.sources
        if item.source_id not in {"fcnd_api", "iac_gps", "iac_beidou_almanac", "iac_beidou_constellation"}
    ]
    bkg = next(item for item in old.sources if item.source_id == "igs_bkg")
    bkg.base_url = "https://operator.example.test/brdc"
    old.selection["GLONASS"].auto_order = ["iac_glonass", "iac_ftp_archive", "igs_bkg", "igs_whu"]
    old.selection["GPS"].mode = "manual"
    old.selection["GPS"].selected_source_id = "navcen_gps_sem"
    path.write_text(old.model_dump_json(indent=2), encoding="utf-8")

    upgraded = load_source_settings()
    assert upgraded.version == 4
    assert any(item.source_id == "fcnd_api" for item in upgraded.sources)
    assert any(item.source_id == "iac_gps" for item in upgraded.sources)
    assert next(item for item in upgraded.sources if item.source_id == "igs_bkg").base_url == "https://operator.example.test/brdc"
    assert upgraded.selection["GLONASS"].auto_order == [
        "iac_glonass",
        "iac_ftp_archive",
        "fcnd_api",
        "igs_bkg",
        "igs_whu",
    ]
    assert upgraded.selection["GPS"].mode == "manual"
    assert upgraded.selection["GPS"].selected_source_id == "navcen_gps_sem"


def test_v3_shipped_source_paths_migrate_but_custom_paths_survive(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "source_endpoints.json"
    monkeypatch.setenv("OC_GNSS_SOURCE_SETTINGS", str(path))
    old = DEFAULT_SOURCE_SETTINGS.model_copy(deep=True)
    old.version = 3
    whu = next(item for item in old.sources if item.source_id == "igs_whu")
    whu.request_template = "{base_url}/{year}/{doy}/{yy}p/"
    iac = next(item for item in old.sources if item.source_id == "iac_ftp_archive")
    iac.request_template = "{base_url}/{directory}/"
    navcen = next(item for item in old.sources if item.source_id == "navcen_gps_yuma")
    navcen.base_url = "https://operator.example.test"
    path.write_text(old.model_dump_json(indent=2), encoding="utf-8")

    upgraded = load_source_settings()
    assert upgraded.version == 4
    assert next(item for item in upgraded.sources if item.source_id == "igs_whu").request_template == "{base_url}/{year}/{doy}/"
    assert next(item for item in upgraded.sources if item.source_id == "iac_ftp_archive").request_template == "{base_url}/MCC/BRDC/{year}/"
    assert next(item for item in upgraded.sources if item.source_id == "navcen_gps_yuma").base_url == "https://operator.example.test"


def test_manual_source_selection_is_fail_closed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OC_GNSS_SOURCE_SETTINGS", str(tmp_path / "source_endpoints.json"))
    document = DEFAULT_SOURCE_SETTINGS.model_copy(deep=True)
    document.selection["GLONASS"].mode = "manual"
    document.selection["GLONASS"].selected_source_id = "iac_glonass"
    save_source_settings(document)
    assert [item.source_id for item in selected_source_sequence("GLONASS")] == ["iac_glonass"]
    with pytest.raises(ValueError, match="selected source is unavailable"):
        selected_source_sequence("GLONASS", capability="broadcast_rinex_nav")


def test_settings_api_round_trip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OC_GNSS_SOURCE_SETTINGS", str(tmp_path / "source_endpoints.json"))
    app = FastAPI()
    install_source_settings_routes(app)
    client = TestClient(app)
    initial = client.get("/api/settings/sources")
    assert initial.status_code == 200
    payload = initial.json()
    assert payload["version"] == 4
    assert len(payload["sources"]) == 12
    assert any(item["source_id"] == "igs_whu" for item in payload["sources"])
    assert any(item["source_id"] == "iac_ftp_archive" for item in payload["sources"])
    assert any(item["source_id"] == "fcnd_api" for item in payload["sources"])
    assert any(item["source_id"] == "iac_gps" for item in payload["sources"])
    assert payload["selection"]["GLONASS"]["mode"] == "auto"
    payload["sources"][0]["base_url"] = "https://example.test/nav"
    payload["selection"]["GLONASS"]["mode"] = "manual"
    payload["selection"]["GLONASS"]["selected_source_id"] = "igs_whu"
    saved = client.put("/api/settings/sources", json=payload)
    assert saved.status_code == 200
    current = client.get("/api/settings/sources").json()
    assert current["sources"][0]["base_url"] == "https://example.test/nav"
    assert current["selection"]["GLONASS"]["selected_source_id"] == "igs_whu"


def test_saved_settings_file_is_valid_json(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "source_endpoints.json"
    monkeypatch.setenv("OC_GNSS_SOURCE_SETTINGS", str(path))
    save_source_settings(DEFAULT_SOURCE_SETTINGS.model_copy(deep=True))
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["version"] == 4
    assert len(payload["sources"]) == 12
