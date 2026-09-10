from __future__ import annotations

from pathlib import Path
from typing import cast

from fastapi import FastAPI

from constellation_control.application.run import load_scenario
from constellation_control.domain.models import ForceMode
from constellation_control.preview.base_preview_shell import preview_catalog


def mission_modelling_templates(scenario_root: Path) -> dict[str, object]:
    root = scenario_root.resolve()
    names = cast(list[str], preview_catalog(root)["scenarios"])
    candidates: list[dict[str, object]] = []
    for name in names:
        try:
            scenario = load_scenario(root / name)
        except (ValueError, TypeError, OSError):
            continue
        if not scenario.orekit_sidecar_url or not scenario.constellation.satellites:
            continue
        if scenario.force_model.mode not in {ForceMode.DESIGN, ForceMode.VALIDATION}:
            continue
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
            }
        )
    candidates.sort(
        key=lambda item: (
            0 if item["force_mode"] == ForceMode.DESIGN.value else 1,
            str(item["scenario_name"]),
        )
    )
    recommended = str(candidates[0]["scenario_name"]) if candidates else None
    return {
        "recommended": recommended,
        "candidates": candidates,
        "policy": (
            "Prefer an operator-selected eligible ScenarioConfig. Otherwise prefer packaged DESIGN authority, "
            "then VALIDATION authority, deterministic by scenario name. Never use SCREENING or a scenario "
            "without Orekit/spacecraft authority for RINEX-to-runnable baseline construction."
        ),
    }


MISSION_TEMPLATE_POLICY_SCRIPT = r"""
function missionEchelonLabel(mode){
  return mode==='manual'?'Ручной / Manual':mode==='auto'?'Автоматический / Automatic':'Полуавтоматический / Assisted';
}
function updateGlobalMissionEchelon(){
  const e=operatorById('activeEchelon');
  if(e)e.textContent=missionEchelonLabel(localStorage.getItem('mission-echelon')||'assisted');
}
function installMissionGlobalUi(){
  const grid=operatorById('activeRunConfigurationCard')&&operatorById('activeRunConfigurationCard').querySelector('.active-run-grid');
  if(grid&&!operatorById('activeEchelon')){
    const item=document.createElement('div');
    item.innerHTML='<b>AIMETON echelon</b><div id="activeEchelon">—</div>';
    grid.prepend(item);
  }
  const other=operatorById('other');
  const expert=operatorById('operatorTabExpert');
  if(other&&expert){
    const card=other.closest('.card');
    if(card){card.id=card.id||'otherYamlInputsCard';expert.appendChild(card);}
  }
  updateGlobalMissionEchelon();
}
const missionBaseSetEchelon=setMissionEchelon;
setMissionEchelon=function(mode){missionBaseSetEchelon(mode);updateGlobalMissionEchelon();};

async function resolveMissionModellingTemplate(preferred){
  const r=await fetch('/api/mission/modelling-templates');
  const d=await r.json();
  if(!r.ok)throw new Error(d.detail||'Modelling template policy failed');
  const candidates=Array.isArray(d.candidates)?d.candidates:[];
  let selected=candidates.find(x=>x.scenario_name===preferred)||null;
  if(!selected&&d.recommended)selected=candidates.find(x=>x.scenario_name===d.recommended)||null;
  if(!selected)throw new Error('Нет пригодного DESIGN/VALIDATION modelling profile с Orekit authority');
  if(typeof igsTemplateScenario!=='undefined')igsTemplateScenario.value=selected.scenario_name;
  return {selected,policy:d.policy||''};
}
function missionTemplateLabel(x){
  return x.scenario_name+'; '+String(x.force_mode||'').toUpperCase()+'; '+x.gravity_model+' '+x.gravity_degree+'x'+x.gravity_order+'; '+x.frame+'/'+x.time_scale;
}
missionPrepareBaseline=async function(){
  const date=(operatorById('missionDate')||{}).value||'';
  const system=(operatorById('missionSystem')||{}).value||'GLONASS';
  if(!date){missionRefreshNextStep('Укажите дату baseline.');return;}
  if(typeof igsStartDate!=='undefined')igsStartDate.value=date;
  if(typeof igsSystem!=='undefined')igsSystem.value=system;
  const mode=localStorage.getItem('mission-echelon')||'assisted';
  const preferred=typeof scenario!=='undefined'&&scenario&&scenario.value?scenario.value:'';
  const card=operatorById('igsConstellationCard');if(card)card.scrollIntoView({behavior:'smooth',block:'start'});
  if(mode==='manual'){
    if(preferred&&typeof igsTemplateScenario!=='undefined')igsTemplateScenario.value=preferred;
    missionRefreshNextStep('Ручной эшелон: дата и система подготовлены. Явно выберите modelling template и управляйте intake/conversion самостоятельно.');
    return;
  }
  let resolved;
  try{resolved=await resolveMissionModellingTemplate(preferred);}
  catch(e){missionRefreshNextStep('Не удалось подобрать modelling profile: '+String(e.message||e));return;}
  const label=missionTemplateLabel(resolved.selected);
  if(mode==='assisted'){
    missionRefreshNextStep('Предложен modelling profile: '+label+'. Проверьте его и нажмите «Создать baseline».');
    return;
  }
  missionRefreshNextStep('AUTO: выбран '+label+' → IGS/cache → Orekit → runnable ScenarioConfig…');
  if(typeof createIgsBaseline!=='function'){missionRefreshNextStep('AUTO остановлен: IGS baseline workflow недоступен.');return;}
  const ok=await createIgsBaseline();
  missionRefreshNextStep(ok?'AUTO baseline готов. Следующий шаг: создать вариант или перейти к экспериментам.':'AUTO остановлен. Подробности показаны в baseline-карточке.');
}

const missionPolicyBootstrap=bootstrap;
bootstrap=async function(){await missionPolicyBootstrap();installMissionGlobalUi();};
"""


def install_mission_template_policy_routes(app: FastAPI, scenario_root: Path) -> None:
    @app.get("/api/mission/modelling-templates")
    def templates() -> dict[str, object]:
        return mission_modelling_templates(scenario_root)
