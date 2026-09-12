from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from constellation_control.adapters.orekit.mean_conversion import OrekitRinexGlonassMeanConversionClient
from constellation_control.application.run import load_scenario
from constellation_control.domain.digital_twin import DigitalTwinConfig, ScenarioLineage
from constellation_control.domain.models import ConstellationSpec, SatelliteSpec, ScenarioConfig
from constellation_control.preview.source_runtime import fetch_selected_broadcast_rinex


class GlonassRinexProbeRequest(BaseModel):
    source_date: date


class GlonassRinexRunnerRequest(GlonassRinexProbeRequest):
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


def _orekit_safe_rinex_text(path: Path) -> str:
    """Return parser-safe ASCII while preserving source byte-column geometry.

    Some reviewed providers place UTF-8/legacy national characters in RINEX header
    comments/agency metadata. RINEX navigation records themselves remain ASCII and
    fixed-column. Replace only high-bit header bytes with spaces (one byte -> one
    space), preserving every column position. Non-ASCII bytes after END OF HEADER
    are rejected fail-closed because changing navigation records would alter data.
    """

    raw = path.read_bytes()
    marker = raw.find(b"END OF HEADER")
    if marker < 0:
        raise ValueError(f"{path.name}: RINEX header has no END OF HEADER marker")
    newline = raw.find(b"\n", marker)
    header_end = len(raw) if newline < 0 else newline + 1
    header = raw[:header_end]
    body = raw[header_end:]
    try:
        body_text = body.decode("ascii", errors="strict")
    except UnicodeDecodeError as exc:
        absolute_offset = header_end + exc.start
        raise ValueError(
            f"{path.name}: non-ASCII byte in RINEX navigation records at byte {absolute_offset}; "
            "only non-ASCII header metadata may be sanitized"
        ) from exc
    safe_header = bytes(value if value < 0x80 else 0x20 for value in header)
    return safe_header.decode("ascii", errors="strict") + body_text


def _validated_target_epoch(request: GlonassRinexRunnerRequest) -> datetime:
    target_epoch = request.target_epoch
    if target_epoch.tzinfo is None or target_epoch.utcoffset() is None:
        raise ValueError("target_epoch must include an explicit UTC offset, for example 2026-09-11T12:00:00Z")
    target_utc = target_epoch.astimezone(UTC)
    if target_utc.date() != request.source_date:
        raise ValueError(
            "target_epoch UTC date must match source_date for daily RINEX authority: "
            f"source_date={request.source_date.isoformat()} target_epoch={target_utc.isoformat()}"
        )
    return target_utc


def probe_glonass_rinex_source(root: Path, request: GlonassRinexProbeRequest) -> dict[str, object]:
    selected = fetch_selected_broadcast_rinex(
        request.source_date,
        "GLONASS",
        root.parent / "data" / "cache" / "rinex",
    )
    cached = selected.cached
    return {
        "valid": True,
        "selected_source_id": selected.source_id,
        "source_attempts": [item.as_dict() for item in selected.attempts],
        "source_url": cached.source_url,
        "source_sha256": cached.source_sha256,
        "rinex_sha256": cached.rinex_sha256,
        "source_filename": cached.source_filename,
        "transport": cached.transport,
        "cached_rinex": str(cached.rinex_path),
        "cache_manifest": str(cached.manifest_path),
    }


