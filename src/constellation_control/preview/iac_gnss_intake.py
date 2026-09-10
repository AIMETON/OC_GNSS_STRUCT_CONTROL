from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from constellation_control.adapters.iac_gnss_tables import (
    IAC_URLS,
    IacDataset,
    fetch_iac_table,
    parse_iac_text,
)


class IacOfflinePreviewRequest(BaseModel):
    dataset: IacDataset
    filename: str
    content_text: str


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
  <p class="hint">Источник: glonass-iac.ru. Online читает только фиксированные разрешённые страницы ИАЦ. Offline принимает сохранённую текстовую таблицу того же набора данных. Оба режима сохраняют SHA-256 и canonical TSV; неизвестные колонки не повышаются скрыто до орбитальной authority.</p>
  <label>Набор данных / Dataset
    <select id="iacGnssDataset">
      <option value="glonass-almanac">GLONASS — альманах</option>
      <option value="gps-almanac">GPS — альманах</option>
      <option value="beidou-almanac">BeiDou — альманах</option>
      <option value="beidou-constellation">BeiDou — состав ОГ</option>
    </select>
  </label>
  <div class="grid">
    <button onclick="fetchIacGnssOnline()">Загрузить с ИАЦ / Fetch online</button>
    <label>Offline TXT/TSV <input id="iacGnssFile" type="file" accept=".txt,.tsv,.csv"></label>
  </div>
  <button onclick="previewIacGnssOffline()">Прочитать файл / Read offline file</button>
  <div id="iacGnssStatus" class="status"></div>
  <pre id="iacGnssPreview"></pre>
</div>
"""

IAC_GNSS_SCRIPT = r"""
function iacStatus(text,kind=''){iacGnssStatus.textContent=text;iacGnssStatus.className='status '+kind;}
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
  iacStatus('Загрузка ИАЦ… / Fetching IAC…');
  const dataset=iacGnssDataset.value;
  const r=await fetch('/api/iac-gnss/online/'+encodeURIComponent(dataset));
  const d=await r.json();
  if(!r.ok){iacStatus(d.detail||'IAC online fetch failed','danger');return;}
  showIacTable(d);iacStatus('VALID ONLINE: '+d.record_count+' rows','ok');
}
async function previewIacGnssOffline(){
  const file=iacGnssFile.files&&iacGnssFile.files[0];
  if(!file){iacStatus('Выберите TXT/TSV файл / Select TXT/TSV file','danger');return;}
  const text=await file.text();
  const r=await fetch('/api/iac-gnss/offline-preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({dataset:iacGnssDataset.value,filename:file.name,content_text:text})});
  const d=await r.json();
  if(!r.ok){iacStatus(d.detail||'IAC offline parse failed','danger');return;}
  showIacTable(d);iacStatus('VALID OFFLINE: '+d.record_count+' rows','ok');
}
"""


def install_iac_gnss_routes(app: FastAPI) -> None:
    @app.get("/api/iac-gnss/sources")
    def sources() -> dict[str, str]:
        return {dataset.value: url for dataset, url in IAC_URLS.items()}

    @app.get("/api/iac-gnss/online/{dataset}")
    def online(dataset: IacDataset) -> dict[str, object]:
        try:
            return _payload(fetch_iac_table(dataset))
        except (ValueError, TypeError, OSError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/iac-gnss/offline-preview")
    def offline_preview(request: IacOfflinePreviewRequest) -> dict[str, object]:
        try:
            table = parse_iac_text(request.dataset, request.content_text)
            return _payload(table, filename=request.filename)
        except (ValueError, TypeError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
