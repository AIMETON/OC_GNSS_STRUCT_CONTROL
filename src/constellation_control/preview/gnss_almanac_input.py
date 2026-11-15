from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from constellation_control.adapters.gnss_almanac import GnssAlmanacFormat, preview_gnss_almanac
from constellation_control.adapters.orekit.mean_conversion import OrekitGpsAlmanacMeanConversionClient
from constellation_control.application.run import load_scenario
from constellation_control.domain.digital_twin import DigitalTwinConfig, ScenarioLineage
from constellation_control.domain.models import ConstellationSpec, SatelliteSpec, ScenarioConfig

GpsSelectionMode = Literal["all", "healthy-only", "selected"]


class GnssAlmanacPreviewRequest(BaseModel):
    filename: str
    content_text: str
    source_format: GnssAlmanacFormat


class GpsAlmanacAuthorityRequest(GnssAlmanacPreviewRequest):
    source_scenario_name: str
    template_satellite_id: str | None = None
    selection: GpsSelectionMode = "selected"
    prns: list[int] = Field(default_factory=list)
    # Legacy single-satellite API compatibility. The 0.2.17 UI never uses these fields.
    satellite_id: str | None = None
    prn: int | None = None


class GpsAlmanacCreateRequest(GpsAlmanacAuthorityRequest):
    target_scenario_name: str
    new_scenario_id: str


def _gps_source_format(source_format: GnssAlmanacFormat) -> Literal["gps-yuma", "gps-sem"]:
    if source_format == GnssAlmanacFormat.GPS_YUMA:
        return "gps-yuma"
    if source_format == GnssAlmanacFormat.GPS_SEM:
        return "gps-sem"
    raise ValueError("GLONASS remains preview-only and cannot use GPS almanac authority")


def _lineage_source_type(source_format: GnssAlmanacFormat) -> Literal["gps_yuma", "gps_sem"]:
    if source_format == GnssAlmanacFormat.GPS_YUMA:
        return "gps_yuma"
    if source_format == GnssAlmanacFormat.GPS_SEM:
        return "gps_sem"
    raise ValueError("GLONASS remains preview-only and cannot use GPS almanac authority")


def _legacy_single_request(request: GpsAlmanacAuthorityRequest) -> bool:
    return (
        request.template_satellite_id is None
        and not request.prns
        and request.selection == "selected"
        and request.satellite_id is not None
        and request.prn is not None
    )


def _source(root: Path, request: GpsAlmanacAuthorityRequest):
    source = load_scenario(root / request.source_scenario_name)
    template_id = request.template_satellite_id or request.satellite_id
    if not template_id:
        raise ValueError("select an explicit spacecraft template from the modelling-authority scenario")
    satellite = next(
        (item for item in source.constellation.satellites if item.satellite_id == template_id),
        None,
    )
    if satellite is None:
        raise ValueError(f"unknown template satellite_id: {template_id}")
    if not source.orekit_sidecar_url:
        raise ValueError(
            "selected modelling-authority scenario has no orekit_sidecar_url; "
            "choose an explicit DESIGN/VALIDATION authority with Orekit"
        )
    return source, satellite


def _selected_prns(preview, request: GpsAlmanacAuthorityRequest) -> tuple[int, ...]:
    if preview.source_format not in (GnssAlmanacFormat.GPS_YUMA, GnssAlmanacFormat.GPS_SEM):
        raise ValueError("GLONASS remains preview-only and cannot use GPS almanac authority")
    records = {
        int(item.prn): item
        for item in preview.records
        if getattr(item, "prn", None) is not None
    }
    if not records:
        raise ValueError("GPS almanac contains no PRN records")
    if request.selection == "all":
        chosen = list(records)
    elif request.selection == "healthy-only":
        chosen = [
            prn
            for prn, item in records.items()
            if int(getattr(item, "health", 1)) == 0
        ]
    else:
        chosen = list(request.prns)
        if not chosen and request.prn is not None:
            chosen = [request.prn]
    deduplicated: list[int] = []
    for prn in chosen:
        if prn not in deduplicated:
            deduplicated.append(prn)
    if not deduplicated:
        raise ValueError("GPS satellite selection is empty")
    unknown = [prn for prn in deduplicated if prn not in records]
    if unknown:
        raise ValueError("unknown GPS almanac PRN(s): " + ", ".join(str(prn) for prn in unknown))
    return tuple(deduplicated)


