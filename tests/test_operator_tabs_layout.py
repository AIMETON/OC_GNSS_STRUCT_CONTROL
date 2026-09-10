from constellation_control.preview.gravity_release_app import (
    create_preview_app,
    render_preview_page_for_test,
)
from constellation_control.preview.mission_template_policy import MISSION_TEMPLATE_POLICY_SCRIPT
from constellation_control.preview.operator_tabs import OPERATOR_TABS_CARD, OPERATOR_TABS_SCRIPT
from constellation_control.preview.scenario_workspace import SCENARIO_VARIANT_SCRIPT


def test_top_level_workspace_follows_mission_research_flow() -> None:
    assert 'data-tab="mission"' in OPERATOR_TABS_CARD
    assert 'data-tab="scenarios"' in OPERATOR_TABS_CARD
    assert 'data-tab="experiments"' in OPERATOR_TABS_CARD
    assert 'data-tab="results"' in OPERATOR_TABS_CARD
    assert 'data-tab="expert"' in OPERATOR_TABS_CARD
    assert 'data-tab="inputs"' not in OPERATOR_TABS_CARD
    assert 'data-tab="design"' not in OPERATOR_TABS_CARD
    assert 'data-tab="robustness"' not in OPERATOR_TABS_CARD


def test_mission_workspace_exposes_all_three_aimeton_echelons() -> None:
    assert 'data-echelon="manual"' in OPERATOR_TABS_CARD
    assert 'data-echelon="assisted"' in OPERATOR_TABS_CARD
    assert 'data-echelon="auto"' in OPERATOR_TABS_CARD
    assert "setMissionEchelon('manual')" in OPERATOR_TABS_CARD
    assert "setMissionEchelon('assisted')" in OPERATOR_TABS_CARD
    assert "setMissionEchelon('auto')" in OPERATOR_TABS_CARD
    assert "localStorage.setItem('mission-echelon',mode)" in OPERATOR_TABS_SCRIPT
    assert "||'assisted'" in OPERATOR_TABS_SCRIPT


def test_global_active_configuration_exposes_selected_echelon() -> None:
    assert "id=\"activeEchelon\"" in MISSION_TEMPLATE_POLICY_SCRIPT
    assert "updateGlobalMissionEchelon" in MISSION_TEMPLATE_POLICY_SCRIPT
    assert "missionBaseSetEchelon" in MISSION_TEMPLATE_POLICY_SCRIPT
    assert "Ручной / Manual" in MISSION_TEMPLATE_POLICY_SCRIPT
    assert "Полуавтоматический / Assisted" in MISSION_TEMPLATE_POLICY_SCRIPT
    assert "Автоматический / Automatic" in MISSION_TEMPLATE_POLICY_SCRIPT


def test_scenario_workspace_has_fast_creation_and_variant_groups() -> None:
    assert 'id="operatorMissionBaseline"' in OPERATOR_TABS_CARD
    assert 'id="operatorScenarioVariants"' in OPERATOR_TABS_CARD
    assert 'id="operatorScenarioManualState"' in OPERATOR_TABS_CARD
    assert 'id="operatorScenarioSynthesis"' in OPERATOR_TABS_CARD
    assert 'id="operatorScenarioBulk"' in OPERATOR_TABS_CARD
    assert "operatorMoveCard(id,'operatorMissionBaseline')" in OPERATOR_TABS_SCRIPT
    assert "operatorMoveCard(id,'operatorScenarioVariants')" in OPERATOR_TABS_SCRIPT


def test_operator_layout_uses_semantic_card_anchors_not_position_fallbacks() -> None:
    assert "operatorAdoptCardByChild('title','scenarioSummaryCard')" in OPERATOR_TABS_SCRIPT
    assert "operatorAdoptCardByChild('fleet','constellationSummaryCard')" in OPERATOR_TABS_SCRIPT
    assert "operatorAdoptCardByChild('geometry','geometrySummaryCard')" in OPERATOR_TABS_SCRIPT
    assert "operatorAdoptCardByChild('operations','operationsSummaryCard')" in OPERATOR_TABS_SCRIPT
    assert "unassigned[0]" not in OPERATOR_TABS_SCRIPT
    assert "operatorFallbackTarget" not in OPERATOR_TABS_SCRIPT


def test_runtime_progress_is_only_in_results_workspace() -> None:
    expected = "['operationsSummaryCard','runProgressCard','runPromotionCard','resourceStateCard','driftConsistencyCard'].forEach(id=>operatorMoveCard(id,'operatorTabResults'))"
    assert expected in OPERATOR_TABS_SCRIPT
    assert "operatorMoveCard('runProgressCard','operatorMissionBaseline')" not in OPERATOR_TABS_SCRIPT
    assert "operatorMoveCard('runProgressCard','operatorTabScenarios')" not in OPERATOR_TABS_SCRIPT


def test_auto_baseline_resolves_trusted_profile_then_runs_composite() -> None:
    assert "resolveMissionModellingTemplate(preferred,system)" in MISSION_TEMPLATE_POLICY_SCRIPT
    assert "modelling-templates?system=" in MISSION_TEMPLATE_POLICY_SCRIPT
    assert "synthetic smoke profile автоматически не используется" in MISSION_TEMPLATE_POLICY_SCRIPT
    assert "if(mode==='manual')" in MISSION_TEMPLATE_POLICY_SCRIPT
    assert "if(mode==='assisted')" in MISSION_TEMPLATE_POLICY_SCRIPT
    assert "const ok=await createIgsBaseline();" in MISSION_TEMPLATE_POLICY_SCRIPT
    assert "ok?'AUTO baseline готов." in MISSION_TEMPLATE_POLICY_SCRIPT
    assert "AUTO остановлен. Подробности" in MISSION_TEMPLATE_POLICY_SCRIPT


def test_assisted_resolves_profile_but_requires_operator_confirmation() -> None:
    assisted = MISSION_TEMPLATE_POLICY_SCRIPT.split("if(mode==='assisted')", 1)[1].split(
        "missionRefreshNextStep('AUTO:", 1
    )[0]
    assert "Предложен подтверждённый modelling profile" in assisted
    assert "createIgsBaseline" not in assisted


def test_manual_does_not_invoke_automatic_profile_resolver() -> None:
    manual = MISSION_TEMPLATE_POLICY_SCRIPT.split("if(mode==='manual')", 1)[1].split(
        "let resolved;", 1
    )[0]
    assert "resolveMissionModellingTemplate" not in manual
    assert "createIgsBaseline" not in manual


def test_legacy_other_yaml_inputs_are_moved_to_expert() -> None:
    assert "other.closest('.card')" in MISSION_TEMPLATE_POLICY_SCRIPT
    assert "expert.appendChild(card)" in MISSION_TEMPLATE_POLICY_SCRIPT
    assert "otherYamlInputsCard" in MISSION_TEMPLATE_POLICY_SCRIPT


def test_preview_state_is_accessed_as_lexical_state_not_window_property() -> None:
    assert "window.current" not in OPERATOR_TABS_SCRIPT
    assert "window.scenario" not in OPERATOR_TABS_SCRIPT
    assert "window.current" not in SCENARIO_VARIANT_SCRIPT
    assert "window.scenario" not in SCENARIO_VARIANT_SCRIPT
    assert "window.current" not in MISSION_TEMPLATE_POLICY_SCRIPT
    assert "window.scenario" not in MISSION_TEMPLATE_POLICY_SCRIPT
    assert "typeof current!=='undefined'" in OPERATOR_TABS_SCRIPT
    assert "typeof current==='undefined'" in SCENARIO_VARIANT_SCRIPT


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
