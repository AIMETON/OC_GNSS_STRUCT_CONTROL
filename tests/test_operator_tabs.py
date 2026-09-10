from __future__ import annotations

from constellation_control.preview.gravity_release_app import render_preview_page_for_test


def test_packaged_operator_surface_has_tabs_and_active_run_identity() -> None:
    page = render_preview_page_for_test()

    assert 'id="activeRunConfigurationCard"' in page
    assert 'id="activeScenario"' in page
    assert 'id="activeDesignScreening"' in page
    assert 'id="activeDesignValidation"' in page
    assert 'id="activeDesignConfig"' in page
    assert 'id="activeRobustnessValidation"' in page
    assert 'id="activeRobustnessConfig"' in page
    assert 'id="activeFingerprint"' in page

    for tab in ("mission", "scenarios", "experiments", "results", "expert"):
        assert f'data-tab="{tab}"' in page
        assert f'data-tab-pane="{tab}"' in page

    for retired_tab in ("inputs", "design", "robustness"):
        assert f'data-tab="{retired_tab}"' not in page

    assert "splitWorkflowCard()" in page
