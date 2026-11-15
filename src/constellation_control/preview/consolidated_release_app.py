from __future__ import annotations

from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ValidationError

from constellation_control.domain.models import ScenarioConfig
from constellation_control.preview.constellation_editor import (
    CONSTELLATION_EDITOR_CARD,
    CONSTELLATION_EDITOR_SCRIPT,
    install_constellation_editor_routes,
)
from constellation_control.preview.drift_consistency_ui import (
    DRIFT_CONSISTENCY_CARD,
    DRIFT_CONSISTENCY_SCRIPT,
    install_drift_consistency_routes,
)
from constellation_control.preview.galileo_gsc_input import (
    GALILEO_GSC_CARD,
    GALILEO_GSC_SCRIPT,
    install_galileo_gsc_routes,
)
from constellation_control.preview.gnss_almanac_input import (
    GNSS_ALMANAC_CARD,
    GNSS_ALMANAC_SCRIPT,
    install_gnss_almanac_routes,
)
from constellation_control.preview.iac_gnss_intake import (
    IAC_GNSS_CARD,
    IAC_GNSS_SCRIPT,
    install_iac_gnss_routes,
)
from constellation_control.preview.norad_input import NORAD_CARD, NORAD_SCRIPT, install_norad_routes
from constellation_control.preview.osculating_input import (
    OSCULATING_CARD,
    OSCULATING_SCRIPT,
    install_osculating_routes,
)
from constellation_control.preview.perturbation_ui import (
    PERTURBATION_CARD,
    PERTURBATION_SCRIPT,
    install_perturbation_routes,
)
from constellation_control.preview.release_app import (
    create_preview_app as create_release_preview_app,
    render_preview_page_for_test as render_release_page,
)
from constellation_control.preview.resource_state_ui import (
    RESOURCE_STATE_CARD,
    RESOURCE_STATE_SCRIPT,
    install_resource_state_routes,
)
from constellation_control.preview.run_promotion_ui import (
    RUN_PROMOTION_CARD,
    RUN_PROMOTION_SCRIPT,
    install_run_promotion_routes,
)
from constellation_control.preview.spacecraft_catalog_ui import (
    SPACECRAFT_CATALOG_CARD,
    SPACECRAFT_CATALOG_SCRIPT,
    install_spacecraft_catalog_routes,
)
from constellation_control.preview.walker_input import WALKER_CARD, WALKER_SCRIPT, install_walker_routes
from constellation_control.preview.workbook_upload import (
    WORKBOOK_CARD,
    WORKBOOK_SCRIPT,
    install_workbook_preview_route,
)

PREVIEW_VERSION = "0.2.5"


class PreviewScenarioDraftRequest(BaseModel):
    yaml_text: str


class PreviewScenarioSaveRequest(PreviewScenarioDraftRequest):
    scenario_name: str


def _validated_scenario_from_yaml(yaml_text: str) -> ScenarioConfig:
    if not yaml_text.strip():
        raise ValueError("YAML пуст / YAML is empty")
    try:
        payload = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML не читается / YAML cannot be parsed: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("ScenarioConfig должен быть YAML mapping / ScenarioConfig must be a YAML mapping")
    try:
        return ScenarioConfig.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc


def _safe_new_scenario_path(scenario_root: Path, scenario_name: str) -> Path:
    if not scenario_name or Path(scenario_name).name != scenario_name:
        raise ValueError(
            "Имя нового сценария должно быть именем YAML-файла без пути / "
            "new scenario name must be a YAML file name without path components"
        )
    if not scenario_name.lower().endswith((".yaml", ".yml")):
        raise ValueError("Требуется файл .yaml/.yml / scenario name must end with .yaml or .yml")
    root = scenario_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    candidate = (root / scenario_name).resolve()
    if candidate.parent != root:
        raise ValueError("Некорректный путь сценария / invalid scenario path")
    if candidate.exists():
        raise ValueError(
            "Файл уже существует; существующие сценарии не перезаписываются / "
            "file already exists; existing scenarios are never overwritten"
        )
    return candidate


def _draft_payload(scenario: ScenarioConfig) -> dict[str, object]:
    return {
        "valid": True,
        "scenario_id": scenario.scenario_id,
        "force_mode": scenario.force_model.mode.value,
        "force_model_fingerprint": scenario.force_model.fingerprint(),
        "duration_s": scenario.duration_s,
        "output_step_s": scenario.output_step_s,
        "normalized": scenario.model_dump(mode="json"),
    }


