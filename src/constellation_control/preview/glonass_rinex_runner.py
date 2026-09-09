from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from constellation_control.adapters.bkg_rinex_nav import fetch_bkg_glonass_daily
from constellation_control.adapters.orekit.mean_conversion import OrekitRinexGlonassMeanConversionClient
from constellation_control.application.run import load_scenario
from constellation_control.domain.digital_twin import DigitalTwinConfig, ScenarioLineage
from constellation_control.domain.models import ConstellationSpec, SatelliteSpec, ScenarioConfig


class GlonassRinexRunnerRequest(BaseModel):
    source_date: date
    source_scenario_name: str
    template_satellite_id: str
    target_epoch: datetime
    max_ephemeris_age_s: float = Field(gt=0.0)
    glonass_propagation_step_s: float = Field(gt=0.0)
    target_scenario_name: str
    new_scenario_id: str


def _target(root: Path, name: str) -> Path:
    if not name or Path(name).name != name or not name.lower().endswith((".yaml", ".yml")):
        raise ValueError("target_scenario_name must be a plain YAML file name")
    target = (root.resolve() / name).resolve()
    if target.parent != root.resolve():
        raise ValueError("invalid target scenario path")
    if target.exists():
        raise ValueError("target scenario already exists; overwrite is forbidden")
    return target


def build_glonass_rinex_scenario(root: Path, request: GlonassRinexRunnerRequest) -> dict[str, object]:
    source = load_scenario(root / request.source_scenario_name)
    template = next((s for s in source.constellation.satellites if s.satellite_id == request.template_satellite_id), None)
    if template is None:
        raise ValueError(f"unknown template_satellite_id: {request.template_satellite_id}")
    if not source.orekit_sidecar_url:
        raise ValueError("selected scenario has no orekit_sidecar_url")
    if request.new_scenario_id == source.scenario_id:
        raise ValueError("new_scenario_id must differ from parent scenario_id")

    cached = fetch_bkg_glonass_daily(request.source_date, root.parent / "data" / "cache" / "rinex")
    rinex_text = cached.rinex_path.read_text(encoding="ascii", errors="strict")
    result = OrekitRinexGlonassMeanConversionClient(source.orekit_sidecar_url).convert(
        source_name=cached.source_url,
        source_text=rinex_text,
        frame=source.frame,
        target_epoch=request.target_epoch,
        target_time_scale=source.time_scale,
        max_ephemeris_age_s=request.max_ephemeris_age_s,
        glonass_propagation_step_s=request.glonass_propagation_step_s,
        spacecraft=template.spacecraft,
        force_model=source.force_model,
    )

    satellites = tuple(
        SatelliteSpec(
            satellite_id=f"GLO-{item.prn:02d}",
            plane_id="RINEX-UNASSIGNED",
            role="reference",
            mean_orbit=item.mean_orbit,
            spacecraft=template.spacecraft,
        )
        for item in result.satellites
    )
    prior_twin = source.digital_twin or DigitalTwinConfig()
    lineage = ScenarioLineage(
        parent_scenario_id=source.scenario_id,
        parent_config_hash=source.config_hash(),
        transformation="rinex_nav_import",
        random_seed=None,
        source_type="rinex_nav",
        source_name=cached.source_url,
        source_sha256=cached.source_sha256,
        source_record_id=f"GLO:{len(satellites)}:{request.source_date.isoformat()}",
        authority=(
            "BKG/IGS RINEX NAV -> Orekit RinexNavigationParser -> "
            "GLONASSNumericalPropagator -> DSST mean; "
            f"target_epoch={request.target_epoch.isoformat()}; "
            f"max_ephemeris_age_s={request.max_ephemeris_age_s}; "
            f"glonass_propagation_step_s={request.glonass_propagation_step_s}"
        ),
    )
    child = ScenarioConfig.model_validate(
        source.model_dump(mode="json")
        | {
            "scenario_id": request.new_scenario_id,
            "epoch": request.target_epoch.isoformat(),
            "constellation": ConstellationSpec(satellites=satellites, planes=()).model_dump(mode="json"),
            "maneuvers": [],
            "digital_twin": prior_twin.model_copy(update={"lineage": lineage}).model_dump(mode="json"),
        }
    )
    target = _target(root, request.target_scenario_name)
    target.write_text(yaml.safe_dump(child.model_dump(mode="json"), sort_keys=False, allow_unicode=True), encoding="utf-8")
    return {
        "saved": True,
        "runnable": True,
        "scenario_name": target.name,
        "scenario_id": child.scenario_id,
        "child_config_hash": child.config_hash(),
        "satellite_count": len(satellites),
        "source_url": cached.source_url,
        "source_sha256": cached.source_sha256,
        "rinex_sha256": cached.rinex_sha256,
        "cached_gzip": str(cached.gzip_path),
        "cached_rinex": str(cached.rinex_path),
        "cache_manifest": str(cached.manifest_path),
        "target_epoch": request.target_epoch.isoformat(),
    }


