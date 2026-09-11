from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

_SETTINGS_ENV = "OC_GNSS_SOURCE_SETTINGS"
_DEFAULT_SETTINGS_PATH = Path("runtime/settings/source_endpoints.json")
GNSSSystem = Literal["GLONASS", "GPS", "Galileo", "BeiDou"]
SourceSelectionMode = Literal["auto", "manual"]


class SourceEndpointSetting(BaseModel):
    source_id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=160)
    enabled: bool = True
    base_url: str = Field(min_length=1, max_length=2048)
    request_template: str = Field(default="", max_length=4096)
    notes: str = Field(default="", max_length=1000)
    systems: list[GNSSSystem] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        value = value.strip()
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https", "ftp"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute http(s) or ftp URL")
        return value.rstrip("/")


class SourceSelectionPolicy(BaseModel):
    mode: SourceSelectionMode = "auto"
    selected_source_id: str | None = None
    auto_order: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_manual_selection(self) -> SourceSelectionPolicy:
        if self.mode == "manual" and not self.selected_source_id:
            raise ValueError("selected_source_id is required in manual source-selection mode")
        return self


class SourceSettingsDocument(BaseModel):
    version: int = 3
    sources: list[SourceEndpointSetting]
    selection: dict[GNSSSystem, SourceSelectionPolicy] = Field(default_factory=dict)


DEFAULT_SOURCE_SETTINGS = SourceSettingsDocument(
    version=3,
    sources=[
        SourceEndpointSetting(
            source_id="igs_bkg",
            label="IGS BKG broadcast navigation",
            base_url="https://igs.bkg.bund.de/root_ftp/IGS/BRDC",
            request_template="{base_url}/{year}/{doy}/BRDC00WRD_R_{year}{doy}0000_01D_{system_suffix}.rnx.gz",
            notes="Reviewed global broadcast-navigation source.",
            systems=["GLONASS", "GPS", "Galileo", "BeiDou"],
            capabilities=["broadcast_rinex_nav"],
        ),
        SourceEndpointSetting(
            source_id="igs_whu",
            label="IGS Wuhan University daily navigation",
            base_url="ftp://igs.gnsswhu.cn/pub/gps/data/daily",
            request_template="{base_url}/{year}/{doy}/{yy}p/",
            notes="Directory is discovered at runtime; do not hard-code a filename.",
            systems=["GLONASS", "GPS", "Galileo", "BeiDou"],
            capabilities=["broadcast_rinex_nav"],
        ),
        SourceEndpointSetting(
            source_id="galileo_gsc_index",
            label="Galileo GSC almanac index",
            base_url="https://www.gsc-europa.eu",
            request_template="{base_url}/gsc-products/almanac",
            notes="Official Galileo GSC almanac index.",
            systems=["Galileo"],
            capabilities=["almanac_index"],
        ),
        SourceEndpointSetting(
            source_id="galileo_gsc_files",
            label="Galileo GSC almanac files",
            base_url="https://www.gsc-europa.eu",
            request_template="{base_url}/sites/default/files/",
            notes="Allowed official XML file prefix discovered from the index.",
            systems=["Galileo"],
            capabilities=["almanac_xml"],
        ),
        SourceEndpointSetting(
            source_id="navcen_gps_yuma",
            label="USCG NAVCEN GPS YUMA",
            base_url="https://www.navcen.uscg.gov",
            request_template="{base_url}/sites/default/files/gps/almanac/current_yuma.alm",
            systems=["GPS"],
            capabilities=["gps_yuma_almanac"],
        ),
        SourceEndpointSetting(
            source_id="navcen_gps_sem",
            label="USCG NAVCEN GPS SEM",
            base_url="https://www.navcen.uscg.gov",
            request_template="{base_url}/sites/default/files/gps/almanac/current_sem.al3",
            systems=["GPS"],
            capabilities=["gps_sem_almanac"],
        ),
        SourceEndpointSetting(
            source_id="iac_glonass",
            label="IAC GLONASS ephemeris table",
            base_url="https://glonass-iac.ru",
            request_template="{base_url}/glonass/ephemeris/ephemeris_json.php",
            notes="Qualified IAC live GLONASS ephemeris/almanac table.",
            systems=["GLONASS"],
            capabilities=["glonass_ephemeris_table"],
        ),
        SourceEndpointSetting(
            source_id="iac_ftp_archive",
            label="IAC GLONASS FTP archive",
            base_url="ftp://ftp.glonass-iac.ru",
            request_template="{base_url}/{directory}/",
            notes=(
                "Official Applied Consumer Centre FTP archive. Anonymous FTP on port 21. "
                "MCC/IGS products are discovered at runtime and accepted as broadcast RINEX NAV only "
                "after strict format/system validation."
            ),
            systems=["GLONASS", "GPS"],
            capabilities=["archive_discovery", "broadcast_rinex_nav"],
        ),
        SourceEndpointSetting(
            source_id="fcnd_api",
            label="FCND Russian GNSS data API",
            base_url="https://fcnd.ru",
            request_template="{base_url}/api/getData/",
            notes=(
                "Russian Federal Coordinate Network Data Centre API. The runtime queries documented getData "
                "catalogue/datafile endpoints and accepts a candidate only after strict RINEX NAV validation."
            ),
            systems=["GLONASS", "GPS"],
            capabilities=["gnss_data_api", "broadcast_rinex_nav"],
        ),
    ],
    selection={
        "GLONASS": SourceSelectionPolicy(
            mode="auto",
            auto_order=["iac_glonass", "iac_ftp_archive", "fcnd_api", "igs_bkg", "igs_whu"],
        ),
        "GPS": SourceSelectionPolicy(
            mode="auto",
            auto_order=["fcnd_api", "navcen_gps_yuma", "navcen_gps_sem", "igs_bkg", "igs_whu"],
        ),
        "Galileo": SourceSelectionPolicy(
            mode="auto",
            auto_order=["galileo_gsc_index", "galileo_gsc_files", "igs_bkg", "igs_whu"],
        ),
        "BeiDou": SourceSelectionPolicy(mode="auto", auto_order=["igs_bkg", "igs_whu"]),
    },
)


