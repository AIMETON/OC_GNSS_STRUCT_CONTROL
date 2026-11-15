from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from constellation_control.adapters.galileo_gsc_almanac import (
    GSC_ALMANAC_INDEX_URL,
    GSC_DAILY_FILE_PREFIX,
    GalileoGscAlmanac,
    _fetch_text,
    fetch_latest_galileo_gsc_almanac,
    parse_galileo_gsc_almanac,
)
from constellation_control.adapters.orekit.galileo_almanac import OrekitGalileoAlmanacMeanConversionClient
from constellation_control.application.run import load_scenario
from constellation_control.domain.digital_twin import DigitalTwinConfig, ScenarioLineage
from constellation_control.domain.models import ConstellationSpec, SatelliteSpec, ScenarioConfig, TimeScaleName

GalileoSelectionMode = Literal["all", "healthy-only", "selected"]


class GalileoGscOfflineRequest(BaseModel):
    filename: str
    content_text: str


class GalileoGscAuthorityRequest(GalileoGscOfflineRequest):
    source_scenario_name: str
    template_satellite_id: str
    selection: GalileoSelectionMode = "healthy-only"
    svids: list[int] = Field(default_factory=list)


class GalileoGscCreateRequest(GalileoGscAuthorityRequest):
    target_scenario_name: str
    new_scenario_id: str


def _payload(almanac: GalileoGscAlmanac, *, content_text: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "valid": True,
        "source_url": almanac.source_url,
        "source_filename": almanac.source_filename,
        "source_sha256": almanac.source_sha256,
        "issue_date_utc": almanac.issue_date_utc.isoformat() if almanac.issue_date_utc else None,
        "record_count": len(almanac.records),
        "records": [
            {
                "svid": record.svid,
                "delta_sqrt_a_m_sqrt": record.delta_sqrt_a_m_sqrt,
                "sqrt_a_m_sqrt": record.sqrt_a_m_sqrt,
                "semi_major_axis_m": record.semi_major_axis_m,
                "eccentricity": record.eccentricity,
                "delta_inclination_semicircles": record.delta_inclination_semicircles,
                "inclination_rad": record.inclination_rad,
                "raan_semicircles": record.raan_semicircles,
                "raan_rad": record.raan_rad,
                "raan_rate_semicircles_s": record.raan_rate_semicircles_s,
                "raan_rate_rad_s": record.raan_rate_rad_s,
                "argument_of_perigee_semicircles": record.argument_of_perigee_semicircles,
                "argument_of_perigee_rad": record.argument_of_perigee_rad,
                "mean_anomaly_semicircles": record.mean_anomaly_semicircles,
                "mean_anomaly_rad": record.mean_anomaly_rad,
                "af0_s": record.af0_s,
                "af1_s_s": record.af1_s_s,
                "iod": record.iod,
                "t0a_s": record.t0a_s,
                "wna_mod4": record.wna_mod4,
                "status_e5a": record.status_e5a,
                "status_e5b": record.status_e5b,
                "status_e1b": record.status_e1b,
                "healthy": record.healthy,
            }
            for record in almanac.records
        ],
        "runnable_promotion_allowed": almanac.issue_date_utc is not None,
        "authority_note": almanac.authority_note,
    }
    if content_text is not None:
        payload["content_text"] = content_text
    return payload


def _source(root: Path, request: GalileoGscAuthorityRequest):
    source = load_scenario(root / request.source_scenario_name)
    template = next(
        (item for item in source.constellation.satellites if item.satellite_id == request.template_satellite_id),
        None,
    )
    if template is None:
        raise ValueError(f"unknown template satellite_id: {request.template_satellite_id}")
    if not source.orekit_sidecar_url:
        raise ValueError(
            "selected modelling-authority scenario has no orekit_sidecar_url; choose an explicit Orekit authority"
        )
    if source.time_scale != TimeScaleName.UTC:
        raise ValueError(
            "Galileo GSC promotion currently requires a UTC modelling authority so XML issueDate remains an exact absolute epoch"
        )
    return source, template


def _selected_records(almanac: GalileoGscAlmanac, request: GalileoGscAuthorityRequest):
    records = {record.svid: record for record in almanac.records}
    if request.selection == "all":
        chosen = list(records)
    elif request.selection == "healthy-only":
        chosen = [svid for svid, record in records.items() if record.healthy]
    else:
        chosen = list(request.svids)
    deduplicated: list[int] = []
    for svid in chosen:
        if svid not in deduplicated:
            deduplicated.append(svid)
    if not deduplicated:
        raise ValueError("Galileo satellite selection is empty")
    unknown = [svid for svid in deduplicated if svid not in records]
    if unknown:
        raise ValueError("unknown Galileo GSC SVID(s): " + ", ".join(str(svid) for svid in unknown))
    return tuple(records[svid] for svid in deduplicated)


