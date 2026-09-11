from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, field_validator

_SETTINGS_ENV = "OC_GNSS_SOURCE_SETTINGS"
_DEFAULT_SETTINGS_PATH = Path("runtime/settings/source_endpoints.json")


class SourceEndpointSetting(BaseModel):
    source_id: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=160)
    enabled: bool = True
    base_url: str = Field(min_length=1, max_length=2048)
    request_template: str = Field(default="", max_length=4096)
    notes: str = Field(default="", max_length=1000)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        value = value.strip()
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https", "ftp"} or not parsed.netloc:
            raise ValueError("base_url must be an absolute http(s) or ftp URL")
        return value.rstrip("/")


class SourceSettingsDocument(BaseModel):
    version: int = 1
    sources: list[SourceEndpointSetting]


DEFAULT_SOURCE_SETTINGS = SourceSettingsDocument(
    sources=[
        SourceEndpointSetting(
            source_id="igs_bkg",
            label="IGS BKG broadcast navigation",
            base_url="https://igs.bkg.bund.de/root_ftp/IGS/BRDC",
            request_template="{base_url}/{year}/{doy}/BRDC00WRD_R_{year}{doy}0000_01D_{system_suffix}.rnx.gz",
            notes="Reviewed global broadcast-navigation source.",
        ),
        SourceEndpointSetting(
            source_id="igs_whu",
            label="IGS Wuhan University daily navigation",
            base_url="ftp://igs.gnsswhu.cn/pub/gps/data/daily",
            request_template="{base_url}/{year}/{doy}/{yy}p/",
            notes="Directory is discovered at runtime; do not hard-code a filename.",
        ),
        SourceEndpointSetting(
            source_id="galileo_gsc_index",
            label="Galileo GSC almanac index",
            base_url="https://www.gsc-europa.eu",
            request_template="{base_url}/gsc-products/almanac",
            notes="Official Galileo GSC almanac index.",
        ),
        SourceEndpointSetting(
            source_id="galileo_gsc_files",
            label="Galileo GSC almanac files",
            base_url="https://www.gsc-europa.eu",
            request_template="{base_url}/sites/default/files/",
            notes="Allowed official XML file prefix discovered from the index.",
        ),
        SourceEndpointSetting(
            source_id="navcen_gps_yuma",
            label="USCG NAVCEN GPS YUMA",
            base_url="https://www.navcen.uscg.gov",
            request_template="{base_url}/sites/default/files/gps/almanac/current_yuma.alm",
        ),
        SourceEndpointSetting(
            source_id="navcen_gps_sem",
            label="USCG NAVCEN GPS SEM",
            base_url="https://www.navcen.uscg.gov",
            request_template="{base_url}/sites/default/files/gps/almanac/current_sem.al3",
        ),
        SourceEndpointSetting(
            source_id="iac_glonass",
            label="IAC GLONASS ephemeris table",
            base_url="https://glonass-iac.ru",
            request_template="{base_url}/glonass/ephemeris/ephemeris_json.php",
            notes="Operator-configurable IAC authority endpoint.",
        ),
        SourceEndpointSetting(
            source_id="iac_ftp_archive",
            label="IAC GLONASS FTP archive",
            base_url="ftp://ftp.glonass-iac.ru",
            request_template="{base_url}/{directory}/",
            notes=(
                "Official Applied Consumer Centre FTP archive. Anonymous FTP on port 21: login anonymous, "
                "password anonymous. Relevant sections include MCC (IAC analysis products, daily GLONASS/GPS "
                "almanacs, generalized onboard ephemerides), IGS, NAVCEN, IERS, FAF, GENERAL, REPORTS. "
                "Use directory=MCC for the primary Russian GLONASS archive; exact product filenames are discovered "
                "at runtime and are not hard-coded."
            ),
        ),
    ]
)


def settings_path() -> Path:
    configured = os.environ.get(_SETTINGS_ENV, "").strip()
    return Path(configured) if configured else _DEFAULT_SETTINGS_PATH


