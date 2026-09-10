from __future__ import annotations

from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Literal

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from constellation_control.adapters.bkg_rinex_nav import CachedRinexNav, fetch_bkg_gnss_daily
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


class IgsDataFetchRequest(BaseModel):
    source_date: date
    system: IgsSystem


class IgsConstellationRequest(IgsDataFetchRequest):
    template_scenario_name: str


def _safe_target(root: Path, name: str) -> Path:
    target = (root.resolve() / name).resolve()
    if target.parent != root.resolve():
        raise ValueError("invalid target scenario path")
    if target.exists():
        raise ValueError(f"derived scenario already exists: {name}")
    return target


def _cache_root(root: Path) -> Path:
    return root.parent / "data" / "cache" / "rinex"


def _cache_result(cached: CachedRinexNav, request: IgsDataFetchRequest) -> dict[str, object]:
    return {
        "downloaded": cached.transport != "cache",
        "cached": True,
        "system": request.system,
        "source_date": request.source_date.isoformat(),
        "source_url": cached.source_url,
        "source_filename": cached.source_filename,
        "source_sha256": cached.source_sha256,
        "rinex_sha256": cached.rinex_sha256,
        "cached_gzip": str(cached.gzip_path),
        "cached_rinex": str(cached.rinex_path),
        "cache_manifest": str(cached.manifest_path),
        "transport": cached.transport,
        "requires_orekit": False,
        "requires_scenario": False,
    }


def fetch_igs_constellation_data(root: Path, request: IgsDataFetchRequest) -> dict[str, object]:
    cached = fetch_bkg_gnss_daily(request.source_date, request.system, _cache_root(root))
    return _cache_result(cached, request)