GLONASS_RINEX_CARD = """
<div class="card" id="glonassRinexRunnerCard">
<h3>IGS/BKG RINEX NAV → GLONASS ScenarioConfig</h3>
<p class="hint">Скачивание RINEX NAV, immutable cache, Orekit parsing/propagation, DSST mean, новый runnable ScenarioConfig.</p>
<div class="grid">
<label>Дата RINEX <input id="gloRinexDate" type="date"></label>
<label>Шаблон КА <select id="gloRinexTemplate"></select></label>
<label>Целевая эпоха <input id="gloRinexEpoch" type="text" placeholder="2026-09-09T00:00:00Z"></label>
<label>Max age, s <input id="gloRinexAge" type="number" value="7200"></label>
<label>GLONASS propagation step, s <input id="gloRinexStep" type="number" value="60"></label>
<label>Новый scenario_id <input id="gloRinexScenarioId" value="igs-bkg-glonass-rinex"></label>
<label>Новый YAML <input id="gloRinexFile" value="igs-bkg-glonass-rinex.yaml"></label>
</div>
<button onclick="buildGlonassRinex()">Скачать RINEX и создать сценарий</button>
<pre id="gloRinexResult"></pre><div id="gloRinexStatus" class="status"></div>
</div>
"""

GLONASS_RINEX_SCRIPT = r"""
function syncGlonassRinexTemplate(){if(!current)return;const sats=((current.normalized||current).constellation||{}).satellites||[];gloRinexTemplate.replaceChildren(...sats.map(s=>{const o=document.createElement('option');o.value=s.satellite_id;o.textContent=s.satellite_id;return o;}));}
async function buildGlonassRinex(){try{gloRinexStatus.textContent='RINEX download / conversion…';const p={source_date:gloRinexDate.value,source_scenario_name:scenario.value,template_satellite_id:gloRinexTemplate.value,target_epoch:gloRinexEpoch.value,max_ephemeris_age_s:Number(gloRinexAge.value),glonass_propagation_step_s:Number(gloRinexStep.value),target_scenario_name:gloRinexFile.value.trim(),new_scenario_id:gloRinexScenarioId.value.trim()};const r=await fetch('/api/glonass-rinex-runner/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});const d=await r.json();if(!r.ok)throw new Error(d.detail||'RINEX runner failed');gloRinexResult.textContent=JSON.stringify(d,null,2);gloRinexStatus.textContent='VALID: '+d.satellite_count+' GLONASS satellites';gloRinexStatus.className='status ok';}catch(e){gloRinexStatus.textContent=String(e.message||e);gloRinexStatus.className='status danger';}}
"""


def install_glonass_rinex_runner_routes(app: FastAPI, scenario_root: Path) -> None:
    @app.post("/api/glonass-rinex-runner/create")
    def create(request: GlonassRinexRunnerRequest) -> dict[str, object]:
        try:
            return build_glonass_rinex_scenario(scenario_root, request)
        except (ValueError, RuntimeError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