def load_source_settings() -> SourceSettingsDocument:
    path = settings_path()
    if not path.is_file():
        return DEFAULT_SOURCE_SETTINGS.model_copy(deep=True)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return SourceSettingsDocument.model_validate(payload)
    except Exception as exc:  # noqa: BLE001 - runtime settings boundary
        raise ValueError(f"invalid source settings file {path}: {exc}") from exc


def save_source_settings(document: SourceSettingsDocument) -> Path:
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
    if item is None or not item.enabled:
        return default_url
    template = item.request_template.strip() or "{base_url}"
    params = {"base_url": item.base_url, **values}
    try:
        return template.format(**params)
    except (KeyError, ValueError) as exc:
        raise ValueError(f"invalid request_template for {source_id}: {exc}") from exc


def install_source_settings_routes(app: FastAPI) -> None:
    @app.get("/api/settings/sources")
    def get_source_settings() -> dict[str, object]:
        document = load_source_settings()
        return {
            "settings_path": str(settings_path()),
            "version": document.version,
            "sources": [item.model_dump(mode="json") for item in document.sources],
        }

    @app.put("/api/settings/sources")
    def put_source_settings(document: SourceSettingsDocument) -> dict[str, object]:
        ids = [item.source_id for item in document.sources]
        if len(ids) != len(set(ids)):
            raise HTTPException(status_code=422, detail="source_id values must be unique")
        path = save_source_settings(document)
        return {"saved": True, "settings_path": str(path), "sources": len(document.sources)}

    @app.post("/api/settings/sources/reset")
    def reset_source_settings() -> dict[str, object]:
        path = save_source_settings(DEFAULT_SOURCE_SETTINGS.model_copy(deep=True))
        return {"saved": True, "reset": True, "settings_path": str(path)}


SOURCE_SETTINGS_CARD = r"""
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
  try{const r=await fetch('/api/settings/sources');const d=await r.json();if(!r.ok)throw new Error(d.detail||JSON.stringify(d));sourceSettingsDocument={version:d.version,sources:d.sources};document.getElementById('sourceSettingsPath').textContent='Файл: '+d.settings_path;renderSourceSettings();status.textContent='READY: '+d.sources.length+' sources';status.className='status ok';return true;}catch(e){status.textContent='FAILED: '+String(e);status.className='status danger';return false;}
}
function collectSourceSettings(){
  const rows=Array.from(document.querySelectorAll('.source-setting-row'));
  return {version:sourceSettingsDocument?.version||1,sources:rows.map(row=>({source_id:row.querySelector('.src-id').value.trim(),label:row.querySelector('.src-label').value.trim(),enabled:row.querySelector('.src-enabled').checked,base_url:row.querySelector('.src-base').value.trim(),request_template:row.querySelector('.src-template').value,notes:row.querySelector('.src-notes').value.trim()}))};
}
async function saveSourceSettings(){
  const button=document.getElementById('sourceSettingsSave'),status=document.getElementById('sourceSettingsStatus');button.disabled=true;
  try{const payload=collectSourceSettings();const r=await fetch('/api/settings/sources',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const d=await r.json();if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:JSON.stringify(d.detail||d));sourceSettingsDocument=payload;status.textContent='SAVED: '+d.settings_path;status.className='status ok';return true;}catch(e){status.textContent='FAILED: '+String(e);status.className='status danger';return false;}finally{button.disabled=false;}
}
async function resetSourceSettings(){
  const button=document.getElementById('sourceSettingsReset'),status=document.getElementById('sourceSettingsStatus');button.disabled=true;
  try{const r=await fetch('/api/settings/sources/reset',{method:'POST'});const d=await r.json();if(!r.ok)throw new Error(d.detail||JSON.stringify(d));await loadSourceSettings();status.textContent='RESET: штатные настройки восстановлены';status.className='status ok';return true;}catch(e){status.textContent='FAILED: '+String(e);status.className='status danger';return false;}finally{button.disabled=false;}
}
"""