def _authority_batch(root: Path, request: GalileoGscAuthorityRequest):
    almanac = parse_galileo_gsc_almanac(request.filename, request.content_text)
    if almanac.issue_date_utc is None:
        raise ValueError("Galileo GSC runnable promotion requires XML header issueDate")
    source, template = _source(root, request)
    records = _selected_records(almanac, request)
    client = OrekitGalileoAlmanacMeanConversionClient(source.orekit_sidecar_url)
    converted = []
    for record in records:
        result = client.convert(
            source_name=almanac.source_filename,
            source_text=request.content_text,
            svid=record.svid,
            frame=source.frame,
            target_epoch=almanac.issue_date_utc,
            target_time_scale=TimeScaleName.UTC,
            spacecraft=template.spacecraft,
            force_model=source.force_model,
        )
        converted.append((record, result))
    return almanac, source, template, tuple(converted)


def preview_galileo_gsc_authority(root: Path, request: GalileoGscAuthorityRequest) -> dict[str, object]:
    almanac, source, template, converted = _authority_batch(root, request)
    return {
        "valid": True,
        "source_scenario_id": source.scenario_id,
        "source_config_hash": source.config_hash(),
        "source_filename": almanac.source_filename,
        "source_sha256": almanac.source_sha256,
        "issue_date_utc": almanac.issue_date_utc.isoformat() if almanac.issue_date_utc else None,
        "template_satellite_id": template.satellite_id,
        "selection": request.selection,
        "selected_svids": [record.svid for record, _result in converted],
        "satellite_count": len(converted),
        "converted_records": [
            {
                "svid": record.svid,
                "mean_orbit": result.mean_orbit.model_dump(mode="json"),
                "backend_metadata": result.backend_metadata,
            }
            for record, result in converted
        ],
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


def create_galileo_gsc_scenario(root: Path, request: GalileoGscCreateRequest) -> dict[str, object]:
    almanac, source, template, converted = _authority_batch(root, request)
    if request.new_scenario_id == source.scenario_id:
        raise ValueError("new_scenario_id must differ from modelling-authority scenario_id")
    if almanac.issue_date_utc is None:
        raise ValueError("Galileo GSC runnable promotion requires XML header issueDate")
    target = _target(root, request.target_scenario_name)
    satellites = tuple(
        SatelliteSpec(
            satellite_id=f"GAL-{record.svid:02d}",
            plane_id="ALMANAC-UNASSIGNED",
            role="reference",
            mean_orbit=result.mean_orbit,
            spacecraft=template.spacecraft,
        )
        for record, result in converted
    )
    constellation = ConstellationSpec(satellites=satellites, planes=())
    svids = [record.svid for record, _result in converted]
    authority = converted[0][1].backend_metadata.get(
        "source_authority", "GALILEO-GSC-ALMANAC-OREKIT-GNSS"
    )
    digital_twin = DigitalTwinConfig(
        lineage=ScenarioLineage(
            parent_scenario_id=source.scenario_id,
            parent_config_hash=source.config_hash(),
            transformation="import",
            random_seed=None,
            source_type="mixed_gnss_almanac",
            source_name=almanac.source_filename,
            source_sha256=almanac.source_sha256,
            source_record_id="GAL:" + ",".join(str(svid) for svid in svids),
            authority=authority,
        )
    )
    child = ScenarioConfig.model_validate(
        source.model_dump(mode="json")
        | {
            "scenario_id": request.new_scenario_id,
            "epoch": almanac.issue_date_utc.isoformat(),
            "time_scale": TimeScaleName.UTC.value,
            "constellation": constellation.model_dump(mode="json"),
            "maneuvers": [],
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
        "parent_scenario_id": source.scenario_id,
        "parent_config_hash": source.config_hash(),
        "child_config_hash": child.config_hash(),
        "source_filename": almanac.source_filename,
        "source_sha256": almanac.source_sha256,
        "issue_date_utc": almanac.issue_date_utc.isoformat(),
        "selection": request.selection,
        "selected_svids": svids,
        "satellite_count": len(svids),
        "template_satellite_id": template.satellite_id,
        "backend_metadata": converted[0][1].backend_metadata,
    }


GALILEO_GSC_CARD = r"""
<div class="card" id="galileoGscCard">
  <h3>Galileo GSC Almanac → runnable ScenarioConfig</h3>
  <p class="hint">Official GSC XML является orbital data authority. Online сначала проверяет официальный direct daily XML вида /sites/default/files/sites/all/files/YYYY-MM-DD.xml, затем product index. После проверки можно выбрать modelling authority и КА-шаблон, загрузить все/healthy/выбранные Galileo SVID и получить полноценный ScenarioConfig: GSC XML → Orekit GalileoAlmanac → GNSS propagator@issueDate → osculating PV → DSST mean. Старая эпоха modelling authority не переносится.</p>
  <div class="grid">
    <button onclick="fetchGalileoGscOnline()">Загрузить GSC online / Fetch GSC</button>
    <label>Offline XML <input id="galileoGscFile" type="file" accept=".xml,text/xml,application/xml"></label>
  </div>
  <button onclick="previewGalileoGscOffline()">Прочитать XML / Read XML</button>
  <div class="grid">
    <label>Modelling authority<select id="galileoGscAuthority" onchange="loadGalileoGscAuthority()"><option value="">— выберите явно —</option></select></label>
    <label>Шаблон КА / Spacecraft template<select id="galileoGscTemplate"></select></label>
    <label>Выбор КА<select id="galileoGscSelection" onchange="syncGalileoGscSelection()"><option value="healthy-only">Только healthy / Healthy only</option><option value="all">Все записи / All records</option><option value="selected">Выбранные SVID / Selected</option></select></label>
    <label>SVID<select id="galileoGscSvids" multiple size="8" disabled></select></label>
  </div>
  <button onclick="previewGalileoGscAuthority()">Проверить выбранные КА через Orekit</button>
  <label>Новый scenario_id <input id="galileoGscScenarioId" type="text" placeholder="galileo-gsc-current"></label>
  <label>Новый YAML <input id="galileoGscScenarioFile" type="text" placeholder="galileo-gsc-current.yaml"></label>
  <button onclick="createGalileoGscScenario()">Создать полноценный сценарий / Create runnable scenario</button>
  <div id="galileoGscStatus" class="status"></div>
  <pre id="galileoGscPreview"></pre>
</div>
"""


GALILEO_GSC_SCRIPT = r"""
let galileoGscLast=null;
function galileoGscStatusMsg(text,kind=''){galileoGscStatus.textContent=text;galileoGscStatus.className='status '+kind;}
function syncGalileoGscSelection(){galileoGscSvids.disabled=galileoGscSelection.value!=='selected';}
function syncGalileoGscAuthorityOptions(){const names=(typeof catalog!=='undefined'&&catalog&&catalog.scenarios)||[];const previous=galileoGscAuthority.value;galileoGscAuthority.replaceChildren(new Option('— выберите явно —',''),...names.map(x=>new Option(x,x)));if(previous&&names.includes(previous))galileoGscAuthority.value=previous;}
async function loadGalileoGscAuthority(){const name=galileoGscAuthority.value;galileoGscTemplate.replaceChildren();if(!name)return;try{const r=await fetch('/api/scenarios/'+encodeURIComponent(name));const d=await r.json();if(!r.ok)throw new Error(d.detail||'scenario load failed');const normalized=d.normalized||d;if(!normalized.orekit_sidecar_url)throw new Error('Выбранный modelling authority не содержит orekit_sidecar_url');if(normalized.time_scale!=='UTC')throw new Error('Для GSC promotion сейчас требуется UTC modelling authority');const sats=(normalized.constellation||{}).satellites||[];galileoGscTemplate.replaceChildren(...sats.map(s=>new Option(s.satellite_id,s.satellite_id)));galileoGscStatusMsg('AUTHORITY READY: '+name,'ok');}catch(e){galileoGscAuthority.value='';galileoGscTemplate.replaceChildren();galileoGscStatusMsg(String(e.message||e),'danger');}}
function showGalileoGsc(d){
  galileoGscLast=d;syncGalileoGscAuthorityOptions();
  galileoGscSvids.replaceChildren(...(d.records||[]).map(r=>new Option('E'+String(r.svid).padStart(2,'0')+(r.healthy?' healthy':''),String(r.svid))));
  const lines=[];
  lines.push('source='+(d.source_url||d.source_filename));lines.push('sha256='+d.source_sha256);lines.push('issueDate='+(d.issue_date_utc||'—'));lines.push('records='+d.record_count);lines.push('authority='+d.authority_note);lines.push('');
  for(const r of d.records.slice(0,12))lines.push(`E${String(r.svid).padStart(2,'0')} a=${r.semi_major_axis_m}m e=${r.eccentricity} i=${r.inclination_rad}rad Ω=${r.raan_rad}rad M=${r.mean_anomaly_rad}rad health(E1B/E5a/E5b)=${r.status_e1b}/${r.status_e5a}/${r.status_e5b}`);
  if(d.records.length>12)lines.push('...');galileoGscPreview.textContent=lines.join('\n');
}
async function fetchGalileoGscOnline(){galileoGscStatusMsg('Загрузка GSC direct daily / index…');const r=await fetch('/api/galileo-gsc/online');const d=await r.json();if(!r.ok){galileoGscStatusMsg(d.detail||'GSC fetch failed','danger');return;}showGalileoGsc(d);galileoGscStatusMsg('VALID ONLINE: '+d.record_count+' Galileo records','ok');}
async function previewGalileoGscOffline(){const file=galileoGscFile.files&&galileoGscFile.files[0];if(!file){galileoGscStatusMsg('Выберите XML / Select XML','danger');return;}const text=await file.text();const r=await fetch('/api/galileo-gsc/offline-preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({filename:file.name,content_text:text})});const d=await r.json();if(!r.ok){galileoGscStatusMsg(d.detail||'GSC XML parse failed','danger');return;}showGalileoGsc(d);galileoGscStatusMsg('VALID OFFLINE: '+d.record_count+' Galileo records','ok');}
function galileoGscPayload(create=false){if(!galileoGscLast||!galileoGscLast.content_text)throw new Error('Сначала загрузите GSC XML');if(!galileoGscAuthority.value)throw new Error('Выберите modelling authority');if(!galileoGscTemplate.value)throw new Error('Выберите spacecraft template');const p={filename:galileoGscLast.source_filename,content_text:galileoGscLast.content_text,source_scenario_name:galileoGscAuthority.value,template_satellite_id:galileoGscTemplate.value,selection:galileoGscSelection.value,svids:Array.from(galileoGscSvids.selectedOptions).map(o=>Number(o.value))};if(create){p.new_scenario_id=galileoGscScenarioId.value.trim();p.target_scenario_name=galileoGscScenarioFile.value.trim();if(!p.new_scenario_id||!p.target_scenario_name)throw new Error('Укажите новый scenario_id и YAML');}return p;}
async function previewGalileoGscAuthority(){try{galileoGscStatusMsg('Orekit Galileo authority…');const r=await fetch('/api/galileo-gsc/authority-preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(galileoGscPayload(false))});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Galileo authority failed');galileoGscPreview.textContent=JSON.stringify(d,null,2);galileoGscStatusMsg('AUTHORITY VALID: '+d.satellite_count+' Galileo satellites','ok');}catch(e){galileoGscStatusMsg(String(e.message||e),'danger');}}
async function createGalileoGscScenario(){try{galileoGscStatusMsg('Создание Galileo ScenarioConfig…');const r=await fetch('/api/galileo-gsc/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(galileoGscPayload(true))});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Galileo scenario creation failed');const c=await fetch('/api/scenarios');catalog=await c.json();scenario.replaceChildren(...catalog.scenarios.map(x=>new Option(x,x)));scenario.value=d.scenario_name;await loadScenario();galileoGscStatusMsg('RUNNABLE READY: '+d.scenario_name+'; satellites='+d.satellite_count,'ok');}catch(e){galileoGscStatusMsg(String(e.message||e),'danger');}}
"""


def install_galileo_gsc_routes(app: FastAPI, scenario_root: Path = Path("scenarios")) -> None:
    @app.get("/api/galileo-gsc/source")
    def source() -> dict[str, str]:
        return {"index_url": GSC_ALMANAC_INDEX_URL, "daily_file_prefix": GSC_DAILY_FILE_PREFIX}

    @app.get("/api/galileo-gsc/online")
    def online() -> dict[str, object]:
        try:
            almanac = fetch_latest_galileo_gsc_almanac()
            if not almanac.source_url:
                raise ValueError("online GSC almanac omitted source URL")
            content_text = _fetch_text(almanac.source_url, 20.0)
            verified = parse_galileo_gsc_almanac(
                almanac.source_filename, content_text, source_url=almanac.source_url
            )
            if verified.source_sha256 != almanac.source_sha256:
                raise ValueError("GSC XML changed between discovery and exact-content fetch")
            return _payload(verified, content_text=content_text)
        except (ValueError, TypeError, OSError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/galileo-gsc/offline-preview")
    def offline_preview(request: GalileoGscOfflineRequest) -> dict[str, object]:
        try:
            return _payload(
                parse_galileo_gsc_almanac(request.filename, request.content_text),
                content_text=request.content_text,
            )
        except (ValueError, TypeError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/galileo-gsc/authority-preview")
    def authority_preview(request: GalileoGscAuthorityRequest) -> dict[str, object]:
        try:
            return preview_galileo_gsc_authority(scenario_root, request)
        except (ValueError, TypeError, RuntimeError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/galileo-gsc/create")
    def create(request: GalileoGscCreateRequest) -> dict[str, object]:
        try:
            return create_galileo_gsc_scenario(scenario_root, request)
        except (ValueError, TypeError, RuntimeError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
