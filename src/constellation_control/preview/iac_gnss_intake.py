from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from constellation_control.adapters.iac_gnss_tables import (
    IAC_DATA_URLS,
    IacDataset,
    fetch_iac_table,
    parse_iac_html,
    parse_iac_json,
    parse_iac_text,
)
from constellation_control.adapters.reviewed_http_fetch import fetch_reviewed_url
from constellation_control.preview.source_settings import resolve_source_url


class IacOfflinePreviewRequest(BaseModel):
    dataset: IacDataset
    filename: str
    content_text: str


_IAC_SOURCE_IDS: dict[IacDataset, str] = {
    IacDataset.GLONASS_ALMANAC: "iac_glonass",
    IacDataset.GPS_ALMANAC: "iac_gps",
    IacDataset.BEIDOU_ALMANAC: "iac_beidou_almanac",
    IacDataset.BEIDOU_CONSTELLATION: "iac_beidou_constellation",
}


def _configured_iac_url(dataset: IacDataset) -> str:
    return resolve_source_url(_IAC_SOURCE_IDS[dataset], IAC_DATA_URLS[dataset])


def _fetch_configured_iac_table(dataset: IacDataset):
    url = _configured_iac_url(dataset)
    if url == IAC_DATA_URLS[dataset]:
        return fetch_iac_table(dataset)
    response = fetch_reviewed_url(url, timeout_s=15.0)
    try:
        content = response.raw.decode("utf-8")
    except UnicodeDecodeError:
        content = response.raw.decode("cp1251")
    if dataset == IacDataset.BEIDOU_CONSTELLATION:
        return parse_iac_html(dataset, content, source_url=url)
    return parse_iac_json(dataset, content, source_url=url)


def _payload(table, *, filename: str | None = None) -> dict[str, object]:
    return {
        "valid": True,
        "dataset": table.dataset.value,
        "source_url": table.source_url,
        "source_filename": filename,
        "source_sha256": table.source_sha256,
        "headers": list(table.headers),
        "rows": [list(row) for row in table.rows],
        "record_count": len(table.rows),
        "canonical_tsv": table.canonical_tsv,
        "runnable_promotion_allowed": False,
        "authority_note": (
            "IAC table intake is preserved as source evidence. Dataset-specific orbital mapping "
            "must be explicit and validated before ScenarioConfig promotion."
        ),
    }


IAC_GNSS_CARD = r"""
<div class="card" id="iacGnssCard">
  <h3>ИАЦ GNSS: online / offline</h3>
  <p class="hint">Источник: glonass-iac.ru. Online URL берётся из Settings для выбранной системы. Offline принимает сохранённую текстовую таблицу того же набора данных. Оба режима сохраняют SHA-256 и canonical TSV; неизвестные колонки не повышаются скрыто до орбитальной authority.</p>
  <label>Набор данных / Dataset
    <select id="iacGnssDataset">
      <option value="glonass-almanac">GLONASS — альманах</option>
      <option value="gps-almanac">GPS — альманах</option>
      <option value="beidou-almanac">BeiDou — альманах</option>
      <option value="beidou-constellation">BeiDou — состав ОГ</option>
    </select>
  </label>
  <div class="grid">
    <button type="button" id="iacGnssOnlineBtn" onclick="fetchIacGnssOnline()">Загрузить с ИАЦ / Fetch online</button>
    <label>Offline TXT/TSV <input id="iacGnssFile" type="file" accept=".txt,.tsv,.csv"></label>
  </div>
  <button type="button" onclick="previewIacGnssOffline()">Прочитать файл / Read offline file</button>
  <div id="iacGnssStatus" class="status"></div>
  <pre id="iacGnssPreview"></pre>
</div>
"""

IAC_GNSS_SCRIPT = r"""
function iacStatus(text,kind=''){iacGnssStatus.textContent=text;iacGnssStatus.className='status '+kind;}
function iacErrorDetail(d){if(!d)return 'IAC request failed';if(typeof d.detail==='string')return d.detail;if(d.detail!==undefined)return JSON.stringify(d.detail);return JSON.stringify(d);}
function showIacTable(d){
  const lines=[];
  lines.push('dataset='+d.dataset);
  lines.push('source='+(d.source_url||d.source_filename||'offline'));
  lines.push('sha256='+d.source_sha256);
  lines.push('records='+d.record_count);
  lines.push('headers='+d.headers.join(' | '));
  lines.push('');
  lines.push(d.canonical_tsv);
  iacGnssPreview.textContent=lines.join('\n');
}
async function fetchIacGnssOnline(){
  const button=document.getElementById('iacGnssOnlineBtn');button.disabled=true;iacStatus('Загрузка ИАЦ… / Fetching IAC…');
  try{const dataset=iacGnssDataset.value;const r=await fetch('/api/iac-gnss/online/'+encodeURIComponent(dataset));const d=await r.json();if(!r.ok)throw new Error(iacErrorDetail(d));showIacTable(d);iacStatus('VALID ONLINE: '+d.record_count+' rows','ok');}
  catch(e){iacStatus(String(e.message||e),'danger');}
  finally{button.disabled=false;}
}
async function previewIacGnssOffline(){
  const file=iacGnssFile.files&&iacGnssFile.files[0];
  if(!file){iacStatus('Выберите TXT/TSV файл / Select TXT/TSV file','danger');return;}
  const text=await file.text();
  const r=await fetch('/api/iac-gnss/offline-preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dataset:iacGnssDataset.value,filename:file.name,content_text:text})});
  const d=await r.json();
  if(!r.ok){iacStatus(iacErrorDetail(d),'danger');return;}
  showIacTable(d);iacStatus('VALID OFFLINE: '+d.record_count+' rows','ok');
}
"""


def install_iac_gnss_routes(app: FastAPI) -> None:
    @app.get("/api/iac-gnss/sources")
    def sources() -> dict[str, str]:
        try:
            return {dataset.value: _configured_iac_url(dataset) for dataset in IacDataset}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/iac-gnss/online/{dataset}")
    def online(dataset: IacDataset) -> dict[str, object]:
        try:
            return _payload(_fetch_configured_iac_table(dataset))
        except (ValueError, TypeError, OSError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/iac-gnss/offline-preview")
    def offline_preview(request: IacOfflinePreviewRequest) -> dict[str, object]:
        try:
            table = parse_iac_text(request.dataset, request.content_text)
            return _payload(table, filename=request.filename)
        except (ValueError, TypeError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
