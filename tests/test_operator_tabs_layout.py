from constellation_control.preview.gravity_release_app import (
    create_preview_app,
    render_preview_page_for_test,
)
from constellation_control.preview.operator_tabs import OPERATOR_TABS_CARD, OPERATOR_TABS_SCRIPT


def test_inputs_workspace_has_explicit_engineering_groups() -> None:
    assert 'id="operatorInputOfficialSources"' in OPERATOR_TABS_CARD
    assert 'id="operatorInputManualState"' in OPERATOR_TABS_CARD
    assert 'id="operatorInputSynthesis"' in OPERATOR_TABS_CARD
    assert 'id="operatorInputBulk"' in OPERATOR_TABS_CARD


def test_operator_layout_uses_semantic_card_anchors_not_position_or_text_regex() -> None:
    assert "operatorAdoptCardByChild('title','scenarioSummaryCard')" in OPERATOR_TABS_SCRIPT
    assert "operatorAdoptCardByChild('fleet','constellationSummaryCard')" in OPERATOR_TABS_SCRIPT
    assert "operatorAdoptCardByChild('geometry','geometrySummaryCard')" in OPERATOR_TABS_SCRIPT
    assert "operatorAdoptCardByChild('operations','operationsSummaryCard')" in OPERATOR_TABS_SCRIPT
    assert "unassigned[0]" not in OPERATOR_TABS_SCRIPT
    assert "operatorFallbackTarget" not in OPERATOR_TABS_SCRIPT


def test_runtime_progress_is_never_part_of_input_workspace() -> None:
    expected = "['operationsSummaryCard','runProgressCard','runPromotionCard','resourceStateCard','driftConsistencyCard'].forEach(id=>operatorMoveCard(id,'operatorTabResults'))"
    assert expected in OPERATOR_TABS_SCRIPT
    assert "operatorMoveCard('runProgressCard','operatorTabInputs')" not in OPERATOR_TABS_SCRIPT


def test_galileo_gsc_card_is_rendered_exactly_once_and_routed_to_expert() -> None:
    page = render_preview_page_for_test()
    assert page.count('id="galileoGscCard"') == 1
    assert "'galileoGscCard'" in OPERATOR_TABS_SCRIPT
    assert "operatorTabExpert" in OPERATOR_TABS_SCRIPT


def test_galileo_gsc_routes_are_registered_once() -> None:
    app = create_preview_app()
    paths = [getattr(route, "path", "") for route in app.router.routes]
    gsc_paths = [path for path in paths if path.startswith("/api/galileo-gsc/")]
    assert gsc_paths
    assert len(gsc_paths) == len(set(gsc_paths))
