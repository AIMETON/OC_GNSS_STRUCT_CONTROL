import shutil
from pathlib import Path

from constellation_control.preview.mission_template_policy import mission_modelling_templates


def _repo_root() -> Path:
    return Path(__file__).parents[1]


def test_packaged_policy_recommends_design_authority() -> None:
    result = mission_modelling_templates(_repo_root() / "scenarios")
    candidates = result["candidates"]
    assert candidates
    assert result["recommended"] == candidates[0]["scenario_name"]
    assert candidates[0]["force_mode"] == "design"
    assert all(item["force_mode"] in {"design", "validation"} for item in candidates)
    assert all(item["orekit_sidecar_url"] for item in candidates)
    assert all(item["spacecraft_template_id"] for item in candidates)


def test_policy_excludes_screening_even_when_it_is_a_runnable_scenario(tmp_path: Path) -> None:
    scenario_root = tmp_path / "scenarios"
    scenario_root.mkdir()
    shutil.copy2(_repo_root() / "scenarios" / "mvp_45deg.yaml", scenario_root / "screening.yaml")
    shutil.copy2(_repo_root() / "scenarios" / "orekit_design_smoke.yaml", scenario_root / "design.yaml")

    result = mission_modelling_templates(scenario_root)
    names = [item["scenario_name"] for item in result["candidates"]]
    assert names == ["design.yaml"]
    assert result["recommended"] == "design.yaml"
