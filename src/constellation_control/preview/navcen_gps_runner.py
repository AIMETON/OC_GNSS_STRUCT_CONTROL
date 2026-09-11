from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from constellation_control.adapters.gnss_almanac import GnssAlmanacFormat, preview_gnss_almanac
from constellation_control.adapters.orekit.mean_conversion import OrekitGpsAlmanacMeanConversionClient
from constellation_control.adapters.reviewed_http_fetch import fetch_reviewed_url
from constellation_control.application.run import load_scenario
from constellation_control.domain.digital_twin import DigitalTwinConfig, ScenarioLineage
from constellation_control.domain.models import ScenarioConfig
from constellation_control.preview.source_settings import resolve_source_url

NAVCEN_GPS_ALMANAC_URLS: dict[Literal["yuma", "sem"], str] = {
    "yuma": "https://www.navcen.uscg.gov/sites/default/files/gps/almanac/current_yuma.alm",
    "sem": "https://www.navcen.uscg.gov/sites/default/files/gps/almanac/current_sem.al3",
}


class NavcenGpsAuthorityRequest(BaseModel):
    source_format: Literal["yuma", "sem"]
    source_scenario_name: str
    satellite_id: str
    prn: int


class NavcenGpsCreateRequest(NavcenGpsAuthorityRequest):
    target_scenario_name: str
    new_scenario_id: str


def fetch_navcen_gps_almanac(
    source_format: Literal["yuma", "sem"], *, timeout_s: float = 20.0
) -> tuple[str, str, str]:
    default_url = NAVCEN_GPS_ALMANAC_URLS[source_format]
    url = resolve_source_url(f"navcen_gps_{source_format}", default_url)
    response = fetch_reviewed_url(url, timeout_s=timeout_s)
    raw = response.raw
    if not raw:
        raise ValueError(f"NAVCEN GPS almanac response is empty (transport={response.transport})")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("ascii")
    if "html" in response.content_type.lower() or "<html" in text[:256].lower() or "<!doctype html" in text[:256].lower():
        raise ValueError(
            "NAVCEN GPS almanac endpoint returned HTML instead of an almanac "
            f"(transport={response.transport})"
        )
    sha256 = hashlib.sha256(raw).hexdigest()
    return url, text, sha256


def _format(source_format: Literal["yuma", "sem"]) -> GnssAlmanacFormat:
    return GnssAlmanacFormat.GPS_YUMA if source_format == "yuma" else GnssAlmanacFormat.GPS_SEM


def _source_format(source_format: Literal["yuma", "sem"]) -> Literal["gps-yuma", "gps-sem"]:
    return "gps-yuma" if source_format == "yuma" else "gps-sem"


def _lineage_source_type(source_format: Literal["yuma", "sem"]) -> Literal["gps_yuma", "gps_sem"]:
    return "gps_yuma" if source_format == "yuma" else "gps_sem"


def _authority(root: Path, request: NavcenGpsAuthorityRequest):
    url, text, raw_sha256 = fetch_navcen_gps_almanac(request.source_format)
    filename = Path(url).name
    preview = preview_gnss_almanac(filename, text, _format(request.source_format))
    if preview.source_sha256 != raw_sha256:
        raise RuntimeError("NAVCEN GPS source hash changed during parsing")
    record = next((item for item in preview.records if getattr(item, "prn", None) == request.prn), None)
    if record is None:
        raise ValueError(f"unknown NAVCEN GPS PRN: {request.prn}")

    source = load_scenario(root / request.source_scenario_name)
    satellite = next(
        (item for item in source.constellation.satellites if item.satellite_id == request.satellite_id),
        None,
    )
    if satellite is None:
        raise ValueError(f"unknown satellite_id: {request.satellite_id}")
    if not source.orekit_sidecar_url:
        raise ValueError("selected scenario has no orekit_sidecar_url; GPS almanac authority is unavailable")

    result = OrekitGpsAlmanacMeanConversionClient(source.orekit_sidecar_url).convert(
        source_format=_source_format(request.source_format),
        source_name=url,
        source_text=text,
        prn=request.prn,
        frame=source.frame,
        target_epoch=source.epoch,
        target_time_scale=source.time_scale,
        spacecraft=satellite.spacecraft,
        force_model=source.force_model,
    )
    if result.backend_metadata.get("gps_prn") != str(request.prn):
        raise RuntimeError("Orekit NAVCEN GPS authority returned a different PRN")
    return url, preview, record, source, satellite, result