def _authority_batch(root: Path, request: GpsAlmanacAuthorityRequest):
    preview = preview_gnss_almanac(request.filename, request.content_text, request.source_format)
    source, template = _source(root, request)
    prns = _selected_prns(preview, request)
    client = OrekitGpsAlmanacMeanConversionClient(source.orekit_sidecar_url)
    converted = []
    for prn in prns:
        result = client.convert(
            source_format=_gps_source_format(request.source_format),
            source_name=preview.source_filename,
            source_text=request.content_text,
            prn=prn,
            frame=source.frame,
            target_epoch=source.epoch,
            target_time_scale=source.time_scale,
            spacecraft=template.spacecraft,
            force_model=source.force_model,
        )
        if result.backend_metadata.get("gps_prn") != str(prn):
            raise RuntimeError(
                f"Orekit GPS almanac authority returned a different PRN: requested={prn} "
                f"returned={result.backend_metadata.get('gps_prn')}"
            )
        converted.append((prn, result))
    return preview, source, template, tuple(converted)


def preview_gps_almanac_authority(
    root: Path,
    request: GpsAlmanacAuthorityRequest,
) -> dict[str, object]:
    preview, source, template, converted = _authority_batch(root, request)
    first_prn, first_result = converted[0]
    return {
        "valid": True,
        "source_scenario_id": source.scenario_id,
        "source_config_hash": source.config_hash(),
        "source_format": preview.source_format.value,
        "source_filename": preview.source_filename,
        "source_sha256": preview.source_sha256,
        "template_satellite_id": template.satellite_id,
        "selection": request.selection,
        "selected_prns": [prn for prn, _result in converted],
        "satellite_count": len(converted),
        "target_scenario_epoch": source.epoch.isoformat(),
        "target_time_scale": source.time_scale.value,
        "converted_records": [
            {
                "prn": prn,
                "mean_orbit": result.mean_orbit.model_dump(mode="json"),
                "backend_metadata": result.backend_metadata,
            }
            for prn, result in converted
        ],
        "prn": first_prn,
        "mean_orbit": first_result.mean_orbit.model_dump(mode="json") if len(converted) == 1 else None,
        "backend_metadata": first_result.backend_metadata,
    }


def _target(root: Path, name: str) -> Path:
    if not name or Path(name).name != name or not name.lower().endswith((".yaml", ".yml")):
        raise ValueError("target_scenario_name must be a new YAML file name without path components")
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = (root / name).resolve()
    if target.parent != root:
        raise ValueError("invalid target scenario path")
    if target.exists():
        raise ValueError("target scenario already exists; overwrite is forbidden")
    return target


def _legacy_single_child(source: ScenarioConfig, template, result) -> ConstellationSpec:
    satellites = tuple(
        item.model_copy(update={"mean_orbit": result.mean_orbit})
        if item.satellite_id == template.satellite_id
        else item
        for item in source.constellation.satellites
    )
    return source.constellation.model_copy(update={"satellites": satellites})


def _full_gps_constellation(template, converted) -> ConstellationSpec:
    satellites = tuple(
        SatelliteSpec(
            satellite_id=f"GPS-{prn:02d}",
            plane_id="ALMANAC-UNASSIGNED",
            role="reference",
            mean_orbit=result.mean_orbit,
            spacecraft=template.spacecraft,
        )
        for prn, result in converted
    )
    return ConstellationSpec(satellites=satellites, planes=())