def settings_path() -> Path:
    configured = os.environ.get(_SETTINGS_ENV, "").strip()
    return Path(configured) if configured else _DEFAULT_SETTINGS_PATH


def _merge_auto_order(system: GNSSSystem, current: list[str], desired: list[str]) -> list[str]:
    result: list[str] = []
    for source_id in current:
        if source_id and source_id not in result:
            result.append(source_id)
    for source_id in desired:
        if source_id in result:
            continue
        if source_id == "fcnd_api" and system == "GLONASS" and "iac_ftp_archive" in result:
            result.insert(result.index("iac_ftp_archive") + 1, source_id)
        elif source_id == "fcnd_api" and system == "GPS":
            result.insert(0, source_id)
        else:
            result.append(source_id)
    return result


def _upgrade_document(document: SourceSettingsDocument) -> SourceSettingsDocument:
    upgraded = document.model_copy(deep=True)
    existing_ids = {item.source_id for item in upgraded.sources}
    for default_source in DEFAULT_SOURCE_SETTINGS.sources:
        if default_source.source_id not in existing_ids:
            upgraded.sources.append(default_source.model_copy(deep=True))
            existing_ids.add(default_source.source_id)

    for system, default_policy in DEFAULT_SOURCE_SETTINGS.selection.items():
        policy = upgraded.selection.get(system)
        if policy is None:
            upgraded.selection[system] = default_policy.model_copy(deep=True)
            continue
        if policy.mode == "auto":
            policy.auto_order = _merge_auto_order(system, policy.auto_order, default_policy.auto_order)
    upgraded.version = 3
    return upgraded


def load_source_settings() -> SourceSettingsDocument:
    path = settings_path()
    if not path.is_file():
        return DEFAULT_SOURCE_SETTINGS.model_copy(deep=True)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return _upgrade_document(SourceSettingsDocument.model_validate(payload))
    except Exception as exc:  # noqa: BLE001 - runtime settings boundary
        raise ValueError(f"invalid source settings file {path}: {exc}") from exc


