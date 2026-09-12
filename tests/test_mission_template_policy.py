from pathlib import Path

import yaml

from constellation_control.application.run import load_scenario
from constellation_control.domain.digital_twin import DigitalTwinConfig, ScenarioLineage
from constellation_control.preview.mission_template_policy import mission_modelling_templates


def _repo_root() -> Path:
    return Path(__file__).parents[1]


def _write_scenario(path: Path, scenario) -> None:
    path.write_text(
        yaml.safe_dump(scenario.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _trusted_source_scenario(*, system: str, scenario_id: str):
    base = load_scenario(_repo_root() / "scenarios" / "orekit_design_smoke.yaml")
    lineage = ScenarioLineage(
        parent_scenario_id=base.scenario_id,
        parent_config_hash=base.config_hash(),
        transformation="rinex_nav_import",
        random_seed=None,
        source_type="rinex_nav",
        source_name="https://igs.example.invalid/source.rnx.gz",
        source_sha256="a" * 64,
        source_record_id=f"{system}:2:2026-09-10",
        authority="test trusted GNSS source",
    )
    return base.model_copy(
        update={
            "scenario_id": scenario_id,
            "digital_twin": DigitalTwinConfig(lineage=lineage),
        }
    )


def test_policy_does_not_promote_synthetic_smoke_automatically(tmp_path: Path) -> None:
    scenario_root = tmp_path / "scenarios"
    scenario_root.mkdir()
    base = load_scenario(_repo_root() / "scenarios" / "orekit_design_smoke.yaml")
    _write_scenario(scenario_root / "synthetic-design.yaml", base)

    result = mission_modelling_templates(scenario_root, system="GLONASS")
    assert result["recommended"] is None
    assert result["candidates"] == []


def test_policy_accepts_trusted_source_derived_profile_for_matching_system(tmp_path: Path) -> None:
    scenario_root = tmp_path / "scenarios"
    scenario_root.mkdir()
    trusted = _trusted_source_scenario(system="GLONASS", scenario_id="trusted-glo")
    _write_scenario(scenario_root / "trusted-glo.yaml", trusted)

    result = mission_modelling_templates(scenario_root, system="GLONASS")
    candidates = result["candidates"]
    assert len(candidates) == 1
    assert result["recommended"] == "trusted-glo.yaml"
    assert candidates[0]["force_mode"] == "design"
    assert candidates[0]["source_system"] == "GLONASS"
    assert candidates[0]["orekit_sidecar_url"]
    assert candidates[0]["spacecraft_template_id"]


def test_policy_never_reuses_other_gnss_spacecraft_authority(tmp_path: Path) -> None:
    scenario_root = tmp_path / "scenarios"
    scenario_root.mkdir()
    trusted = _trusted_source_scenario(system="GLONASS", scenario_id="trusted-glo")
    _write_scenario(scenario_root / "trusted-glo.yaml", trusted)

    gps = mission_modelling_templates(scenario_root, system="GPS")
    assert gps["recommended"] is None
    assert gps["candidates"] == []


def test_trust_follows_immutable_parent_lineage(tmp_path: Path) -> None:
    scenario_root = tmp_path / "scenarios"
    scenario_root.mkdir()
    parent = _trusted_source_scenario(system="GLONASS", scenario_id="trusted-parent")
    _write_scenario(scenario_root / "trusted-parent.yaml", parent)

    child_lineage = ScenarioLineage(
        parent_scenario_id=parent.scenario_id,
        parent_config_hash=parent.config_hash(),
        transformation="operator_scenario_variant",
        random_seed=None,
    )
    child = parent.model_copy(
        update={
            "scenario_id": "trusted-child",
            "digital_twin": (parent.digital_twin or DigitalTwinConfig()).model_copy(
                update={"lineage": child_lineage}
            ),
        }
    )
    _write_scenario(scenario_root / "trusted-child.yaml", child)

    result = mission_modelling_templates(scenario_root, system="GLONASS")
    names = [item["scenario_name"] for item in result["candidates"]]
    assert "trusted-parent.yaml" in names
    assert "trusted-child.yaml" in names
