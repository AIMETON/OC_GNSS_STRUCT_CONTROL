from datetime import date

from constellation_control.adapters.bkg_rinex_nav import bkg_gnss_daily_url
from constellation_control.preview.gravity_release_app import render_preview_page_for_test


def test_bkg_igs_system_suffixes() -> None:
    day = date(2026, 9, 10)
    assert bkg_gnss_daily_url(day, "GLONASS").endswith("_RN.rnx.gz")
    assert bkg_gnss_daily_url(day, "GPS").endswith("_GN.rnx.gz")
    assert bkg_gnss_daily_url(day, "Galileo").endswith("_EN.rnx.gz")
    assert bkg_gnss_daily_url(day, "BeiDou").endswith("_CN.rnx.gz")


def test_operator_inputs_show_one_primary_igs_card() -> None:
    page = render_preview_page_for_test()
    assert 'id="igsConstellationCard"' in page
    assert "/api/igs-constellation/create" in page
    assert "ГЛОНАСС" in page
    assert ">GPS<" in page
    assert ">Galileo<" in page
    assert "BeiDou / Compass" in page
    assert "operatorInputOfficialSources" in page
    assert "operatorTabExpert" in page


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