def save_source_settings(document: SourceSettingsDocument) -> Path:
    document = _upgrade_document(document)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(document.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(path)
    return path


def source_setting(source_id: str) -> SourceEndpointSetting | None:
    for item in load_source_settings().sources:
        if item.source_id == source_id:
            return item
    return None


def resolve_source_url(source_id: str, default_url: str, **values: Any) -> str:
    item = source_setting(source_id)
    if item is None:
        return default_url
    if not item.enabled:
        raise ValueError(f"source {source_id} is disabled")
    template = item.request_template.strip() or "{base_url}"
    params = {"base_url": item.base_url, **values}
    try:
        return template.format(**params)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"invalid request_template for {source_id}: {exc}") from exc


def selected_source_sequence(
    system: GNSSSystem,
    *,
    capability: str | None = None,
) -> list[SourceEndpointSetting]:
    document = load_source_settings()
    by_id = {item.source_id: item for item in document.sources}
    policy = document.selection.get(system, SourceSelectionPolicy())
    if policy.mode == "manual":
        ids = [policy.selected_source_id] if policy.selected_source_id else []
    else:
        ids = list(policy.auto_order)
        ids.extend(item.source_id for item in document.sources if item.source_id not in ids)
    result: list[SourceEndpointSetting] = []
    for source_id in ids:
        item = by_id.get(source_id or "")
        if item is None or not item.enabled or (item.systems and system not in item.systems):
            continue
        if capability is not None and capability not in item.capabilities:
            continue
        result.append(item)
    if policy.mode == "manual" and not result:
        suffix = f" with capability {capability}" if capability else ""
        raise ValueError(f"selected source is unavailable for {system}{suffix}")
    return result


def _validate_document(document: SourceSettingsDocument) -> None:
    ids = [item.source_id for item in document.sources]
    if len(ids) != len(set(ids)):
        raise HTTPException(status_code=422, detail="source_id values must be unique")
    known = set(ids)
    for system, policy in document.selection.items():
        referenced = list(policy.auto_order)
        if policy.selected_source_id:
            referenced.append(policy.selected_source_id)
        unknown = [source_id for source_id in referenced if source_id not in known]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"{system} source-selection policy references unknown source_id: {', '.join(unknown)}",
            )


def install_source_settings_routes(app: FastAPI) -> None:
    @app.get("/api/settings/sources")
    def get_source_settings() -> dict[str, object]:
        document = load_source_settings()
        return {
            "settings_path": str(settings_path()),
            "version": document.version,
            "sources": [item.model_dump(mode="json") for item in document.sources],
            "selection": {system: policy.model_dump(mode="json") for system, policy in document.selection.items()},
        }

    @app.put("/api/settings/sources")
    def put_source_settings(document: SourceSettingsDocument) -> dict[str, object]:
        document = _upgrade_document(document)
        _validate_document(document)
        path = save_source_settings(document)
        return {"saved": True, "settings_path": str(path), "sources": len(document.sources)}

    @app.post("/api/settings/sources/reset")
    def reset_source_settings() -> dict[str, object]:
        path = save_source_settings(DEFAULT_SOURCE_SETTINGS.model_copy(deep=True))
        return {"saved": True, "reset": True, "settings_path": str(path)}