_EDITOR_CARD = r"""
<div class="card" id="scenarioEditorCard">
  <h3>Редактор сценария / Scenario editor</h3>
  <p class="hint">Редактируется полный ScenarioConfig YAML: эпоха, горизонт и шаг, модель сил, интегратор, ограничения, Monte Carlo, орбитальная группировка, параметры КА и манёвры. Перед сохранением выполняется полная валидация. Существующие YAML не перезаписываются. / Edit the complete ScenarioConfig YAML: epoch, horizon and output step, force model, integrator, constraints, Monte Carlo, constellation, spacecraft and maneuvers. Full validation is required before saving. Existing YAML files are never overwritten.</p>
  <textarea id="scenarioEditor" rows="32" style="width:100%;box-sizing:border-box;font-family:Consolas,monospace"></textarea>
  <button onclick="validateScenarioDraft()">Проверить YAML / Validate YAML</button>
  <label for="scenarioSaveAs"><b>Сохранить как новый сценарий / Save as new scenario</b></label>
  <input id="scenarioSaveAs" type="text" placeholder="my-scenario-edited.yaml">
  <button onclick="saveScenarioDraft()">Сохранить и открыть / Save and open</button>
  <div id="scenarioEditorStatus" class="status">Откройте сценарий для редактирования / Open a scenario to edit.</div>
  <pre id="scenarioEditorNormalized" style="display:none"></pre>
</div>
"""


_EDITOR_SCRIPT = r"""
const originalLoadScenarioForEditor=loadScenario;
function scenarioEditorMessage(text,kind=''){const e=document.getElementById('scenarioEditorStatus');e.textContent=text;e.className='status '+kind;}
function syncScenarioEditor(){
  if(!current)return;
  const editor=document.getElementById('scenarioEditor');
  editor.value=current.yaml_text||'';
  const saveAs=document.getElementById('scenarioSaveAs');
  const source=current.scenario_name||scenario.value||'scenario.yaml';
  const lower=source.toLowerCase();
  const ext=lower.endsWith('.yaml')?'.yaml':lower.endsWith('.yml')?'.yml':'.yaml';
  const stem=source.slice(0,source.length-ext.length);
  saveAs.value=stem+'-edited'+ext;
  scenarioEditorMessage('YAML загружен. Измените параметры и выполните проверку / YAML loaded. Edit parameters and validate.');
  if(typeof syncOsculatingSatellites==='function')syncOsculatingSatellites();
  if(typeof syncNoradSatellites==='function')syncNoradSatellites();
  if(typeof syncGnssAlmanacSatellites==='function')syncGnssAlmanacSatellites();
  if(typeof syncConstellationEditor==='function')syncConstellationEditor();
  if(typeof previewResourceState==='function')previewResourceState();
}
loadScenario=async function(){await originalLoadScenarioForEditor();syncScenarioEditor();};
async function validateScenarioDraft(){
  const yamlText=document.getElementById('scenarioEditor').value;
  scenarioEditorMessage('Проверка… / Validating…');
  const r=await fetch('/api/scenario-drafts/validate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({yaml_text:yamlText})});
  const d=await r.json();
  if(!r.ok){scenarioEditorMessage(d.detail||'Validation failed','danger');return false;}
  document.getElementById('scenarioEditorNormalized').textContent=JSON.stringify(d.normalized,null,2);
  scenarioEditorMessage('VALID: '+d.scenario_id+'; mode='+d.force_mode+'; fingerprint='+d.force_model_fingerprint,'ok');
  return true;
}
async function saveScenarioDraft(){
  const yamlText=document.getElementById('scenarioEditor').value;
  const name=document.getElementById('scenarioSaveAs').value.trim();
  if(!name){scenarioEditorMessage('Укажите новое имя YAML / Supply a new YAML file name','danger');return;}
  scenarioEditorMessage('Проверка и сохранение… / Validating and saving…');
  const r=await fetch('/api/scenario-drafts/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scenario_name:name,yaml_text:yamlText})});
  const d=await r.json();
  if(!r.ok){scenarioEditorMessage(d.detail||'Save failed','danger');return;}
  const c=await fetch('/api/scenarios');catalog=await c.json();
  scenario.replaceChildren(...catalog.scenarios.map(x=>{const o=document.createElement('option');o.value=x;o.textContent=x;return o;}));
  scenario.value=d.scenario_name;
  await loadScenario();
  scenarioEditorMessage('Сохранено и открыто: '+d.scenario_name+' / Saved and opened: '+d.scenario_name,'ok');
}
"""


