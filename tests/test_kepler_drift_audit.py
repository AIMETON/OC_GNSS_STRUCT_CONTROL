from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import yaml

from constellation_control.application.kepler_drift_audit import enrich_run_with_kepler_drift_audit
from constellation_control.application.run import run_scenario
from constellation_control.application.run_duration import run_scenario_with_duration

SCENARIO = Path("scenarios/mvp_45deg.yaml")


def test_post_run_audit_preserves_operational_metrics_and_writes_evidence(tmp_path: Path) -> None:
    run_dir = run_scenario(SCENARIO, tmp_path / "raw")
    before = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    before_metrics = before["metrics"]
    before_relative_rate = before["relative_operations"][0]["secular_delta_u_rate_rad_s"]

    diagnostics = enrich_run_with_kepler_drift_audit(run_dir)

    after = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert after["metrics"] == before_metrics
    assert after["relative_operations"][0]["secular_delta_u_rate_rad_s"] == before_relative_rate
    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.initial_period_difference_s == 0.0
    assert diagnostic.time_mean_kepler_delta_n_deg_day == 0.0
    assert "central-field baseline" in diagnostic.semantics

    audit_rows = json.loads((run_dir / "kepler_drift_consistency.json").read_text(encoding="utf-8"))
    assert audit_rows[0]["pair_id"] == "DEMO-ADD-45/DEMO-REF"
    assert "initial_period_difference_s" in audit_rows[0]
    assert "measured_delta_lambda_rate_deg_day" in audit_rows[0]
    assert "measured_delta_u_rate_deg_day" in audit_rows[0]
    assert "delta_lambda_minus_kepler_deg_day" in audit_rows[0]
    assert "delta_u_minus_kepler_deg_day" in audit_rows[0]

    frame = pd.read_csv(run_dir / "timeseries.csv")
    for column in (
        "kepler_delta_n_rad_s",
        "kepler_delta_n_deg_day",
        "kepler_time_mean_delta_n_deg_day",
        "measured_delta_lambda_harmonic_rate_deg_day",
        "measured_delta_u_harmonic_rate_deg_day",
        "delta_lambda_minus_kepler_deg_day",
        "delta_u_minus_kepler_deg_day",
    ):
        assert column in frame.columns
    assert (run_dir / "kepler_drift_consistency.md").exists()
    assert (run_dir / "kepler_drift_consistency.html").exists()
    assert "Hand-check formula" in (run_dir / "kepler_drift_consistency.md").read_text(encoding="utf-8")

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["algorithm_versions"]["kepler_drift_consistency"] == "mean-a-kepler-baseline-v1"
    assert after["provenance"]["kepler_drift_consistency"]["authority"].startswith("independent diagnostic")


def test_preview_duration_path_runs_kepler_audit_automatically(tmp_path: Path) -> None:
    completed = run_scenario_with_duration(
        SCENARIO,
        tmp_path / "preview",
        preset="custom",
        custom_duration_s=86_400.0,
    )
    assert (completed.run_dir / "kepler_drift_consistency.json").exists()
    summary = json.loads((completed.run_dir / "summary.json").read_text(encoding="utf-8"))
    assert len(summary["kepler_drift_consistency"]) == 1
    assert summary["kepler_drift_consistency"][0]["pair_id"] == "DEMO-ADD-45/DEMO-REF"


def test_preview_duration_baseline_without_relative_pairs_completes(tmp_path: Path) -> None:
    payload = yaml.safe_load(SCENARIO.read_text(encoding="utf-8"))
    payload["scenario_id"] = "reference-only-baseline"
    for satellite in payload["constellation"]["satellites"]:
        satellite["role"] = "reference"
        satellite.pop("reference_id", None)
    scenario_path = tmp_path / "reference-only.yaml"
    scenario_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    completed = run_scenario_with_duration(
        scenario_path,
        tmp_path / "preview-reference-only",
        preset="scenario",
    )

    summary = json.loads((completed.run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["relative_operations"] == []
    assert (completed.run_dir / "timeseries.csv").read_text(encoding="utf-8") == "\n"
    assert not (completed.run_dir / "kepler_drift_consistency.json").exists()
