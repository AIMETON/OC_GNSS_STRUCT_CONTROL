from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, model_validator

from constellation_control.adapters.iac_glonass_almanac import normalize_iac_glonass_almanac
from constellation_control.adapters.iac_glonass_authority_bridge import (
    IacGlonassAuthoritySupplement,
    iac_glonass_to_authority_record,
)
from constellation_control.adapters.iac_gnss_tables import IacDataset, fetch_iac_table, parse_iac_text
from constellation_control.adapters.orekit.mean_conversion import OrekitGlonassAlmanacMeanConversionClient
from constellation_control.application.run import load_scenario
from constellation_control.domain.digital_twin import DigitalTwinConfig, ScenarioLineage
from constellation_control.domain.models import ConstellationSpec, SatelliteSpec, ScenarioConfig


class IacGlonassConstellationRequest(BaseModel):
    source_mode: Literal["online", "offline"]
    filename: str | None = None
    content_text: str | None = None
    modelling_authority_scenario_name: str
    template_satellite_id: str | None = None
    health: int
    glo_to_utc_s: float
    gps_to_glo_s: float
    glo_time_offset_s: float
    supplement_confirmed: bool
    target_scenario_name: str
    new_scenario_id: str

    @model_validator(mode="after")
    def validate_supplement(self) -> IacGlonassConstellationRequest:
        if self.health < 0:
            raise ValueError("health must be non-negative")
        if not self.supplement_confirmed:
            raise ValueError("supplementary GLONASS time/health authority must be explicitly confirmed")
        if not self.modelling_authority_scenario_name:
            raise ValueError("explicit modelling authority scenario is required")
        return self


def _table(request: IacGlonassConstellationRequest):
    if request.source_mode == "online":
        return fetch_iac_table(IacDataset.GLONASS_ALMANAC)
    if not request.filename or Path(request.filename).name != request.filename:
        raise ValueError("offline IAC GLONASS source requires a plain source filename")
    if request.content_text is None or not request.content_text.strip():
        raise ValueError("offline IAC GLONASS source is empty")
    return parse_iac_text(IacDataset.GLONASS_ALMANAC, request.content_text)


def _safe_existing_scenario(root: Path, name: str) -> Path:
    if not name or Path(name).name != name or not name.lower().endswith((".yaml", ".yml")):
        raise ValueError("modelling authority must be a .yaml/.yml file name without path components")
    root = root.resolve()
    source = (root / name).resolve()
    if source.parent != root or not source.is_file():
        raise ValueError(f"modelling authority scenario does not exist: {name}")
    return source


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