def render_preview_page_for_test() -> str:
    page = render_release_page().replace("Engineering Preview 0.2.0", f"Engineering Preview {PREVIEW_VERSION}")
    page = page.replace(
        "</section></main>",
        f"{DRIFT_CONSISTENCY_CARD}{RUN_PROMOTION_CARD}{RESOURCE_STATE_CARD}{SPACECRAFT_CATALOG_CARD}{CONSTELLATION_EDITOR_CARD}{PERTURBATION_CARD}{GALILEO_GSC_CARD}{IAC_GNSS_CARD}{GNSS_ALMANAC_CARD}{NORAD_CARD}{OSCULATING_CARD}{WALKER_CARD}{WORKBOOK_CARD}{_EDITOR_CARD}</section></main>",
        1,
    )
    page = page.replace(
        "bootstrap().catch(e=>setStatus(String(e),'danger'));",
        f"{_EDITOR_SCRIPT}\n{WORKBOOK_SCRIPT}\n{WALKER_SCRIPT}\n{OSCULATING_SCRIPT}\n{NORAD_SCRIPT}\n{GNSS_ALMANAC_SCRIPT}\n{IAC_GNSS_SCRIPT}\n{GALILEO_GSC_SCRIPT}\n{PERTURBATION_SCRIPT}\n{RESOURCE_STATE_SCRIPT}\n{SPACECRAFT_CATALOG_SCRIPT}\n{CONSTELLATION_EDITOR_SCRIPT}\n{RUN_PROMOTION_SCRIPT}\n{DRIFT_CONSISTENCY_SCRIPT}\nconst promotionBootstrap=bootstrap;bootstrap=async function(){{await promotionBootstrap();await refreshPromotableRuns();}};bootstrap().catch(e=>setStatus(String(e),'danger'));",
        1,
    )
    return page


def _remove_route(app: FastAPI, path: str) -> None:
    app.router.routes[:] = [route for route in app.router.routes if getattr(route, "path", None) != path]


def create_preview_app(scenario_root: Path = Path("scenarios"), output_root: Path = Path("runs")) -> FastAPI:
    app = create_release_preview_app(scenario_root, output_root)
    app.version = PREVIEW_VERSION
    _remove_route(app, "/")
    _remove_route(app, "/health")

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(render_preview_page_for_test())

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "preview": PREVIEW_VERSION}

    @app.post("/api/scenario-drafts/validate")
    def validate_scenario_draft(request: PreviewScenarioDraftRequest) -> dict[str, object]:
        try:
            return _draft_payload(_validated_scenario_from_yaml(request.yaml_text))
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/scenario-drafts/save")
    def save_scenario_draft(request: PreviewScenarioSaveRequest) -> dict[str, object]:
        try:
            scenario = _validated_scenario_from_yaml(request.yaml_text)
            path = _safe_new_scenario_path(scenario_root, request.scenario_name)
            text = request.yaml_text if request.yaml_text.endswith("\n") else request.yaml_text + "\n"
            path.write_text(text, encoding="utf-8")
        except (ValueError, TypeError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "saved": True,
            "scenario_name": path.name,
            **_draft_payload(scenario),
        }

    install_workbook_preview_route(app, scenario_root)
    install_walker_routes(app, scenario_root)
    install_osculating_routes(app, scenario_root)
    install_norad_routes(app, scenario_root)
    install_gnss_almanac_routes(app, scenario_root)
    install_iac_gnss_routes(app)
    install_galileo_gsc_routes(app, scenario_root)
    install_perturbation_routes(app, scenario_root)
    install_resource_state_routes(app, scenario_root, output_root)
    install_spacecraft_catalog_routes(app, scenario_root)
    install_constellation_editor_routes(app, scenario_root)
    install_run_promotion_routes(app, scenario_root, output_root)
    install_drift_consistency_routes(app, output_root)
    return app