def create_gps_almanac_derived_scenario(
    root: Path,
    request: GpsAlmanacCreateRequest,
) -> dict[str, object]:
    preview, source, template, converted = _authority_batch(root, request)
    if request.new_scenario_id == source.scenario_id:
        raise ValueError("new_scenario_id must differ from parent scenario_id")
    target = _target(root, request.target_scenario_name)
    legacy = _legacy_single_request(request)
    constellation = (
        _legacy_single_child(source, template, converted[0][1])
        if legacy
        else _full_gps_constellation(template, converted)
    )
    prior_twin = source.digital_twin or DigitalTwinConfig()
    source_type = _lineage_source_type(preview.source_format)
    prns = [prn for prn, _result in converted]
    authority = converted[0][1].backend_metadata.get(
        "source_authority",
        "GPS-ALMANAC-OREKIT-GNSS",
    )
    digital_twin = prior_twin.model_copy(
        update={
            "lineage": ScenarioLineage(
                parent_scenario_id=source.scenario_id,
                parent_config_hash=source.config_hash(),
                transformation="gps_almanac_import",
                random_seed=None,
                source_type=source_type,
                source_name=preview.source_filename,
                source_sha256=preview.source_sha256,
                source_record_id=(
                    str(prns[0]) if legacy else "GPS:" + ",".join(str(prn) for prn in prns)
                ),
                authority=authority,
            )
        }
    )
    child = ScenarioConfig.model_validate(
        source.model_dump(mode="json")
        | {
            "scenario_id": request.new_scenario_id,
            "constellation": constellation.model_dump(mode="json"),
            "maneuvers": [] if not legacy else source.model_dump(mode="json")["maneuvers"],
            "digital_twin": digital_twin.model_dump(mode="json"),
        }
    )
    target.write_text(
        yaml.safe_dump(child.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return {
        "saved": True,
        "runnable": True,
        "scenario_name": target.name,
        "scenario_id": child.scenario_id,
        "satellite_id": template.satellite_id,
        "template_satellite_id": template.satellite_id,
        "prn": prns[0],
        "selected_prns": prns,
        "satellite_count": len(prns),
        "selection": request.selection,
        "parent_scenario_id": source.scenario_id,
        "parent_config_hash": source.config_hash(),
        "child_config_hash": child.config_hash(),
        "source_format": preview.source_format.value,
        "source_filename": preview.source_filename,
        "source_sha256": preview.source_sha256,
        "backend_metadata": converted[0][1].backend_metadata,
    }


GNSS_ALMANAC_CARD = r"""
<div class="card" id="gnssAlmanacCard">
  <h3>GNSS Almanac intake</h3>
  <p class="hint">GPS YUMA/SEM: файл является orbital data authority. Для runnable ScenarioConfig modelling authority выбирается отдельно и явно. Можно загрузить все КА, только healthy или выбранные PRN; каждый выбранный PRN проходит Orekit GPS propagator → osculating PV → DSST mean. Активный сценарий не используется скрыто. GLONASS labelled text остаётся preview-only.</p>
  <div class="grid">
    <label>Формат / Format<select id="gnssAlmanacFormat"><option value="gps-yuma">GPS YUMA</option><option value="gps-sem">GPS SEM</option><option value="glonass-text">GLONASS labelled text</option></select></label>
    <label>Файл / File <input id="gnssAlmanacFile" type="file" accept=".alm,.al3,.txt"></label>
  </div>
  <button onclick="previewGnssAlmanac()">Проверить альманах / Preview almanac</button>
  <div class="grid">
    <label>Modelling authority<select id="gnssAlmanacAuthority" onchange="loadGnssAlmanacAuthority()"><option value="">— выберите явно —</option></select></label>
    <label>Шаблон КА authority / Spacecraft template<select id="gnssAlmanacTemplate"></select></label>
    <label>Выбор КА / Satellite selection<select id="gnssAlmanacSelection" onchange="syncGnssAlmanacSelection()"><option value="all">Все КА / All</option><option value="healthy-only">Только healthy / Healthy only</option><option value="selected">Выбранные PRN / Selected PRNs</option></select></label>
    <label>PRN <select id="gnssAlmanacPrns" multiple size="8" disabled></select></label>
  </div>
  <button onclick="previewGpsAlmanacAuthority()">Проверить выбранные КА через Orekit / Preview selected via Orekit</button>
  <pre id="gnssAlmanacPreview"></pre>
  <label>Новый scenario_id <input id="gnssAlmanacScenarioId" type="text" placeholder="gps-almanac-full"></label>
  <label>Новый YAML <input id="gnssAlmanacScenarioFile" type="text" placeholder="gps-almanac-full.yaml"></label>
  <button onclick="createGpsAlmanacScenario()">Создать полноценный сценарий / Create runnable scenario</button>
  <div id="gnssAlmanacStatus" class="status"></div>
</div>
"""


GNSS_ALMANAC_SCRIPT = r"""
let gnssAlmanacLast=null;
function syncGnssAlmanacSelection(){gnssAlmanacPrns.disabled=gnssAlmanacSelection.value!=='selected';}
function syncGnssAlmanacAuthorityOptions(){const names=(typeof catalog!=='undefined'&&catalog&&catalog.scenarios)||[];const previous=gnssAlmanacAuthority.value;gnssAlmanacAuthority.replaceChildren(new Option('— выберите явно —',''),...names.map(x=>new Option(x,x)));if(previous&&names.includes(previous)){gnssAlmanacAuthority.value=previous;void loadGnssAlmanacAuthority();}else{gnssAlmanacAuthority.value='';gnssAlmanacTemplate.replaceChildren();}}
async function loadGnssAlmanacAuthority(){const name=gnssAlmanacAuthority.value;gnssAlmanacTemplate.replaceChildren();if(!name)return;try{const r=await fetch('/api/scenarios/'+encodeURIComponent(name));const d=await r.json();if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail||d));const normalized=d.normalized||d;if(!normalized.orekit_sidecar_url)throw new Error('Выбранный modelling authority не содержит orekit_sidecar_url');const sats=(normalized.constellation||{}).satellites||[];gnssAlmanacTemplate.replaceChildren(...sats.map(s=>new Option(s.satellite_id,s.satellite_id)));gnssAlmanacStatus.textContent='AUTHORITY READY: '+name;gnssAlmanacStatus.className='status ok';}catch(e){gnssAlmanacAuthority.value='';gnssAlmanacTemplate.replaceChildren();gnssAlmanacStatus.textContent=String(e.message||e);gnssAlmanacStatus.className='status danger';}}
const gnssAlmanacPriorLoadScenario=loadScenario;
loadScenario=async function(){await gnssAlmanacPriorLoadScenario();syncGnssAlmanacAuthorityOptions();};
async function previewGnssAlmanac(){const file=gnssAlmanacFile.files&&gnssAlmanacFile.files[0];if(!file){gnssAlmanacStatus.textContent='Выберите файл альманаха';gnssAlmanacStatus.className='status danger';return;}const text=await file.text();gnssAlmanacStatus.textContent='Validation…';const format=gnssAlmanacFormat.value;const r=await fetch('/api/gnss-almanac/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({filename:file.name,content_text:text,source_format:format})});const d=await r.json();if(!r.ok){gnssAlmanacStatus.textContent=d.detail||'Almanac preview failed';gnssAlmanacStatus.className='status danger';return;}gnssAlmanacLast={filename:file.name,content_text:text,source_format:format,preview:d};const gps=format==='gps-yuma'||format==='gps-sem';gnssAlmanacPrns.replaceChildren(...(gps?d.records:[]).map(x=>new Option('PRN '+x.prn,String(x.prn))));gnssAlmanacPreview.textContent=JSON.stringify(d,null,2);gnssAlmanacStatus.textContent='VALID: '+d.source_format+'; records='+d.records.length+(gps?'; выберите modelling authority и состав ОГ':'; GLONASS authority blocked');gnssAlmanacStatus.className='status ok';syncGnssAlmanacSelection();}
function gpsAlmanacSelectedPrns(){return Array.from(gnssAlmanacPrns.selectedOptions).map(o=>Number(o.value));}
function gpsAlmanacAuthorityPayload(){if(!gnssAlmanacLast)throw new Error('preview almanac first');if(gnssAlmanacLast.source_format==='glonass-text')throw new Error('GLONASS remains preview-only');if(!gnssAlmanacAuthority.value)throw new Error('Выберите modelling authority явно');if(!gnssAlmanacTemplate.value)throw new Error('Выберите шаблон КА authority');const selection=gnssAlmanacSelection.value;const prns=selection==='selected'?gpsAlmanacSelectedPrns():[];if(selection==='selected'&&!prns.length)throw new Error('Выберите хотя бы один GPS PRN');return {filename:gnssAlmanacLast.filename,content_text:gnssAlmanacLast.content_text,source_format:gnssAlmanacLast.source_format,source_scenario_name:gnssAlmanacAuthority.value,template_satellite_id:gnssAlmanacTemplate.value,selection,prns};}
async function previewGpsAlmanacAuthority(){let p;try{p=gpsAlmanacAuthorityPayload();}catch(e){gnssAlmanacStatus.textContent=String(e.message||e);gnssAlmanacStatus.className='status danger';return;}gnssAlmanacStatus.textContent='Orekit GPS almanac authority…';const r=await fetch('/api/gnss-almanac/authority',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});const d=await r.json();if(!r.ok){gnssAlmanacStatus.textContent=d.detail||'GPS almanac authority failed';gnssAlmanacStatus.className='status danger';return;}gnssAlmanacPreview.textContent=JSON.stringify(d,null,2);gnssAlmanacStatus.textContent='AUTHORITY VALID: satellites='+d.satellite_count+'; target='+d.target_scenario_epoch;gnssAlmanacStatus.className='status ok';}
async function createGpsAlmanacScenario(){let base;try{base=gpsAlmanacAuthorityPayload();}catch(e){gnssAlmanacStatus.textContent=String(e.message||e);gnssAlmanacStatus.className='status danger';return;}const p={...base,new_scenario_id:gnssAlmanacScenarioId.value.trim(),target_scenario_name:gnssAlmanacScenarioFile.value.trim()};if(!p.new_scenario_id||!p.target_scenario_name){gnssAlmanacStatus.textContent='Укажите новый scenario_id и YAML';gnssAlmanacStatus.className='status danger';return;}gnssAlmanacStatus.textContent='Создание GPS ScenarioConfig…';const r=await fetch('/api/gnss-almanac/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});const d=await r.json();if(!r.ok){gnssAlmanacStatus.textContent=d.detail||'Create failed';gnssAlmanacStatus.className='status danger';return;}const c=await fetch('/api/scenarios');catalog=await c.json();scenario.replaceChildren(...catalog.scenarios.map(x=>new Option(x,x)));scenario.value=d.scenario_name;await loadScenario();gnssAlmanacStatus.textContent='Создан: '+d.scenario_name+'; GPS satellites='+d.satellite_count;gnssAlmanacStatus.className='status ok';}
"""


def install_gnss_almanac_routes(app: FastAPI, scenario_root: Path = Path("scenarios")) -> None:
    @app.post("/api/gnss-almanac/preview")
    def preview(request: GnssAlmanacPreviewRequest) -> dict[str, object]:
        try:
            return preview_gnss_almanac(
                request.filename,
                request.content_text,
                request.source_format,
            ).model_dump(mode="json")
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/gnss-almanac/authority")
    def authority(request: GpsAlmanacAuthorityRequest) -> dict[str, object]:
        try:
            return preview_gps_almanac_authority(scenario_root, request)
        except (ValueError, TypeError, RuntimeError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/gnss-almanac/create")
    def create(request: GpsAlmanacCreateRequest) -> dict[str, object]:
        try:
            return create_gps_almanac_derived_scenario(scenario_root, request)
        except (ValueError, TypeError, RuntimeError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