def build_glonass_rinex_scenario(root: Path, request: GlonassRinexRunnerRequest) -> dict[str, object]:
    target_epoch = _validated_target_epoch(request)
    source = load_scenario(root / request.source_scenario_name)
    template = next(
        (s for s in source.constellation.satellites if s.satellite_id == request.template_satellite_id),
        None,
    )
    if template is None:
        raise ValueError(f"unknown template_satellite_id: {request.template_satellite_id}")
    if not source.orekit_sidecar_url:
        raise ValueError(
            "selected modelling-authority scenario has no orekit_sidecar_url; "
            "use the source probe to test/download RINEX independently, or select a DESIGN/VALIDATION scenario with Orekit"
        )
    if request.new_scenario_id == source.scenario_id:
        raise ValueError("new_scenario_id must differ from parent scenario_id")

    selected = fetch_selected_broadcast_rinex(
        request.source_date,
        "GLONASS",
        root.parent / "data" / "cache" / "rinex",
    )
    cached = selected.cached
    rinex_text = _orekit_safe_rinex_text(cached.rinex_path)
    result = OrekitRinexGlonassMeanConversionClient(source.orekit_sidecar_url).convert(
        source_name=cached.source_url,
        source_text=rinex_text,
        frame=source.frame,
        target_epoch=target_epoch,
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
            f"{selected.source_id} RINEX NAV -> Orekit RinexNavigationParser -> "
            "GLONASSNumericalPropagator -> DSST mean; "
            f"target_epoch={target_epoch.isoformat()}; "
            f"max_ephemeris_age_s={request.max_ephemeris_age_s}; "
            f"glonass_propagation_step_s={request.glonass_propagation_step_s}"
        ),
    )
    child = ScenarioConfig.model_validate(
        source.model_dump(mode="json")
        | {
            "scenario_id": request.new_scenario_id,
            "epoch": target_epoch.isoformat(),
            "constellation": ConstellationSpec(satellites=satellites, planes=()).model_dump(mode="json"),
            "maneuvers": [],
            "digital_twin": prior_twin.model_copy(update={"lineage": lineage}).model_dump(mode="json"),
        }
    )
    target = _target(root, request.target_scenario_name)
    target.write_text(
        yaml.safe_dump(child.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return {
        "saved": True,
        "runnable": True,
        "scenario_name": target.name,
        "scenario_id": child.scenario_id,
        "child_config_hash": child.config_hash(),
        "satellite_count": len(satellites),
        "selected_source_id": selected.source_id,
        "source_attempts": [item.as_dict() for item in selected.attempts],
        "source_url": cached.source_url,
        "source_sha256": cached.source_sha256,
        "rinex_sha256": cached.rinex_sha256,
        "cached_gzip": str(cached.gzip_path),
        "cached_rinex": str(cached.rinex_path),
        "cache_manifest": str(cached.manifest_path),
        "target_epoch": target_epoch.isoformat(),
    }


GLONASS_RINEX_CARD = """
<div class="card" id="glonassRinexRunnerCard">
<h3>RINEX NAV → GLONASS ScenarioConfig</h3>
<p class="hint">Источник берётся из Settings. AUTO реально перебирает совместимые источники и возвращает журнал попыток; MANUAL fail-closed. Российские источники идут перед BKG/WHU. Probe скачивает и валидирует RINEX независимо от Orekit. Modelling authority задаёт force model/frame/Orekit/spacecraft, но её старая эпоха не переносится: целевая эпоха относится к выбранным суткам RINEX.</p>
<div class="grid">
<label>Дата RINEX <input id="gloRinexDate" type="date" onchange="syncGlonassRinexEpochToDate(true)"></label>
<label>Modelling authority <select id="gloRinexAuthority" onchange="loadGlonassRinexAuthority()"><option value="">— выберите явно —</option></select></label>
<label>Шаблон КА authority <select id="gloRinexTemplate"></select></label>
<label>Целевая эпоха UTC <input id="gloRinexEpoch" type="text" placeholder="2026-09-11T12:00:00Z"></label>
<label>Max age, s <input id="gloRinexAge" type="number" value="7200"></label>
<label>GLONASS propagation step, s <input id="gloRinexStep" type="number" value="60"></label>
<label>Новый scenario_id <input id="gloRinexScenarioId" value="glonass-rinex-derived"></label>
<label>Новый YAML <input id="gloRinexFile" value="glonass-rinex-derived.yaml"></label>
</div>
<button type="button" id="gloRinexProbeBtn" onclick="probeGlonassRinex()">Проверить/скачать RINEX / Probe sources</button>
<button type="button" id="gloRinexBuildBtn" onclick="buildGlonassRinex()">Скачать RINEX и создать сценарий</button>
<pre id="gloRinexResult"></pre><div id="gloRinexStatus" class="status"></div>
</div>
"""

GLONASS_RINEX_SCRIPT = r"""
function glonassRinexErrorDetail(d){if(!d)return 'RINEX runner failed';if(typeof d.detail==='string')return d.detail;if(d.detail!==undefined)return JSON.stringify(d.detail);return JSON.stringify(d);}
function syncGlonassRinexEpochToDate(force=false){const day=gloRinexDate.value;if(!day)return;if(force||!gloRinexEpoch.value.trim())gloRinexEpoch.value=day+'T12:00:00Z';}
function syncGlonassRinexTemplate(){
 if(!gloRinexDate.value)gloRinexDate.value=new Date(Date.now()-86400000).toISOString().slice(0,10);
 syncGlonassRinexEpochToDate(false);
 const names=(typeof catalog!=='undefined'&&catalog&&catalog.scenarios)||[];
 const previous=gloRinexAuthority.value;
 gloRinexAuthority.replaceChildren(new Option('— выберите явно —',''),...names.map(x=>new Option(x,x)));
 const normalized=current&&(current.normalized||current);
 if(previous&&names.includes(previous))gloRinexAuthority.value=previous;
 else if(normalized&&normalized.orekit_sidecar_url&&names.includes(scenario.value))gloRinexAuthority.value=scenario.value;
 else gloRinexAuthority.value='';
 if(gloRinexAuthority.value)void loadGlonassRinexAuthority();
 else gloRinexTemplate.replaceChildren();
}
async function loadGlonassRinexAuthority(){
 const name=gloRinexAuthority.value;gloRinexTemplate.replaceChildren();if(!name)return;
 try{const r=await fetch('/api/scenarios/'+encodeURIComponent(name));const d=await r.json();if(!r.ok)throw new Error(glonassRinexErrorDetail(d));const normalized=d.normalized||d;if(!normalized.orekit_sidecar_url)throw new Error('Выбранный modelling authority не содержит orekit_sidecar_url');const sats=(normalized.constellation||{}).satellites||[];gloRinexTemplate.replaceChildren(...sats.map(s=>new Option(s.satellite_id,s.satellite_id)));syncGlonassRinexEpochToDate(false);gloRinexStatus.textContent='AUTHORITY READY: '+name+'; target epoch remains tied to RINEX date';gloRinexStatus.className='status ok';}
 catch(e){gloRinexAuthority.value='';gloRinexTemplate.replaceChildren();gloRinexStatus.textContent=String(e.message||e);gloRinexStatus.className='status danger';}
}
async function probeGlonassRinex(){
 const button=gloRinexProbeBtn;button.disabled=true;gloRinexStatus.textContent='RINEX source probe…';
 try{if(!gloRinexDate.value)throw new Error('Дата RINEX обязательна / RINEX date is required');const r=await fetch('/api/glonass-rinex-runner/probe',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source_date:gloRinexDate.value})});const d=await r.json();if(!r.ok)throw new Error(glonassRinexErrorDetail(d));gloRinexResult.textContent=JSON.stringify(d,null,2);gloRinexStatus.textContent='SOURCE VALID: '+d.selected_source_id+'; '+d.source_filename;gloRinexStatus.className='status ok';}
 catch(e){gloRinexStatus.textContent=String(e.message||e);gloRinexStatus.className='status danger';}
 finally{button.disabled=false;}
}
async function buildGlonassRinex(){
 const button=gloRinexBuildBtn;button.disabled=true;
 try{gloRinexStatus.textContent='RINEX download / conversion…';if(!gloRinexDate.value)throw new Error('Дата RINEX обязательна / RINEX date is required');if(!gloRinexAuthority.value)throw new Error('Выберите modelling authority явно');if(!gloRinexTemplate.value)throw new Error('Выберите шаблон КА authority');if(!gloRinexEpoch.value.trim())throw new Error('Целевая эпоха обязательна / target epoch is required');const p={source_date:gloRinexDate.value,source_scenario_name:gloRinexAuthority.value,template_satellite_id:gloRinexTemplate.value,target_epoch:gloRinexEpoch.value,max_ephemeris_age_s:Number(gloRinexAge.value),glonass_propagation_step_s:Number(gloRinexStep.value),target_scenario_name:gloRinexFile.value.trim(),new_scenario_id:gloRinexScenarioId.value.trim()};const r=await fetch('/api/glonass-rinex-runner/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});const d=await r.json();if(!r.ok)throw new Error(glonassRinexErrorDetail(d));gloRinexResult.textContent=JSON.stringify(d,null,2);gloRinexStatus.textContent='VALID: '+d.satellite_count+' GLONASS satellites; source='+d.selected_source_id;gloRinexStatus.className='status ok';}
 catch(e){gloRinexStatus.textContent=String(e.message||e);gloRinexStatus.className='status danger';}
 finally{button.disabled=false;}
}
"""


def install_glonass_rinex_runner_routes(app: FastAPI, scenario_root: Path) -> None:
    @app.post("/api/glonass-rinex-runner/probe")
    def probe(request: GlonassRinexProbeRequest) -> dict[str, object]:
        try:
            return probe_glonass_rinex_source(scenario_root, request)
        except (ValueError, RuntimeError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/glonass-rinex-runner/create")
    def create(request: GlonassRinexRunnerRequest) -> dict[str, object]:
        try:
            return build_glonass_rinex_scenario(scenario_root, request)
        except (ValueError, RuntimeError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