def build_iac_glonass_constellation(
    root: Path,
    request: IacGlonassConstellationRequest,
) -> dict[str, object]:
    source = load_scenario(_safe_existing_scenario(root, request.modelling_authority_scenario_name))
    if request.new_scenario_id == source.scenario_id:
        raise ValueError("new_scenario_id must differ from modelling authority scenario_id")
    if not source.constellation.satellites:
        raise ValueError("modelling authority scenario contains no spacecraft template")
    if not source.orekit_sidecar_url:
        raise ValueError("selected modelling authority has no orekit_sidecar_url")

    if request.template_satellite_id:
        template = next(
            (item for item in source.constellation.satellites if item.satellite_id == request.template_satellite_id),
            None,
        )
        if template is None:
            raise ValueError(f"unknown template_satellite_id in modelling authority: {request.template_satellite_id}")
    else:
        template = source.constellation.satellites[0]

    table = _table(request)
    almanac = normalize_iac_glonass_almanac(table)
    if not almanac.records:
        raise ValueError("IAC GLONASS almanac contains no records")
    supplement = IacGlonassAuthoritySupplement(
        health=request.health,
        glo_to_utc_s=request.glo_to_utc_s,
        gps_to_glo_s=request.gps_to_glo_s,
        glo_time_offset_s=request.glo_time_offset_s,
    )
    source_name = table.source_url or request.filename or "iac-glonass-offline"
    client = OrekitGlonassAlmanacMeanConversionClient(source.orekit_sidecar_url)
    satellites: list[SatelliteSpec] = []
    satellite_ids: list[str] = []

    for iac_record in almanac.records:
        authority_record = iac_glonass_to_authority_record(iac_record, supplement)
        result = client.convert(
            source_name=source_name,
            slot=authority_record.slot,
            frequency_channel=authority_record.frequency_channel,
            health=authority_record.health,
            reference_date=authority_record.reference_date,
            reference_time_s=authority_record.reference_time_s,
            lambda_rad=authority_record.lambda_rad,
            delta_i_rad=authority_record.delta_i_rad,
            argument_of_perigee_rad=authority_record.argument_of_perigee_rad,
            eccentricity=authority_record.eccentricity,
            delta_t_s=authority_record.delta_t_s,
            delta_t_dot=authority_record.delta_t_dot,
            glo_to_utc_s=authority_record.glo_to_utc_s,
            gps_to_glo_s=authority_record.gps_to_glo_s,
            glo_time_offset_s=authority_record.glo_time_offset_s,
            frame=source.frame,
            target_epoch=source.epoch,
            target_time_scale=source.time_scale,
            spacecraft=template.spacecraft,
            force_model=source.force_model,
        )
        if result.backend_metadata.get("glonass_slot") != str(iac_record.slot):
            raise RuntimeError(f"Orekit GLONASS authority returned a different slot for {iac_record.slot}")
        satellite_id = f"GLO-{iac_record.slot:02d}"
        satellite_ids.append(satellite_id)
        satellites.append(
            SatelliteSpec(
                satellite_id=satellite_id,
                plane_id="ALMANAC-UNASSIGNED",
                role="reference",
                mean_orbit=result.mean_orbit,
                spacecraft=template.spacecraft,
            )
        )

    target = _target(root, request.target_scenario_name)
    prior_twin = source.digital_twin or DigitalTwinConfig()
    authority = (
        "GLONASS-ALMANAC-OREKIT-ANALYTICAL; full IAC constellation import; "
        f"health={request.health}; tGlo2UTC={request.glo_to_utc_s}; "
        f"tGPS2Glo={request.gps_to_glo_s}; tGlo={request.glo_time_offset_s}; "
        f"modelling_authority={request.modelling_authority_scenario_name}; "
        "supplement values explicitly confirmed by operator; physical plane assignment intentionally not inferred"
    )
    digital_twin = prior_twin.model_copy(
        update={
            "lineage": ScenarioLineage(
                parent_scenario_id=source.scenario_id,
                parent_config_hash=source.config_hash(),
                transformation="glonass_almanac_import",
                random_seed=None,
                source_type="glonass_authority_v1",
                source_name=source_name,
                source_sha256=table.source_sha256,
                source_record_id=f"GLO:{len(satellite_ids)}",
                authority=authority,
            )
        }
    )
    constellation = ConstellationSpec(satellites=tuple(satellites), planes=())
    child = ScenarioConfig.model_validate(
        source.model_dump(mode="json")
        | {
            "scenario_id": request.new_scenario_id,
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
        "satellite_count": len(satellite_ids),
        "satellite_ids": satellite_ids,
        "source_mode": request.source_mode,
        "source_url": table.source_url,
        "source_filename": request.filename if request.source_mode == "offline" else None,
        "source_sha256": table.source_sha256,
        "modelling_authority_scenario_name": request.modelling_authority_scenario_name,
        "template_satellite_id": template.satellite_id,
        "plane_assignment": "ALMANAC-UNASSIGNED",
    }


IAC_GLONASS_CONSTELLATION_CARD = r"""
<div class="card" id="iacGlonassConstellationCard">
  <h3>ИАЦ GLONASS → полная runnable группировка</h3>
  <p class="hint">Источник данных ИАЦ и физическая modelling authority разделены. Альманах задаёт орбитальную data authority; выбранный ScenarioConfig задаёт force model, frame/time scale, Orekit и шаблон КА. Активный сценарий не используется скрыто.</p>
  <div class="grid">
    <label>Источник / Source <select id="iacGloConstMode"><option value="online">Online IAC</option><option value="offline">Offline TXT/TSV</option></select></label>
    <label>Offline TXT/TSV <input id="iacGloConstFile" type="file" accept=".txt,.tsv,.csv"></label>
  </div>
  <label>Физическая модель / Modelling authority
    <select id="iacGloConstAuthority"><option value="">— выберите явно —</option></select>
  </label>
  <div class="grid"><label>Health <input id="iacGloConstHealth" type="number" min="0" value="0"></label><label>GLO→UTC, s <input id="iacGloConstUtc" type="number" step="any" value="0"></label></div>
  <div class="grid"><label>GPS→GLO, s <input id="iacGloConstGpsGlo" type="number" step="any" value="0"></label><label>GLO time offset, s <input id="iacGloConstOffset" type="number" step="any" value="0"></label></div>
  <label><input id="iacGloConstConfirm" type="checkbox"> Подтверждаю supplementary time/health authority значения. Значения 0 — явный orbital-only профиль.</label>
  <label>Новый scenario_id <input id="iacGloConstScenarioId" type="text" value="iac-glonass-current"></label>
  <label>Новый YAML <input id="iacGloConstScenarioFile" type="text" value="iac-glonass-current.yaml"></label>
  <button id="iacGloConstBuildButton" onclick="buildIacGlonassConstellation()">Собрать все КА ИАЦ / Build full GLONASS constellation</button>
  <pre id="iacGloConstResult"></pre><div id="iacGloConstStatusBox" class="status"></div>
</div>
"""

IAC_GLONASS_CONSTELLATION_SCRIPT = r"""
function syncIacGloConstAuthority(){
  if(typeof catalog==='undefined'||!catalog||!Array.isArray(catalog.scenarios))return;
  const previous=iacGloConstAuthority.value;
  const opts=[(()=>{const o=document.createElement('option');o.value='';o.textContent='— выберите явно —';return o;})(),...catalog.scenarios.map(x=>{const o=document.createElement('option');o.value=x;o.textContent=x;return o;})];
  iacGloConstAuthority.replaceChildren(...opts);
  if(previous&&catalog.scenarios.includes(previous))iacGloConstAuthority.value=previous;
}
function setIacGloConstStatus(t,k=''){const el=document.getElementById('iacGloConstStatusBox');if(!el)return;el.textContent=t;el.className='status '+k;}
async function buildIacGlonassConstellation(){
 if(!iacGloConstConfirm.checked){setIacGloConstStatus('Подтвердите supplementary time/health authority значения','danger');return false;}
 const authority=iacGloConstAuthority.value;
 if(!authority){setIacGloConstStatus('Явно выберите modelling authority ScenarioConfig','danger');return false;}
 let filename=null,content_text=null;const mode=iacGloConstMode.value;
 try{
   if(mode==='offline'){
     let f=iacGloConstFile.files&&iacGloConstFile.files[0];
     if(!f&&typeof iacGnssFile!=='undefined')f=iacGnssFile.files&&iacGnssFile.files[0];
     if(!f)throw new Error('Выберите offline IAC GLONASS файл');
     filename=f.name;content_text=await f.text();
   }
   const p={source_mode:mode,filename,content_text,modelling_authority_scenario_name:authority,template_satellite_id:null,health:Number(iacGloConstHealth.value),glo_to_utc_s:Number(iacGloConstUtc.value),gps_to_glo_s:Number(iacGloConstGpsGlo.value),glo_time_offset_s:Number(iacGloConstOffset.value),supplement_confirmed:true,new_scenario_id:iacGloConstScenarioId.value.trim(),target_scenario_name:iacGloConstScenarioFile.value.trim()};
   if(!p.new_scenario_id||!p.target_scenario_name)throw new Error('Задайте новый scenario_id и YAML');
   const button=document.getElementById('iacGloConstBuildButton');if(button)button.disabled=true;
   setIacGloConstStatus('ИАЦ data authority → Orekit modelling authority → ScenarioConfig…');
   const r=await fetch('/api/iac-glonass-constellation/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});
   let d;try{d=await r.json();}catch(_){throw new Error('Сервер вернул нечитаемый ответ при создании сценария');}
   if(!r.ok)throw new Error(d.detail||'GLONASS constellation build failed');
   iacGloConstResult.textContent=JSON.stringify(d,null,2);
   const c=await fetch('/api/scenarios');if(!c.ok)throw new Error('Сценарий создан, но список сценариев не удалось обновить');
   catalog=await c.json();scenario.replaceChildren(...catalog.scenarios.map(x=>{const o=document.createElement('option');o.value=x;o.textContent=x;return o;}));
   syncIacGloConstAuthority();scenario.value=d.scenario_name;await loadScenario();
   setIacGloConstStatus('RUNNABLE: '+d.scenario_name+'; satellites='+d.satellite_count+'; sha256='+d.source_sha256,'ok');return true;
 }catch(e){setIacGloConstStatus(String(e.message||e),'danger');return false;}
 finally{const button=document.getElementById('iacGloConstBuildButton');if(button)button.disabled=false;}
}
function installIacGloIntakeBridge(){
 syncIacGloConstAuthority();
 const card=document.getElementById('iacGnssCard');if(!card||document.getElementById('iacGloPromoteAll'))return;
 const b=document.createElement('button');b.id='iacGloPromoteAll';b.textContent='→ Собрать GLONASS-сценарий из альманаха / Build GLONASS scenario';
 b.onclick=()=>{if(iacGnssDataset.value!=='glonass-almanac'){iacStatus('Выберите GLONASS — альманах','danger');return;}iacGloConstMode.value=iacGnssStatus.textContent.includes('OFFLINE')?'offline':'online';document.getElementById('iacGlonassConstellationCard').scrollIntoView({behavior:'smooth',block:'start'});setIacGloConstStatus('Источник ИАЦ выбран. Теперь явно выберите modelling authority, подтвердите time/health и запустите сборку.');};
 card.appendChild(b);
}
"""


def install_iac_glonass_constellation_routes(app: FastAPI, scenario_root: Path = Path("scenarios")) -> None:
    @app.post("/api/iac-glonass-constellation/create")
    def create(request: IacGlonassConstellationRequest) -> dict[str, object]:
        try:
            return build_iac_glonass_constellation(scenario_root, request)
        except (ValueError, TypeError, RuntimeError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
