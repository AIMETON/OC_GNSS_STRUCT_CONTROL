from __future__ import annotations

from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Literal

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from constellation_control.adapters.bkg_rinex_nav import fetch_bkg_gnss_daily
from constellation_control.adapters.orekit.mean_conversion import (
    OrekitRinexGlonassMeanConversionClient,
    OrekitRinexGnssMeanConversionClient,
)
from constellation_control.application.run import load_scenario
from constellation_control.domain.digital_twin import DigitalTwinConfig, ScenarioLineage
from constellation_control.domain.models import ConstellationSpec, SatelliteSpec, ScenarioConfig

IgsSystem = Literal["GLONASS", "GPS", "Galileo", "BeiDou"]
DEFAULT_MAX_EPHEMERIS_AGE_S = 7200.0
DEFAULT_GLONASS_PROPAGATION_STEP_S = 60.0

_SYSTEM_PREFIX = {
    "GLONASS": "GLO",
    "GPS": "GPS",
    "Galileo": "GAL",
    "BeiDou": "BDS",
}


class IgsConstellationRequest(BaseModel):
    source_date: date
    system: IgsSystem
    source_scenario_name: str


def _safe_target(root: Path, name: str) -> Path:
    target = (root.resolve() / name).resolve()
    if target.parent != root.resolve():
        raise ValueError("invalid target scenario path")
    if target.exists():
        raise ValueError(f"derived scenario already exists: {name}")
    return target


