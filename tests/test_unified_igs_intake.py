from datetime import date
from pathlib import Path

from constellation_control.adapters.bkg_rinex_nav import CachedRinexNav, bkg_gnss_daily_url
from constellation_control.preview import igs_constellation_input
from constellation_control.preview.gravity_release_app import render_preview_page_for_test


def test_bkg_igs_system_suffixes() -> None:
    day = date(2026, 9, 10)
    assert bkg_gnss_daily_url(day, "GLONASS").endswith("_RN.rnx.gz")
    assert bkg_gnss_daily_url(day, "GPS").endswith("_GN.rnx.gz")
    assert bkg_gnss_daily_url(day, "Galileo").endswith("_EN.rnx.gz")
    assert bkg_gnss_daily_url(day, "BeiDou").endswith("_CN.rnx.gz")


def test_operator_inputs_show_two_stage_igs_workflow() -> None:
    page = render_preview_page_for_test()
    assert 'id="igsConstellationCard"' in page
    assert "/api/igs-constellation/fetch" in page
    assert "/api/igs-constellation/create" in page
    assert "1. Скачать IGS RINEX" in page
    assert "2. Сформировать сценарий" in page
    assert "Базовая модель сценария — выбрать явно" in page
    assert "ГЛОНАСС" in page
    assert ">GPS<" in page
    assert ">Galileo<" in page
    assert "BeiDou / Compass" in page
    assert "operatorInputOfficialSources" in page
    assert "operatorTabExpert" in page


def test_igs_fetch_payload_has_no_active_scenario_dependency() -> None:
    page = render_preview_page_for_test()
    fetch_script = page.split("async function fetchIgsConstellationData(){", 1)[1].split(
        "async function buildIgsConstellation(){", 1
    )[0]
    assert "const p={source_date:date,system:igsSystem.value};" in fetch_script
    assert "scenario.value" not in fetch_script
    assert "template_scenario_name:template" in page


def test_igs_fetch_request_requires_no_scenario_or_orekit(tmp_path: Path, monkeypatch) -> None:
    source_date = date(2026, 8, 10)
    gzip_path = tmp_path / "source.rnx.gz"
    rinex_path = tmp_path / "source.rnx"
    manifest_path = tmp_path / "source.rnx.gz.manifest.json"
    fake = CachedRinexNav(
        source_url="https://igs.example.invalid/source.rnx.gz",
        source_date=source_date,
        source_filename="source.rnx.gz",
        source_sha256="a" * 64,
        rinex_sha256="b" * 64,
        gzip_path=gzip_path,
        rinex_path=rinex_path,
        manifest_path=manifest_path,
        transport="cache",
    )

    def fake_fetch(day: date, system: str, cache_root: Path) -> CachedRinexNav:
        assert day == source_date
        assert system == "GLONASS"
        return fake

    monkeypatch.setattr(igs_constellation_input, "fetch_bkg_gnss_daily", fake_fetch)
    request = igs_constellation_input.IgsDataFetchRequest(
        source_date=source_date,
        system="GLONASS",
    )
    result = igs_constellation_input.fetch_igs_constellation_data(tmp_path / "scenarios", request)

    assert "source_scenario_name" not in request.model_fields
    assert result["requires_scenario"] is False
    assert result["requires_orekit"] is False
    assert result["transport"] == "cache"


def test_low_level_source_cards_are_routed_to_expert() -> None:
    page = render_preview_page_for_test()
    assert "glonassRinexRunnerCard" in page
    assert "iacGlonassRunnerCard" in page
    assert "navcenGpsRunnerCard" in page
    assert "mixedGnssRunnerCard" in page


def test_operator_tab_routing_contract_is_role_based() -> None:
    page = render_preview_page_for_test()
    # Scenarios: scenario overview/composition only.
    assert "constellationEditorCard" in page
    # Inputs: primary IGS plus explicit/manual/synthesis/bulk inputs.
    assert "igsConstellationCard" in page
    assert "osculatingCard" in page
    assert "walkerCard" in page
    assert "workbookImportCard" in page
    # Design.
    assert "gravityModelCard" in page
    assert "closedLoopCard" in page
    assert "designWorkflowCard" in page or "workflowCard" in page
    # Robustness.
    assert "perturbationCard" in page
    # Results.
    assert "runProgressCard" in page
    assert "runPromotionCard" in page
    assert "resourceStateCard" in page
    assert "driftConsistencyCard" in page
    # Expert-only low-level and YAML tools remain available.
    assert "scenarioEditorCard" in page
    assert "galileoGscCard" in page
    assert "iacGnssCard" in page
    assert "gnssAlmanacCard" in page
    assert "noradCard" in page