SOURCE_SETTINGS_CARD = r"""
<div class="card" id="sourceSelectionCard">
  <h3>Выбор источника данных / Data source selection</h3>
  <p class="hint">Для каждой ГНСС можно выбрать один конкретный источник или режим AUTO. AUTO перебирает только включённые источники по заданному порядку; ошибки каждого источника сохраняются, успешный источник фиксируется в provenance.</p>
  <div class="grid">
    <label>Система / System<select id="sourceSelectionSystem" onchange="renderSourceSelection()"><option>GLONASS</option><option>GPS</option><option>Galileo</option><option>BeiDou</option></select></label>
    <label>Режим / Mode<select id="sourceSelectionMode" onchange="sourceSelectionModeChanged()"><option value="auto">AUTO — перебирать по порядку</option><option value="manual">MANUAL — только выбранный источник</option></select></label>
  </div>
  <label>Источник / Selected source<select id="sourceSelectionSelected"></select></label>
  <label>Порядок AUTO / AUTO order<textarea id="sourceSelectionOrder" rows="5" placeholder="one source_id per line"></textarea></label>
  <p class="hint">Порядок AUTO задаётся сверху вниз. Отключённые и несовместимые с системой источники пропускаются. MANUAL fail-closed: при недоступности выбранного источника скрытого перехода на другой источник нет.</p>
  <div id="sourceSelectionStatus" class="status">Политика выбора не загружена / Selection policy not loaded.</div>
</div>
<div class="card" id="sourceSettingsCard">
  <h3>Источники данных / Data source settings</h3>
  <p class="hint">URL и шаблоны запросов хранятся локально и переживают перезапуск Preview. Подстановки в шаблонах: {base_url}, {year}, {yy}, {doy}, {system}, {system_suffix}, {prn}, {slot}, {directory}. Ошибка шаблона блокирует запрос явно.</p>
  <div id="sourceSettingsPath" class="hint"></div>
  <div id="sourceSettingsRows"></div>
  <div class="grid">
    <button type="button" id="sourceSettingsSave" onclick="saveSourceSettings()">Сохранить настройки / Save settings</button>
    <button type="button" id="sourceSettingsReset" onclick="resetSourceSettings()">Вернуть штатные / Reset defaults</button>
  </div>
  <div id="sourceSettingsStatus" class="status">Настройки не загружены / Settings not loaded.</div>
</div>
"""