def build_igs_constellation_scenario(root: Path, request: IgsConstellationRequest) -> dict[str, object]:
    if not request.template_scenario_name:
        raise ValueError("select an explicit template scenario")

    source = load_scenario(root / request.template_scenario_name)
    if not source.constellation.satellites:
        raise ValueError("template scenario contains no spacecraft template")
    if not source.orekit_sidecar_url:
        raise ValueError("selected template scenario has no orekit_sidecar_url")

    template = source.constellation.satellites[0]
    target_epoch = datetime.combine(request.source_date, time.min, tzinfo=UTC)
    cached = fetch_bkg_gnss_daily(request.source_date, request.system, _cache_root(root))
    rinex_text = cached.rinex_path.read_text(encoding="ascii", errors="strict")

    if request.system == "GLONASS":
        glonass_converted = OrekitRinexGlonassMeanConversionClient(source.orekit_sidecar_url).convert(
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
        records = [(item.prn, item.mean_orbit) for item in glonass_converted.satellites]
    else:
        gnss_converted = OrekitRinexGnssMeanConversionClient(source.orekit_sidecar_url).convert(
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
        records = [(item.prn, item.mean_orbit) for item in gnss_converted.satellites]

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
            f"IGS RINEX NAV; system={request.system}; "
            f"source_url={cached.source_url}; "
            f"source_transport={cached.transport}; "
            f"target_epoch={target_epoch.isoformat()}; "
            f"max_ephemeris_age_s={DEFAULT_MAX_EPHEMERIS_AGE_S}; "
            f"glonass_propagation_step_s={DEFAULT_GLONASS_PROPAGATION_STEP_S if request.system == 'GLONASS' else 'n/a'}; "
            f"spacecraft_template={template.satellite_id}; "
            f"template_scenario={request.template_scenario_name}"
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
        "source_transport": cached.transport,
        "template_scenario_name": request.template_scenario_name,
        "template_satellite_id": template.satellite_id,
        "max_ephemeris_age_s": DEFAULT_MAX_EPHEMERIS_AGE_S,
        "glonass_propagation_step_s": (
            DEFAULT_GLONASS_PROPAGATION_STEP_S if request.system == "GLONASS" else None
        ),
        "child_config_hash": child.config_hash(),
    }


IGS_CONSTELLATION_CARD = r"""
<div class="card primary-input-card" id="igsConstellationCard">
  <h3>Создать baseline из реальной ОГ / Create baseline from real constellation</h3>
  <p class="hint">Нормальный рабочий путь: выберите дату, систему и modelling template. Программа сама получает/использует cache RINEX NAV, сохраняет provenance и строит runnable ScenarioConfig. Технические этапы доступны ниже для ручного эшелона.</p>
  <div class="grid">
    <label>Дата baseline
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
    <label>Modelling template
      <select id="igsTemplateScenario"><option value="">— выберите —</option></select>
    </label>
  </div>
  <button onclick="createIgsBaseline()">Создать baseline / Create runnable baseline</button>
  <div id="igsBaselineStatus" class="status"></div>
  <details>
    <summary>Ручной эшелон: разделить получение данных и построение ScenarioConfig</summary>
    <p class="hint">Получение RINEX зависит только от даты и GNSS и не требует активного сценария/Orekit. Второй шаг использует явно выбранный modelling template как authority.</p>
    <button onclick="fetchIgsConstellationData()">1. Скачать IGS RINEX</button>
    <div id="igsConstellationFetchStatus" class="status"></div>
    <button onclick="buildIgsConstellation()">2. Сформировать сценарий</button>
    <div id="igsConstellationStatus" class="status"></div>
  </details>
  <pre id="igsConstellationResult"></pre>
  <details>
    <summary>Инженерная политика и provenance</summary>
    <p class="hint">Сетевой intake выполняется cache-first. Modelling template задаёт force model, frame/time scale, integrator и физическую модель КА. Целевая эпоха: 00:00 UTC выбранной даты. Допустимый возраст ближайшего broadcast ephemeris: 7200 s. Для ГЛОНАСС шаг broadcast propagation: 60 s. Source URL, transport, SHA-256 и template записываются в lineage.</p>
  </details>
</div>
"""


IGS_CONSTELLATION_SCRIPT = r"""
function syncIgsTemplateScenarios(){
  if(typeof catalog==='undefined'||!catalog||!Array.isArray(catalog.scenarios))return;
  const previous=igsTemplateScenario.value;
  const opts=[(()=>{const o=document.createElement('option');o.value='';o.textContent='— выберите —';return o;})(),...catalog.scenarios.map(x=>{const o=document.createElement('option');o.value=x;o.textContent=x;return o;})];
  igsTemplateScenario.replaceChildren(...opts);
  if(previous&&catalog.scenarios.includes(previous))igsTemplateScenario.value=previous;
}
async function fetchIgsConstellationData(){
  const date=igsStartDate.value;
  if(!date){igsConstellationFetchStatus.textContent='Укажите стартовую дату';igsConstellationFetchStatus.className='status danger';return false;}
  const p={source_date:date,system:igsSystem.value};
  igsConstellationFetchStatus.textContent='IGS/BKG → RINEX NAV → локальный cache…';igsConstellationFetchStatus.className='status';
  try{
    const r=await fetch('/api/igs-constellation/fetch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});
    const d=await r.json();if(!r.ok)throw new Error(d.detail||'IGS data fetch failed');
    igsConstellationResult.textContent=JSON.stringify(d,null,2);
    igsConstellationFetchStatus.textContent='DATA READY: '+d.source_filename+'; transport='+d.transport;
    igsConstellationFetchStatus.className='status ok';return true;
  }catch(e){igsConstellationFetchStatus.textContent=String(e.message||e);igsConstellationFetchStatus.className='status danger';return false;}
}
async function buildIgsConstellation(){
  const date=igsStartDate.value;
  if(!date){igsConstellationStatus.textContent='Укажите стартовую дату';igsConstellationStatus.className='status danger';return false;}
  const template=igsTemplateScenario.value;
  if(!template){igsConstellationStatus.textContent='Явно выберите modelling template';igsConstellationStatus.className='status danger';return false;}
  const p={source_date:date,system:igsSystem.value,template_scenario_name:template};
  igsConstellationStatus.textContent='Локальный RINEX → Orekit → ScenarioConfig…';igsConstellationStatus.className='status';
  try{
    const r=await fetch('/api/igs-constellation/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});
    const d=await r.json();if(!r.ok)throw new Error(d.detail||'IGS scenario build failed');
    igsConstellationResult.textContent=JSON.stringify(d,null,2);
    const c=await fetch('/api/scenarios');catalog=await c.json();
    scenario.replaceChildren(...catalog.scenarios.map(x=>{const o=document.createElement('option');o.value=x;o.textContent=x;return o;}));
    syncIgsTemplateScenarios();
    scenario.value=d.scenario_name;await loadScenario();
    igsConstellationStatus.textContent='RUNNABLE: '+d.scenario_name+'; '+d.system+'; КА='+d.satellite_count;
    igsConstellationStatus.className='status ok';return true;
  }catch(e){igsConstellationStatus.textContent=String(e.message||e);igsConstellationStatus.className='status danger';return false;}
}
async function createIgsBaseline(){
  if(!igsStartDate.value){igsBaselineStatus.textContent='Укажите дату baseline';igsBaselineStatus.className='status danger';return false;}
  if(!igsTemplateScenario.value){igsBaselineStatus.textContent='Выберите modelling template';igsBaselineStatus.className='status danger';return false;}
  igsBaselineStatus.textContent='Создание baseline: source → cache → authority → runnable ScenarioConfig…';igsBaselineStatus.className='status';
  if(!(await fetchIgsConstellationData())){igsBaselineStatus.textContent='Baseline остановлен на получении исходных данных';igsBaselineStatus.className='status danger';return false;}
  if(!(await buildIgsConstellation())){igsBaselineStatus.textContent='Baseline остановлен при построении ScenarioConfig';igsBaselineStatus.className='status danger';return false;}
  igsBaselineStatus.textContent='BASELINE READY: '+scenario.value;igsBaselineStatus.className='status ok';return true;
}
"""


def install_igs_constellation_routes(app: FastAPI, scenario_root: Path) -> None:
    @app.post("/api/igs-constellation/fetch")
    def fetch_data(request: IgsDataFetchRequest) -> dict[str, object]:
        try:
            return fetch_igs_constellation_data(scenario_root, request)
        except (ValueError, RuntimeError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/igs-constellation/create")
    def create(request: IgsConstellationRequest) -> dict[str, object]:
        try:
            return build_igs_constellation_scenario(scenario_root, request)
        except (ValueError, RuntimeError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
