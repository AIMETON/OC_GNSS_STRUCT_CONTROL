from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from constellation_control.adapters.galileo_gsc_almanac import (
    GSC_ALMANAC_INDEX_URL,
    GSC_DAILY_FILE_PREFIX,
    GalileoGscAlmanac,
    fetch_latest_galileo_gsc_almanac,
    parse_galileo_gsc_almanac,
)


class GalileoGscOfflineRequest(BaseModel):
    filename: str
    content_text: str


def _payload(almanac: GalileoGscAlmanac) -> dict[str, object]:
    return {
        "valid": True,
        "source_url": almanac.source_url,
        "source_filename": almanac.source_filename,
        "source_sha256": almanac.source_sha256,
        "record_count": len(almanac.records),
        "records": [
            {
                "svid": record.svid,
                "delta_sqrt_a_m_sqrt": record.delta_sqrt_a_m_sqrt,
                "sqrt_a_m_sqrt": record.sqrt_a_m_sqrt,
                "semi_major_axis_m": record.semi_major_axis_m,
                "eccentricity": record.eccentricity,
                "delta_inclination_semicircles": record.delta_inclination_semicircles,
                "inclination_rad": record.inclination_rad,
                "raan_semicircles": record.raan_semicircles,
                "raan_rad": record.raan_rad,
                "raan_rate_semicircles_s": record.raan_rate_semicircles_s,
                "raan_rate_rad_s": record.raan_rate_rad_s,
                "argument_of_perigee_semicircles": record.argument_of_perigee_semicircles,
                "argument_of_perigee_rad": record.argument_of_perigee_rad,
                "mean_anomaly_semicircles": record.mean_anomaly_semicircles,
                "mean_anomaly_rad": record.mean_anomaly_rad,
                "af0_s": record.af0_s,
                "af1_s_s": record.af1_s_s,
                "iod": record.iod,
                "t0a_s": record.t0a_s,
                "wna_mod4": record.wna_mod4,
                "status_e5a": record.status_e5a,
                "status_e5b": record.status_e5b,
                "status_e1b": record.status_e1b,
            }
            for record in almanac.records
        ],
        "runnable_promotion_allowed": False,
        "authority_note": almanac.authority_note,
    }


GALILEO_GSC_CARD = r"""
<div class="card" id="galileoGscCard">
  <h3>Galileo — официальный GSC Almanac</h3>
  <p class="hint">Official European GNSS Service Centre XML. Online сначала проверяет официальный прямой daily XML вида /sites/default/files/sites/all/files/YYYY-MM-DD.xml за последние дни и только затем product index. Это сохраняет работу, когда /gsc-products/almanac недоступен, а сам XML открывается. Offline принимает сохранённый XML. aSqRoot трактуется как поправка к √A для номинальной полуоси 29 600 км; deltai — поправка к 56°; semicircle = π rad. SHA-256 сохраняется.</p>
  <div class="grid">
    <button onclick="fetchGalileoGscOnline()">Загрузить GSC online / Fetch GSC</button>
    <label>Offline XML <input id="galileoGscFile" type="file" accept=".xml,text/xml,application/xml"></label>
  </div>
  <button onclick="previewGalileoGscOffline()">Прочитать XML / Read XML</button>
  <div id="galileoGscStatus" class="status"></div>
  <pre id="galileoGscPreview"></pre>
</div>
"""


GALILEO_GSC_SCRIPT = r"""
function galileoGscStatusMsg(text,kind=''){galileoGscStatus.textContent=text;galileoGscStatus.className='status '+kind;}
function showGalileoGsc(d){
  const lines=[];
  lines.push('source='+(d.source_url||d.source_filename));
  lines.push('sha256='+d.source_sha256);
  lines.push('records='+d.record_count);
  lines.push('authority='+d.authority_note);
  lines.push('');
  for(const r of d.records.slice(0,12)){
    lines.push(`E${String(r.svid).padStart(2,'0')} a=${r.semi_major_axis_m}m e=${r.eccentricity} i=${r.inclination_rad}rad Ω=${r.raan_rad}rad M=${r.mean_anomaly_rad}rad health(E1B/E5a/E5b)=${r.status_e1b}/${r.status_e5a}/${r.status_e5b}`);
  }
  if(d.records.length>12)lines.push('...');
  galileoGscPreview.textContent=lines.join('\n');
}
async function fetchGalileoGscOnline(){
  galileoGscStatusMsg('Загрузка GSC direct daily / index…');
  const r=await fetch('/api/galileo-gsc/online');
  const d=await r.json();
  if(!r.ok){galileoGscStatusMsg(d.detail||'GSC fetch failed','danger');return;}
  showGalileoGsc(d);galileoGscStatusMsg('VALID ONLINE: '+d.record_count+' Galileo records','ok');
}
async function previewGalileoGscOffline(){
  const file=galileoGscFile.files&&galileoGscFile.files[0];
  if(!file){galileoGscStatusMsg('Выберите XML / Select XML','danger');return;}
  const text=await file.text();
  const r=await fetch('/api/galileo-gsc/offline-preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({filename:file.name,content_text:text})});
  const d=await r.json();
  if(!r.ok){galileoGscStatusMsg(d.detail||'GSC XML parse failed','danger');return;}
  showGalileoGsc(d);galileoGscStatusMsg('VALID OFFLINE: '+d.record_count+' Galileo records','ok');
}
"""


def install_galileo_gsc_routes(app: FastAPI) -> None:
    @app.get("/api/galileo-gsc/source")
    def source() -> dict[str, str]:
        return {"index_url": GSC_ALMANAC_INDEX_URL, "daily_file_prefix": GSC_DAILY_FILE_PREFIX}

    @app.get("/api/galileo-gsc/online")
    def online() -> dict[str, object]:
        try:
            return _payload(fetch_latest_galileo_gsc_almanac())
        except (ValueError, TypeError, OSError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/galileo-gsc/offline-preview")
    def offline_preview(request: GalileoGscOfflineRequest) -> dict[str, object]:
        try:
            return _payload(parse_galileo_gsc_almanac(request.filename, request.content_text))
        except (ValueError, TypeError, OSError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
