from constellation_control.preview.gravity_release_app import render_preview_page_for_test


def test_operator_tabs_have_runtime_error_surface_and_safe_switching() -> None:
    page = render_preview_page_for_test()
    assert 'id="operatorRuntimeStatus"' in page
    assert "function operatorRuntimeError" in page
    assert "window.addEventListener('error'" in page
    assert "window.addEventListener('unhandledrejection'" in page
    assert "function showOperatorTab(name)" in page
    assert "unknown operator tab" in page
    for name in ("mission", "scenarios", "experiments", "results", "expert"):
        assert f'data-tab="{name}"' in page
        assert f'data-tab-pane="{name}"' in page


def test_mission_never_silently_promotes_active_scenario_to_modelling_authority() -> None:
    page = render_preview_page_for_test()
    script = page.split("async function missionPrepareBaseline(){", 1)[1].split(
        "function installMissionWorkspace(){", 1
    )[0]
    assert "igsTemplateScenario.value=selected" not in script
    assert "modelling authority не выбрана явно" in script
    assert "const authority=" in script


def test_iac_full_constellation_chain_is_explicit_and_observable() -> None:
    page = render_preview_page_for_test()
    assert 'id="iacGloConstAuthority"' in page
    assert 'id="iacGloConstBuildButton"' in page
    assert "modelling_authority_scenario_name:authority" in page
    assert "Сценарий создан, но список сценариев не удалось обновить" in page
    assert "scenario.value=d.scenario_name;await loadScenario()" in page
    assert "finally{" in page
