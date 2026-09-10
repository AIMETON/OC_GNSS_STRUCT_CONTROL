import shutil
from pathlib import Path

from constellation_control.application.run import load_scenario
from constellation_control.preview.scenario_workspace import (
    ScenarioVariantRequest,
    create_scenario_variant,
)


def _repo_root() -> Path:
    return Path(__file__).parents[1]


def test_quick_variant_preserves_parent_and_records_lineage(tmp_path: Path) -> None:
    scenario_root = tmp_path / "scenarios"
    scenario_root.mkdir()
    shutil.copy2(_repo_root() / "scenarios" / "mvp_45deg.yaml", scenario_root / "source.yaml")
    parent = load_scenario(scenario_root / "source.yaml")

    result = create_scenario_variant(
        scenario_root,
        ScenarioVariantRequest(
            source_scenario_name="source.yaml",
            target_scenario_name="source-variant.yaml",
            new_scenario_id="source-variant",
            duration_s=86400.0,
            output_step_s=900.0,
        ),
    )

    assert result["scenario_name"] == "source-variant.yaml"
    assert (scenario_root / "source.yaml").is_file()
    child = load_scenario(scenario_root / "source-variant.yaml")
    assert child.scenario_id == "source-variant"
    assert child.duration_s == 86400.0
    assert child.output_step_s == 900.0
    assert child.digital_twin is not None
    assert child.digital_twin.lineage is not None
    assert child.digital_twin.lineage.parent_scenario_id == parent.scenario_id
    assert child.digital_twin.lineage.parent_config_hash == parent.config_hash()
    assert child.digital_twin.lineage.transformation == "operator_scenario_variant"


def test_quick_variant_refuses_parent_id_reuse(tmp_path: Path) -> None:
    scenario_root = tmp_path / "scenarios"
    scenario_root.mkdir()
    shutil.copy2(_repo_root() / "scenarios" / "mvp_45deg.yaml", scenario_root / "source.yaml")
    parent = load_scenario(scenario_root / "source.yaml")

    request = ScenarioVariantRequest(
        source_scenario_name="source.yaml",
        target_scenario_name="bad.yaml",
        new_scenario_id=parent.scenario_id,
        duration_s=86400.0,
        output_step_s=900.0,
    )
    try:
        create_scenario_variant(scenario_root, request)
    except ValueError as exc:
        assert "must differ" in str(exc)
    else:
        raise AssertionError("parent scenario_id reuse must be rejected")