def preview_navcen_gps_authority(root: Path, request: NavcenGpsAuthorityRequest) -> dict[str, object]:
    url, preview, _record, source, satellite, result = _authority(root, request)
    return {
        "valid": True,
        "provider": "USCG NAVCEN",
        "source_url": url,
        "source_format": preview.source_format.value,
        "source_filename": preview.source_filename,
        "source_sha256": preview.source_sha256,
        "source_scenario_id": source.scenario_id,
        "source_config_hash": source.config_hash(),
        "satellite_id": satellite.satellite_id,
        "prn": request.prn,
        "records": len(preview.records),
        "target_scenario_epoch": source.epoch.isoformat(),
        "target_time_scale": source.time_scale.value,
        "mean_orbit": result.mean_orbit.model_dump(mode="json"),
        "backend_metadata": result.backend_metadata,
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


def create_navcen_gps_scenario(root: Path, request: NavcenGpsCreateRequest) -> dict[str, object]:
    url, preview, _record, source, satellite, result = _authority(root, request)
    if request.new_scenario_id == source.scenario_id:
        raise ValueError("new_scenario_id must differ from parent scenario_id")
    target = _target(root, request.target_scenario_name)
    satellites = tuple(
        item.model_copy(update={"mean_orbit": result.mean_orbit})
        if item.satellite_id == request.satellite_id
        else item
        for item in source.constellation.satellites
    )
    constellation = source.constellation.model_copy(update={"satellites": satellites})
    prior_twin = source.digital_twin or DigitalTwinConfig()
    digital_twin = prior_twin.model_copy(
        update={
            "lineage": ScenarioLineage(
                parent_scenario_id=source.scenario_id,
                parent_config_hash=source.config_hash(),
                transformation="gps_almanac_import",
                random_seed=None,
                source_type=_lineage_source_type(request.source_format),
                source_name=url,
                source_sha256=preview.source_sha256,
                source_record_id=str(request.prn),
                authority=result.backend_metadata.get("source_authority", "GPS-ALMANAC-OREKIT-GNSS")
                + "; USCG NAVCEN direct online source",
            )
        }
    )
    child = ScenarioConfig.model_validate(
        source.model_dump(mode="json")
        | {
            "scenario_id": request.new_scenario_id,
            "constellation": constellation.model_dump(mode="json"),
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
        "satellite_id": satellite.satellite_id,
        "prn": request.prn,
        "parent_scenario_id": source.scenario_id,
        "parent_config_hash": source.config_hash(),
        "child_config_hash": child.config_hash(),
        "provider": "USCG NAVCEN",
        "source_url": url,
        "source_format": preview.source_format.value,
        "source_sha256": preview.source_sha256,
        "backend_metadata": result.backend_metadata,
    }


def install_navcen_gps_runner_routes(app: FastAPI, scenario_root: Path) -> None:
    @app.post("/api/navcen-gps-runner/authority")
    def navcen_authority(request: NavcenGpsAuthorityRequest) -> dict[str, object]:
        try:
            return preview_navcen_gps_authority(scenario_root, request)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/navcen-gps-runner/create")
    def navcen_create(request: NavcenGpsCreateRequest) -> dict[str, object]:
        try:
            return create_navcen_gps_scenario(scenario_root, request)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=422, detail=str(exc)) from exc


NAVCEN_GPS_RUNNER_CARD = r"""
<div class="card" id="navcenGpsRunnerCard">
<h3>NAVCEN GPS YUMA/SEM → runnable scenario</h3>
<p class="hint">Прямой online authority source USCG NAVCEN: current YUMA/SEM → штатный Orekit GPS almanac parser/GNSS propagator → DSST mean → новый ScenarioConfig. URL берётся из меню Settings; HTML/error responses блокируют исходный файл; фиксируется SHA-256.</p>
<label>Формат / Format<select id="navcenGpsFormat"><option value="yuma">Current YUMA</option><option value="sem">Current SEM</option></select></label>
<label>PRN<input id="navcenGpsPrn" type="number" min="1" max="63" value="1"></label>
<label>КА сценария / Scenario satellite<select id="navcenGpsSat"></select></label>
<button type="button" onclick="navcenGpsPreview()">Скачать и проверить через Orekit / Fetch + preview</button>
<pre id="navcenGpsPreview"></pre>
<label>Новый scenario_id<input id="navcenGpsScenarioId" value="navcen-gps-derived-01"></label>
<label>Новый YAML<input id="navcenGpsFile" value="navcen-gps-derived-01.yaml"></label>
<button type="button" onclick="navcenGpsCreate()">Собрать runnable scenario / Build runnable scenario</button>
<div id="navcenGpsStatus" class="status"></div>
</div>
"""

NAVCEN_GPS_RUNNER_SCRIPT = r"""
function syncNavcenGpsSatellites(){if(typeof current==='undefined'||!current)return;navcenGpsSat.replaceChildren(...current.satellites.map(x=>new Option(x.satellite_id,x.satellite_id)));}
async function navcenGpsPayload(){return {source_format:navcenGpsFormat.value,source_scenario_name:scenario.value,satellite_id:navcenGpsSat.value,prn:Number(navcenGpsPrn.value)};}
async function navcenGpsPreview(){navcenGpsStatus.textContent='Loading NAVCEN…';try{const r=await fetch('/api/navcen-gps-runner/authority',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(await navcenGpsPayload())});const d=await r.json();if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail||d));navcenGpsPreview.textContent=JSON.stringify(d,null,2);navcenGpsStatus.textContent='READY';navcenGpsStatus.className='status ok';}catch(e){navcenGpsStatus.textContent=String(e);navcenGpsStatus.className='status danger';}}
async function navcenGpsCreate(){navcenGpsStatus.textContent='Building…';try{const p=await navcenGpsPayload();p.new_scenario_id=navcenGpsScenarioId.value.trim();p.target_scenario_name=navcenGpsFile.value.trim();const r=await fetch('/api/navcen-gps-runner/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});const d=await r.json();if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail||d));const c=await fetch('/api/scenarios');catalog=await c.json();scenario.replaceChildren(...catalog.scenarios.map(x=>new Option(x,x)));scenario.value=d.scenario_name;await loadScenario();navcenGpsStatus.textContent='RUNNABLE: '+d.scenario_name;navcenGpsStatus.className='status ok';}catch(e){navcenGpsStatus.textContent=String(e);navcenGpsStatus.className='status danger';}}
"""
