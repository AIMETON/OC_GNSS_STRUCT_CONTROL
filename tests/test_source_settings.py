from __future__ import annotations

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


def test_iac_ftp_archive_default_is_operator_configurable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OC_GNSS_SOURCE_SETTINGS", str(tmp_path / "source_endpoints.json"))
    archive = next(item for item in DEFAULT_SOURCE_SETTINGS.sources if item.source_id == "iac_ftp_archive")
    assert archive.base_url == "ftp://ftp.glonass-iac.ru"
    assert "{directory}" in archive.request_template
    assert "anonymous" in archive.notes.lower()
    assert resolve_source_url("iac_ftp_archive", "ftp://invalid", directory="MCC") == "ftp://ftp.glonass-iac.ru/MCC/"


def test_auto_source_selection_preserves_operator_order(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OC_GNSS_SOURCE_SETTINGS", str(tmp_path / "source_endpoints.json"))
    document = DEFAULT_SOURCE_SETTINGS.model_copy(deep=True)
    document.selection["GLONASS"].mode = "auto"
    document.selection["GLONASS"].auto_order = ["iac_ftp_archive", "igs_whu", "igs_bkg"]
    save_source_settings(document)
    assert [item.source_id for item in selected_source_sequence("GLONASS")] == [
        "iac_ftp_archive",
        "igs_whu",
        "igs_bkg",
        "iac_glonass",
    ]
    assert [item.source_id for item in selected_source_sequence("GLONASS", capability="broadcast_rinex_nav")] == [
        "igs_whu",
        "igs_bkg",
    ]


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
    assert any(item["source_id"] == "igs_whu" for item in payload["sources"])
    assert any(item["source_id"] == "iac_ftp_archive" for item in payload["sources"])
    assert payload["selection"]["GLONASS"]["mode"] == "auto"
    payload["sources"][0]["base_url"] = "https://example.test/nav"
    payload["selection"]["GLONASS"]["mode"] = "manual"
    payload["selection"]["GLONASS"]["selected_source_id"] = "igs_whu"
    saved = client.put("/api/settings/sources", json=payload)
    assert saved.status_code == 200
    current = client.get("/api/settings/sources").json()
    assert current["sources"][0]["base_url"] == "https://example.test/nav"
    assert current["selection"]["GLONASS"]["selected_source_id"] == "igs_whu"
