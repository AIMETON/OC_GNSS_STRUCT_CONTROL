from __future__ import annotations

from pathlib import Path
from typing import cast

import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from constellation_control.application.run import load_scenario
from constellation_control.domain.digital_twin import DigitalTwinConfig, ScenarioLineage
from constellation_control.domain.models import ScenarioConfig
from constellation_control.preview.base_preview_shell import preview_catalog


class ScenarioVariantRequest(BaseModel):
    source_scenario_name: str
    target_scenario_name: str
    new_scenario_id: str
    duration_s: float = Field(gt=0.0)
    output_step_s: float = Field(gt=0.0)


def _safe_source(root: Path, name: str) -> Path:
    if not name or Path(name).name != name or not name.lower().endswith((".yaml", ".yml")):
        raise ValueError("source scenario must be a .yaml/.yml file name without path components")
    root = root.resolve()
    source = (root / name).resolve()
    if source.parent != root:
        raise ValueError("invalid source scenario path")
    if not source.is_file():
        raise ValueError(f"source scenario does not exist: {name}")
    return source


def _safe_target(root: Path, name: str) -> Path:
    if not name or Path(name).name != name or not name.lower().endswith((".yaml", ".yml")):
        raise ValueError("target scenario must be a new .yaml/.yml file name without path components")
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = (root / name).resolve()
    if target.parent != root:
        raise ValueError("invalid target scenario path")
    if target.exists():
        raise ValueError("target scenario already exists; overwrite is forbidden")
    return target


def create_scenario_variant(root: Path, request: ScenarioVariantRequest) -> dict[str, object]:
    root = root.resolve()
    source = load_scenario(_safe_source(root, request.source_scenario_name))
    if request.new_scenario_id == source.scenario_id:
        raise ValueError("new scenario_id must differ from the parent scenario_id")
    if request.output_step_s > request.duration_s:
        raise ValueError("output_step_s must not exceed duration_s")

    target = _safe_target(root, request.target_scenario_name)
    prior_twin = source.digital_twin or DigitalTwinConfig()
    lineage = ScenarioLineage(
        parent_scenario_id=source.scenario_id,
        parent_config_hash=source.config_hash(),
        transformation="operator_scenario_variant",
        random_seed=None,
    )
    child = ScenarioConfig.model_validate(
        source.model_dump(mode="json")
        | {
            "scenario_id": request.new_scenario_id,
            "duration_s": request.duration_s,
            "output_step_s": request.output_step_s,
            "digital_twin": prior_twin.model_copy(update={"lineage": lineage}).model_dump(mode="json"),
        }
    )
    target.write_text(
        yaml.safe_dump(child.model_dump(mode="json"), sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    catalog = preview_catalog(root)
    runnable = cast(list[str], catalog["scenarios"])
    if target.name not in runnable:
        raise RuntimeError("derived scenario was saved but is not discoverable as runnable")
    return {
        "saved": True,
        "scenario_name": target.name,
        "scenario_id": child.scenario_id,
        "parent_scenario_id": source.scenario_id,
        "duration_s": child.duration_s,
        "output_step_s": child.output_step_s,
        "child_config_hash": child.config_hash(),
        "catalog": catalog,
    }


SCENARIO_VARIANT_CARD = r"""
<div class="card" id="scenarioVariantCard">
  <h3>Быстрый вариант сценария / Quick scenario variant</h3>
  <p class="hint">Основной путь корректировки исследования: исходный ScenarioConfig не изменяется. Создаётся новый вариант с lineage на родителя. Здесь вынесены частые безопасные изменения; параметры, меняющие физическую authority, обрабатываются специализированными workflow.</p>
  <div class="grid">
    <label>Длительность, s <input id="variantDuration" type="number" min="0.001" step="1"></label>
    <label>Шаг выдачи, s <input id="variantOutputStep" type="number" min="0.001" step="1"></label>
  </div>
  <div class="grid">
    <label>Новый scenario_id <input id="variantScenarioId" type="text"></label>
    <label>Новый YAML <input id="variantScenarioFile" type="text"></label>
  </div>
  <button onclick="createScenarioVariant()">Создать вариант</button>
  <button class="secondary" onclick="openExpertScenarioEditor()">Полный YAML — Expert</button>
  <div id="variantStatus" class="status"></div>
</div>
"""


SCENARIO_VARIANT_SCRIPT = r"""
function scenarioVariantSlug(source){return String(source||'scenario').replace(/\.ya?ml$/i,'').replace(/[^A-Za-z0-9_-]+/g,'-');}
function syncScenarioVariant(){
  if(typeof current==='undefined'||!current||typeof scenario==='undefined'||!scenario)return;
  const n=current.normalized||{};
  variantDuration.value=n.duration_s||current.duration_s||'';
  variantOutputStep.value=n.output_step_s||current.output_step_s||'';
  const slug=scenarioVariantSlug(scenario.value);
  variantScenarioId.value=slug+'-variant';
  variantScenarioFile.value=slug+'-variant.yaml';
  variantStatus.textContent='Родитель: '+scenario.value;
  variantStatus.className='status';
}
async function createScenarioVariant(){
  if(!scenario.value){variantStatus.textContent='Сначала выберите исходный сценарий';variantStatus.className='status danger';return;}
  const p={
    source_scenario_name:scenario.value,
    target_scenario_name:variantScenarioFile.value.trim(),
    new_scenario_id:variantScenarioId.value.trim(),
    duration_s:Number(variantDuration.value),
    output_step_s:Number(variantOutputStep.value)
  };
  if(!p.target_scenario_name||!p.new_scenario_id||!Number.isFinite(p.duration_s)||!Number.isFinite(p.output_step_s)){
    variantStatus.textContent='Заполните параметры нового варианта';variantStatus.className='status danger';return;
  }
  variantStatus.textContent='Создание производного ScenarioConfig…';variantStatus.className='status';
  try{
    const r=await fetch('/api/scenario-variants/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(p)});
    const d=await r.json();if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail));
    catalog=d.catalog;
    scenario.replaceChildren(...catalog.scenarios.map(x=>{const o=document.createElement('option');o.value=x;o.textContent=x;return o;}));
    scenario.value=d.scenario_name;await loadScenario();
    variantStatus.textContent='Создан вариант: '+d.scenario_name+' ← '+d.parent_scenario_id;
    variantStatus.className='status ok';
  }catch(e){variantStatus.textContent=String(e.message||e);variantStatus.className='status danger';}
}
function openExpertScenarioEditor(){showOperatorTab('expert');const e=document.getElementById('scenarioEditorCard');if(e)e.scrollIntoView({behavior:'smooth',block:'start'});}
"""


def install_scenario_variant_routes(app: FastAPI, scenario_root: Path) -> None:
    @app.post("/api/scenario-variants/create")
    def create(request: ScenarioVariantRequest) -> dict[str, object]:
        try:
            return create_scenario_variant(scenario_root, request)
        except (ValueError, TypeError, RuntimeError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