def build_igs_constellation_scenario(root: Path, request: IgsConstellationRequest) -> dict[str, object]:
    source = load_scenario(root / request.source_scenario_name)
    if not source.constellation.satellites:
        raise ValueError("active scenario contains no spacecraft template")
    if not source.orekit_sidecar_url:
        raise ValueError("active scenario has no orekit_sidecar_url")

    template = source.constellation.satellites[0]
    target_epoch = datetime.combine(request.source_date, time.min, tzinfo=UTC)
    cache_root = root.parent / "data" / "cache" / "rinex"
    cached = fetch_bkg_gnss_daily(request.source_date, request.system, cache_root)
    rinex_text = cached.rinex_path.read_text(encoding="ascii", errors="strict")

    if request.system == "GLONASS":
        converted = OrekitRinexGlonassMeanConversionClient(source.orekit_sidecar_url).convert(
            source_name=cached.source_url,
            source_text=rinex_text,
            frame=source.frame,
            target_epoch=target_epoch,
            target_time_scale=source.time_scale,
            max_ephemeris_age_s=DEFAULT_MAX_EPHEMERIS_AGE_S,
            glonass_propagation_step_s=DEFAULT_GLONASS_PROPAGATION_STEP_S,
            spacecraft=template.spacecraft,
            force_model=source.force_model,
        )
        records = [(item.prn, item.mean_orbit) for item in converted.satellites]
    else:
        converted = OrekitRinexGnssMeanConversionClient(source.orekit_sidecar_url).convert(
            system=request.system,
            source_name=cached.source_url,
            source_text=rinex_text,
            frame=source.frame,
            target_epoch=target_epoch,
            target_time_scale=source.time_scale,
            max_ephemeris_age_s=DEFAULT_MAX_EPHEMERIS_AGE_S,
            spacecraft=template.spacecraft,
            force_model=source.force_model,
        )
        records = [(item.prn, item.mean_orbit) for item in converted.satellites]

    prefix = _SYSTEM_PREFIX[request.system]
    satellites = tuple(
        SatelliteSpec(
            satellite_id=f"{prefix}-{prn:02d}",
            plane_id="RINEX-UNASSIGNED",
            role="reference",
            mean_orbit=mean_orbit,
            spacecraft=template.spacecraft,
        )
        for prn, mean_orbit in records
    )
    if not satellites:
        raise ValueError(f"IGS RINEX contains no {request.system} spacecraft")

    slug = request.system.lower().replace(" ", "-")
    scenario_id = f"igs-{slug}-{request.source_date.isoformat()}"
    scenario_name = f"{scenario_id}.yaml"
    target = _safe_target(root, scenario_name)

    lineage = ScenarioLineage(
        parent_scenario_id=source.scenario_id,
        parent_config_hash=source.config_hash(),
        transformation="rinex_nav_import",
        random_seed=None,
        source_type="rinex_nav",
        source_name=cached.source_url,
        source_sha256=cached.source_sha256,
        source_record_id=f"{request.system}:{len(satellites)}:{request.source_date.isoformat()}",
        authority=(
            f"BKG/IGS RINEX NAV; system={request.system}; "
            f"target_epoch={target_epoch.isoformat()}; "
            f"max_ephemeris_age_s={DEFAULT_MAX_EPHEMERIS_AGE_S}; "
            f"glonass_propagation_step_s={DEFAULT_GLONASS_PROPAGATION_STEP_S if request.system == 'GLONASS' else 'n/a'}; "
            f"spacecraft_template={template.satellite_id}"
        ),
    )
    prior_twin = source.digital_twin or DigitalTwinConfig()
    child = ScenarioConfig.model_validate(
        source.model_dump(mode="json")
        | {
            "scenario_id": scenario_id,
            "epoch": target_epoch.isoformat(),
            "constellation": ConstellationSpec(satellites=satellites, planes=()).model_dump(mode="json"),
            "maneuvers": [],
            "digital_twin": prior_twin.model_copy(update={"lineage": lineage}).model_dump(mode="json"),
        }
    )
    target.write_text(
        yaml.safe_dump(child.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return {
        "saved": True,
        "runnable": True,
        "scenario_name": scenario_name,
        "scenario_id": scenario_id,
        "system": request.system,
        "source_date": request.source_date.isoformat(),
        "target_epoch": target_epoch.isoformat(),
        "satellite_count": len(satellites),
        "source_url": cached.source_url,
        "source_sha256": cached.source_sha256,
        "rinex_sha256": cached.rinex_sha256,
        "cached_rinex": str(cached.rinex_path),
        "template_satellite_id": template.satellite_id,
        "max_ephemeris_age_s": DEFAULT_MAX_EPHEMERIS_AGE_S,
        "glonass_propagation_step_s": (
            DEFAULT_GLONASS_PROPAGATION_STEP_S if request.system == "GLONASS" else None
        ),
        "child_config_hash": child.config_hash(),
    }


IGS_CONSTELLATION_CARD = r"""
<div class="card primary-input-card" id="igsConstellationCard">
  <h3>Источник орбитальной группировки</h3>
  <p class="hint">Основной рабочий путь: выберите дату и систему. Программа сама скачает RINEX NAV с IGS/BKG, сохранит исходник, сформирует внутренний ScenarioConfig и сделает его активным.</p>
  <div class="grid">
    <label>Стартовая дата
      <input id="igsStartDate" type="date">
    </label>
    <label>Система
      <select id="igsSystem">
        <option value="GLONASS">ГЛОНАСС</option>
        <option value="GPS">GPS</option>
        <option value="Galileo">Galileo</option>
        <option value="BeiDou">BeiDou / Compass</option>
      </select>
    </label>
  </div>
  <button onclick="buildIgsConstellation()">Скачать IGS и сформировать сценарий</button>
  <div id="igsConstellationStatus" class="status"></div>
  <pre id="igsConstellationResult"></pre>
  <details>
    <summary>Используемая инженерная политика</summary>
    <p class="hint">Источник: BKG/IGS daily RINEX NAV. Целевая эпоха: 00:00 UTC выбранной даты. Допустимый возраст ближайшего broadcast ephemeris: 7200 s. Для ГЛОНАСС шаг broadcast propagation: 60 s. Модель КА берётся из первого КА активного базового сценария. Все значения записываются в lineage.</p>
  </details>
</div>
"""

IGS_CONSTELLATION_SCRIPT = r"""
async function buildIgsConstellation(){
  const date=igsStartDate.value;
  if(!date){igsConstellationStatus.textContent='Укажите стартовую дату';igsConstellationStatus.className='status danger';return;}
  const p={source_date:date,system:igsSystem.value,source_scenario_name:scenario.value};
  igsConstellationStatus.textContent='IGS/BKG → RINEX NAV → Orekit → ScenarioConfig…';igsConstellationStatus.className='status';
  try{
    const r=await fetch('/api/igs-constellation/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});
    const d=await r.json();if(!r.ok)throw new Error(d.detail||'IGS intake failed');
    igsConstellationResult.textContent=JSON.stringify(d,null,2);
    const c=await fetch('/api/scenarios');catalog=await c.json();
    scenario.replaceChildren(...catalog.scenarios.map(x=>{const o=document.createElement('option');o.value=x;o.textContent=x;return o;}));
    scenario.value=d.scenario_name;await loadScenario();
    igsConstellationStatus.textContent='RUNNABLE: '+d.scenario_name+'; '+d.system+'; КА='+d.satellite_count;
    igsConstellationStatus.className='status ok';
  }catch(e){igsConstellationStatus.textContent=String(e.message||e);igsConstellationStatus.className='status danger';}
}
"""


def install_igs_constellation_routes(app: FastAPI, scenario_root: Path) -> None:
    @app.post("/api/igs-constellation/create")
    def create(request: IgsConstellationRequest) -> dict[str, object]:
        try:
            return build_igs_constellation_scenario(scenario_root, request)
        except (ValueError, RuntimeError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
