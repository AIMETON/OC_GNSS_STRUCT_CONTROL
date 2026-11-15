from __future__ import annotations

from datetime import datetime

from constellation_control.adapters.orekit.mean_conversion import (
    MeanConversionResult,
    _post_conversion,
    _verify_common_result,
)
from constellation_control.domain.models import ForceModelConfig, FrameName, SpacecraftModel, TimeScaleName


class OrekitGalileoAlmanacMeanConversionClient:
    """Promote one official GSC Galileo almanac record through Orekit GNSS propagation and DSST mean authority."""

    def __init__(self, base_url: str, timeout_s: float = 60.0) -> None:
        # Reuse the reviewed GNSS-almanac sidecar endpoint. The Java engine
        # dispatches source_format=galileo-gsc to Orekit GalileoAlmanac.
        self._url = base_url.rstrip("/") + "/v1/orbits/gps-almanac-to-mean"
        self._timeout_s = timeout_s

    def convert(
        self,
        *,
        source_name: str,
        source_text: str,
        svid: int,
        frame: FrameName,
        target_epoch: datetime,
        target_time_scale: TimeScaleName,
        spacecraft: SpacecraftModel,
        force_model: ForceModelConfig,
    ) -> MeanConversionResult:
        requested_gravity = force_model.gravity_model
        if requested_gravity is None:
            raise RuntimeError("Galileo GSC almanac-to-mean conversion requires explicit gravity authority")
        if not 1 <= svid <= 36:
            raise ValueError("Galileo SVID must be in 1..36")
        fingerprint = force_model.fingerprint()
        payload: dict[str, object] = {
            "source_format": "galileo-gsc",
            "source_name": source_name,
            "source_text": source_text,
            "prn": svid,
            "frame": frame.value,
            "target_epoch": target_epoch.isoformat().replace("+00:00", "Z"),
            "target_time_scale": target_time_scale.value,
            "spacecraft": spacecraft.model_dump(mode="json"),
            "force_model": force_model.model_dump(mode="json"),
            "force_model_fingerprint": fingerprint,
        }
        result = _post_conversion(self._url, payload, self._timeout_s, "Galileo GSC almanac conversion")
        _verify_common_result(result, requested_gravity=requested_gravity.value, fingerprint=fingerprint)
        metadata = result.backend_metadata
        if metadata.get("source_authority") != "GALILEO-GSC-ALMANAC-OREKIT-GNSS":
            raise RuntimeError("Galileo GSC conversion returned unexpected source authority")
        if metadata.get("almanac_source_format") != "galileo-gsc":
            raise RuntimeError("Galileo GSC conversion source format does not match request")
        if metadata.get("galileo_svid") != str(svid):
            raise RuntimeError("Galileo GSC conversion SVID does not match request")
        if metadata.get("gnss_target_time_scale") != target_time_scale.value:
            raise RuntimeError("Galileo GSC conversion target time scale does not match request")
        chain = metadata.get("conversion_chain", "")
        if "Orekit-GalileoAlmanac" not in chain or "Orekit-GNSS-propagator" not in chain or "Orekit-DSST-mean" not in chain:
            raise RuntimeError("Galileo GSC conversion omitted the reviewed authority chain")
        if not metadata.get("gsc_issue_date") or not metadata.get("almanac_epoch"):
            raise RuntimeError("Galileo GSC conversion omitted epoch attestation")
        return result
