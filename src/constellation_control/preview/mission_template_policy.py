from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from constellation_control.application.run import load_scenario
from constellation_control.domain.models import ForceMode
from constellation_control.preview.base_preview_shell import preview_catalog


def mission_modelling_templates(scenario_root: Path) -> dict[str, object]:
    root = scenario_root.resolve()
    candidates: list[dict[str, object]] = []
    for name in preview_catalog(root).get("scenarios", []):
        if not isinstance(name, str):
            continue
        try:
            scenario = load_scenario(root / name)
        except (ValueError, TypeError, OSError):
            continue
        if not scenario.orekit_sidecar_url or not scenario.constellation.satellites:
            continue
        if scenario.force_model.mode not in {ForceMode.DESIGN, ForceMode.VALIDATION}:
            continue
        mode_rank = 0 if scenario.force_model.mode == ForceMode.DESIGN else 1
        candidates.append(
            {
                "scenario_name": name,
                "scenario_id": scenario.scenario_id,
                "force_mode": scenario.force_model.mode.value,
                "gravity_model": scenario.force_model.gravity_model,
                "gravity_degree": scenario.force_model.gravity_degree,
                "gravity_order": scenario.force_model.gravity_order,
                "frame": scenario.frame.value,
                "time_scale": scenario.time_scale.value,
                "orekit_sidecar_url": scenario.orekit_sidecar_url,
                "spacecraft_template_id": scenario.constellation.satellites[0].satellite_id,
                "_rank": (mode_rank, name),
            }
        )
    candidates.sort(key=lambda item: item["_rank"])
    for item in candidates:
        item.pop("_rank", None)
    recommended = candidates[0]["scenario_name"] if candidates else None
    return {
        "recommended": recommended,
        "candidates": candidates,
        "policy": (
            "Prefer an operator-selected eligible ScenarioConfig. Otherwise prefer packaged DESIGN authority, "
            "then VALIDATION authority, deterministic by scenario name. Never use SCREENING or a scenario "
            "without Orekit/spacecraft authority for RINEX-to-runnable baseline construction."
        ),
    }


def install_mission_template_policy_routes(app: FastAPI, scenario_root: Path) -> None:
    @app.get("/api/mission/modelling-templates")
    def templates() -> dict[str, object]:
        return mission_modelling_templates(scenario_root)