SOURCE_SETTINGS_SCRIPT = r"""
let sourceSettingsDocument=null;
function sourceSettingsEscape(value){return String(value??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));}
function sourceSystemCandidates(system){return (sourceSettingsDocument?.sources||[]).filter(s=>s.enabled&&(!s.systems||!s.systems.length||s.systems.includes(system)));}
function ensureSelectionPolicy(system){if(!sourceSettingsDocument.selection)sourceSettingsDocument.selection={};if(!sourceSettingsDocument.selection[system])sourceSettingsDocument.selection[system]={mode:'auto',selected_source_id:null,auto_order:sourceSystemCandidates(system).map(s=>s.source_id)};return sourceSettingsDocument.selection[system];}
function renderSourceSelection(){
  if(!sourceSettingsDocument)return;
  const system=document.getElementById('sourceSelectionSystem').value,policy=ensureSelectionPolicy(system),candidates=sourceSystemCandidates(system);
  const mode=document.getElementById('sourceSelectionMode'),selected=document.getElementById('sourceSelectionSelected'),order=document.getElementById('sourceSelectionOrder');
  mode.value=policy.mode||'auto';selected.replaceChildren(...candidates.map(s=>new Option(s.label+' ['+s.source_id+']',s.source_id)));
  if(policy.selected_source_id&&candidates.some(s=>s.source_id===policy.selected_source_id))selected.value=policy.selected_source_id;
  else if(candidates.length)selected.value=candidates[0].source_id;
  order.value=(policy.auto_order||[]).join('\n');sourceSelectionModeChanged(false);
  document.getElementById('sourceSelectionStatus').textContent=(mode.value==='auto'?'AUTO: ':'MANUAL: ')+(mode.value==='auto'?(policy.auto_order||[]).join(' → '):(selected.value||'—'));
}
function sourceSelectionModeChanged(update=true){
  const mode=document.getElementById('sourceSelectionMode').value,selected=document.getElementById('sourceSelectionSelected'),order=document.getElementById('sourceSelectionOrder');
  selected.disabled=mode!=='manual';order.disabled=mode!=='auto';if(update)syncSelectionFromUi();
}
function syncSelectionFromUi(){
  if(!sourceSettingsDocument)return;
  const system=document.getElementById('sourceSelectionSystem').value,policy=ensureSelectionPolicy(system),mode=document.getElementById('sourceSelectionMode').value;
  policy.mode=mode;policy.selected_source_id=mode==='manual'?document.getElementById('sourceSelectionSelected').value:null;
  policy.auto_order=document.getElementById('sourceSelectionOrder').value.split(/\r?\n/).map(v=>v.trim()).filter(Boolean);
  document.getElementById('sourceSelectionStatus').textContent=(mode==='auto'?'AUTO: '+policy.auto_order.join(' → '):'MANUAL: '+(policy.selected_source_id||'—'));
}
function renderSourceSettings(){
  const root=document.getElementById('sourceSettingsRows');if(!root||!sourceSettingsDocument)return;
  root.innerHTML=sourceSettingsDocument.sources.map((s,i)=>`<div class="card source-setting-row" data-index="${i}">
    <div class="grid"><label><input type="checkbox" class="src-enabled" ${s.enabled?'checked':''}> enabled</label><label>Source id<input class="src-id" value="${sourceSettingsEscape(s.source_id)}" readonly></label></div>
    <label>Название / Label<input class="src-label" value="${sourceSettingsEscape(s.label)}"></label>
    <label>Base URL<input class="src-base" value="${sourceSettingsEscape(s.base_url)}"></label>
    <label>Шаблон запроса / Request template<textarea class="src-template" rows="2">${sourceSettingsEscape(s.request_template)}</textarea></label>
    <label>Примечание / Notes<input class="src-notes" value="${sourceSettingsEscape(s.notes||'')}"></label>
  </div>`).join('');
}
async function loadSourceSettings(){
  const status=document.getElementById('sourceSettingsStatus');
  try{const r=await fetch('/api/settings/sources');const d=await r.json();if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail||d));sourceSettingsDocument={version:d.version,sources:d.sources,selection:d.selection||{}};document.getElementById('sourceSettingsPath').textContent='Файл: '+d.settings_path;renderSourceSettings();renderSourceSelection();status.textContent='READY: '+d.sources.length+' sources';status.className='status ok';return true;}catch(e){status.textContent='FAILED: '+String(e);status.className='status danger';return false;}
}
function collectSourceSettings(){
  syncSelectionFromUi();
  const rows=Array.from(document.querySelectorAll('.source-setting-row'));
  const sources=rows.map((row,i)=>({...sourceSettingsDocument.sources[i],source_id:row.querySelector('.src-id').value.trim(),label:row.querySelector('.src-label').value.trim(),enabled:row.querySelector('.src-enabled').checked,base_url:row.querySelector('.src-base').value.trim(),request_template:row.querySelector('.src-template').value,notes:row.querySelector('.src-notes').value.trim()}));
  return {version:sourceSettingsDocument?.version||3,sources,selection:sourceSettingsDocument.selection||{}};
}
async function saveSourceSettings(){
  const button=document.getElementById('sourceSettingsSave'),status=document.getElementById('sourceSettingsStatus');button.disabled=true;
  try{const payload=collectSourceSettings();const r=await fetch('/api/settings/sources',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const d=await r.json();if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail||d));await loadSourceSettings();status.textContent='SAVED: '+d.settings_path;status.className='status ok';return true;}catch(e){status.textContent='FAILED: '+String(e);status.className='status danger';return false;}finally{button.disabled=false;}
}
async function resetSourceSettings(){
  const button=document.getElementById('sourceSettingsReset'),status=document.getElementById('sourceSettingsStatus');button.disabled=true;
  try{const r=await fetch('/api/settings/sources/reset',{method:'POST'});const d=await r.json();if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail||d));await loadSourceSettings();status.textContent='RESET: штатные настройки восстановлены';status.className='status ok';return true;}catch(e){status.textContent='FAILED: '+String(e);status.className='status danger';return false;}finally{button.disabled=false;}
}
document.addEventListener('change',event=>{if(event.target&&event.target.id==='sourceSelectionSelected')syncSelectionFromUi();});
document.addEventListener('input',event=>{if(event.target&&event.target.id==='sourceSelectionOrder')syncSelectionFromUi();});
"""
