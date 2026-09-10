from __future__ import annotations

import json
from datetime import date, datetime
from math import pi
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request

from pydantic import BaseModel, ConfigDict, Field

from constellation_control.adapters.orekit.http import open_orekit_url
from constellation_control.domain.models import (
    ForceModelConfig,
    FrameName,
    MeanOrbit,
    SpacecraftModel,
    TimeScaleName,
)


class OsculatingKeplerianElements(BaseModel):
    model_config = ConfigDict(frozen=True)

    a_m: float = Field(gt=0.0)
    e: float = Field(ge=0.0, lt=1.0)
    i_rad: float = Field(ge=0.0, lt=pi)
    pa_rad: float
    raan_rad: float
    anomaly_rad: float
    anomaly_type: Literal["mean", "eccentric", "true"]


class MeanConversionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    mean_orbit: MeanOrbit
    backend_metadata: dict[str, str]


def _post_conversion(url: str, payload: dict[str, object], timeout_s: float, label: str) -> MeanConversionResult:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    request = Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with open_orekit_url(request, timeout_s) as response:
            raw = response.read().decode()
    except HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise RuntimeError(f"Orekit {label} HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"Orekit {label} connection failed: {error.reason}") from error
    except TimeoutError as error:
        raise RuntimeError(f"Orekit {label} exceeded {timeout_s:.0f} s") from error
    return MeanConversionResult.model_validate(json.loads(raw))


def _verify_common_result(
    result: MeanConversionResult,
    *,
    requested_gravity: str,
    fingerprint: str,
) -> None:
    metadata = result.backend_metadata
    if metadata.get("backend") != "orekit-dsst-mean-conversion":
        raise RuntimeError("mean conversion returned unexpected backend identity")
    if metadata.get("gravity_model") != requested_gravity:
        raise RuntimeError("mean conversion gravity authority does not match request")
    if not metadata.get("orekit_version"):
        raise RuntimeError("mean conversion omitted Orekit version")
    if not metadata.get("orekit_data_sha256"):
        raise RuntimeError("mean conversion omitted Orekit-data fingerprint")
    definition = result.mean_orbit.definition
    if definition.force_model_fingerprint != fingerprint:
        raise RuntimeError("mean conversion force-model fingerprint does not match request")
    if not definition.theory.startswith("orekit-dsst-"):
        raise RuntimeError("mean conversion returned an unrecognized mean-element definition")


class OrekitMeanConversionClient:
    """Convert Keplerian osculating elements to canonical mean elements via Orekit DSST."""

    def __init__(self, base_url: str, timeout_s: float = 60.0) -> None:
        self._url = base_url.rstrip("/") + "/v1/orbits/osculating-to-mean"
        self._timeout_s = timeout_s

    def convert(
        self,
        *,
        epoch: datetime,
        frame: FrameName,
        time_scale: TimeScaleName,
        elements: OsculatingKeplerianElements,
        spacecraft: SpacecraftModel,
        force_model: ForceModelConfig,
    ) -> MeanConversionResult:
        requested_gravity = force_model.gravity_model
        if requested_gravity is None:
            raise RuntimeError("osculating-to-mean conversion requires explicit gravity authority")
        fingerprint = force_model.fingerprint()
        payload: dict[str, object] = {
            "epoch": epoch.isoformat().replace("+00:00", "Z"),
            "frame": frame.value,
            "time_scale": time_scale.value,
            **elements.model_dump(mode="json"),
            "spacecraft": spacecraft.model_dump(mode="json"),
            "force_model": force_model.model_dump(mode="json"),
            "force_model_fingerprint": fingerprint,
        }
        result = _post_conversion(self._url, payload, self._timeout_s, "mean conversion")
        _verify_common_result(result, requested_gravity=requested_gravity.value, fingerprint=fingerprint)
        return result


class OrekitTleMeanConversionClient:
    """Convert raw TLE through Orekit SGP4/TEME at an explicit target epoch, then DSST mean authority."""

    def __init__(self, base_url: str, timeout_s: float = 60.0) -> None:
        self._url = base_url.rstrip("/") + "/v1/orbits/tle-to-mean"
        self._timeout_s = timeout_s

    def convert(
        self,
        *,
        line1: str,
        line2: str,
        frame: FrameName,
        target_epoch: datetime,
        target_time_scale: TimeScaleName,
        spacecraft: SpacecraftModel,
        force_model: ForceModelConfig,
    ) -> MeanConversionResult:
        requested_gravity = force_model.gravity_model
        if requested_gravity is None:
            raise RuntimeError("TLE-to-mean conversion requires explicit gravity authority")
        fingerprint = force_model.fingerprint()
        target_epoch_text = target_epoch.isoformat().replace("+00:00", "Z")
        payload: dict[str, object] = {
            "line1": line1,
            "line2": line2,
            "frame": frame.value,
            "target_epoch": target_epoch_text,
            "target_time_scale": target_time_scale.value,
            "spacecraft": spacecraft.model_dump(mode="json"),
            "force_model": force_model.model_dump(mode="json"),
            "force_model_fingerprint": fingerprint,
        }
        result = _post_conversion(self._url, payload, self._timeout_s, "TLE conversion")
        _verify_common_result(result, requested_gravity=requested_gravity.value, fingerprint=fingerprint)
        metadata = result.backend_metadata
        if metadata.get("source_authority") != "NORAD-TLE-SGP4":
            raise RuntimeError("TLE conversion returned unexpected source authority")
        if metadata.get("sgp4_frame") != "TEME":
            raise RuntimeError("TLE conversion did not attest TEME SGP4 authority")
        chain = metadata.get("conversion_chain", "")
        if "TLE->Orekit-SGP4/TEME" not in chain or "Orekit-DSST-mean" not in chain:
            raise RuntimeError("TLE conversion omitted the reviewed authority chain")
        if not metadata.get("norad_satellite_number"):
            raise RuntimeError("TLE conversion omitted NORAD satellite number")
        if not metadata.get("tle_epoch"):
            raise RuntimeError("TLE conversion omitted source TLE epoch")
        if metadata.get("sgp4_target_time_scale") != target_time_scale.value:
            raise RuntimeError("TLE conversion target time scale does not match request")
        if not metadata.get("sgp4_target_epoch"):
            raise RuntimeError("TLE conversion omitted target epoch")
        return result


class OrekitGpsAlmanacMeanConversionClient:
    """Convert GPS YUMA/SEM source through Orekit GNSS propagation, then DSST mean authority."""

    def __init__(self, base_url: str, timeout_s: float = 60.0) -> None:
        self._url = base_url.rstrip("/") + "/v1/orbits/gps-almanac-to-mean"
        self._timeout_s = timeout_s

    def convert(
        self,
        *,
        source_format: Literal["gps-yuma", "gps-sem"],
        source_name: str,
        source_text: str,
        prn: int,
        frame: FrameName,
        target_epoch: datetime,
        target_time_scale: TimeScaleName,
        spacecraft: SpacecraftModel,
        force_model: ForceModelConfig,
    ) -> MeanConversionResult:
        requested_gravity = force_model.gravity_model
        if requested_gravity is None:
            raise RuntimeError("GPS almanac-to-mean conversion requires explicit gravity authority")
        if prn <= 0:
            raise ValueError("GPS almanac PRN must be positive")
        fingerprint = force_model.fingerprint()
        payload: dict[str, object] = {
            "source_format": source_format,
            "source_name": source_name,
            "source_text": source_text,
            "prn": prn,
            "frame": frame.value,
            "target_epoch": target_epoch.isoformat().replace("+00:00", "Z"),
            "target_time_scale": target_time_scale.value,
            "spacecraft": spacecraft.model_dump(mode="json"),
            "force_model": force_model.model_dump(mode="json"),
            "force_model_fingerprint": fingerprint,
        }
        result = _post_conversion(self._url, payload, self._timeout_s, "GPS almanac conversion")
        _verify_common_result(result, requested_gravity=requested_gravity.value, fingerprint=fingerprint)
        metadata = result.backend_metadata
        if metadata.get("source_authority") != "GPS-ALMANAC-OREKIT-GNSS":
            raise RuntimeError("GPS almanac conversion returned unexpected source authority")
        if metadata.get("almanac_source_format") != source_format:
            raise RuntimeError("GPS almanac conversion source format does not match request")
        if metadata.get("gps_prn") != str(prn):
            raise RuntimeError("GPS almanac conversion PRN does not match request")
        if metadata.get("gnss_target_time_scale") != target_time_scale.value:
            raise RuntimeError("GPS almanac conversion target time scale does not match request")
        chain = metadata.get("conversion_chain", "")
        if "Orekit-GNSS-propagator" not in chain or "Orekit-DSST-mean" not in chain:
            raise RuntimeError("GPS almanac conversion omitted the reviewed authority chain")
        if not metadata.get("almanac_epoch") or not metadata.get("gnss_target_epoch"):
            raise RuntimeError("GPS almanac conversion omitted epoch attestation")
        return result


class OrekitGlonassAlmanacMeanConversionClient:
    """Convert an authority-ready GLONASS almanac record through Orekit analytical propagation and DSST."""

    def __init__(self, base_url: str, timeout_s: float = 60.0) -> None:
        self._url = base_url.rstrip("/") + "/v1/orbits/glonass-almanac-to-mean"
        self._timeout_s = timeout_s

    def convert(
        self,
        *,
        source_name: str,
        slot: int,
        frequency_channel: int,
        health: int,
        reference_date: date,
        reference_time_s: float,
        lambda_rad: float,
        delta_i_rad: float,
        argument_of_perigee_rad: float,
        eccentricity: float,
        delta_t_s: float,
        delta_t_dot: float,
        glo_to_utc_s: float,
        gps_to_glo_s: float,
        glo_time_offset_s: float,
        frame: FrameName,
        target_epoch: datetime,
        target_time_scale: TimeScaleName,
        spacecraft: SpacecraftModel,
        force_model: ForceModelConfig,
    ) -> MeanConversionResult:
        requested_gravity = force_model.gravity_model
        if requested_gravity is None:
            raise RuntimeError("GLONASS almanac-to-mean conversion requires explicit gravity authority")
        if not 1 <= slot <= 63:
            raise ValueError("GLONASS slot must be in 1..63")
        if not -7 <= frequency_channel <= 6:
            raise ValueError("GLONASS frequency channel must be in -7..6")
        fingerprint = force_model.fingerprint()
        payload: dict[str, object] = {
            "source_name": source_name,
            "slot": slot,
            "frequency_channel": frequency_channel,
            "health": health,
            "reference_date": reference_date.isoformat(),
            "reference_time_s": reference_time_s,
            "lambda_rad": lambda_rad,
            "delta_irad": delta_i_rad,
            "argument_of_perigee_rad": argument_of_perigee_rad,
            "eccentricity": eccentricity,
            "delta_ts": delta_t_s,
            "delta_tdot": delta_t_dot,
            "glo_to_utc_s": glo_to_utc_s,
            "gps_to_glo_s": gps_to_glo_s,
            "glo_time_offset_s": glo_time_offset_s,
            "frame": frame.value,
            "target_epoch": target_epoch.isoformat().replace("+00:00", "Z"),
            "target_time_scale": target_time_scale.value,
            "spacecraft": spacecraft.model_dump(mode="json"),
            "force_model": force_model.model_dump(mode="json"),
            "force_model_fingerprint": fingerprint,
        }
        result = _post_conversion(self._url, payload, self._timeout_s, "GLONASS almanac conversion")
        _verify_common_result(result, requested_gravity=requested_gravity.value, fingerprint=fingerprint)
        metadata = result.backend_metadata
        if metadata.get("source_authority") != "GLONASS-ALMANAC-OREKIT-ANALYTICAL":
            raise RuntimeError("GLONASS almanac conversion returned unexpected source authority")
        if metadata.get("almanac_source_format") != "glonass-labelled-authority-v1":
            raise RuntimeError("GLONASS almanac conversion returned unexpected source format")
        if metadata.get("glonass_slot") != str(slot):
            raise RuntimeError("GLONASS almanac conversion slot does not match request")
        if metadata.get("frequency_channel") != str(frequency_channel):
            raise RuntimeError("GLONASS almanac conversion frequency channel does not match request")
        if metadata.get("glonass_target_time_scale") != target_time_scale.value:
            raise RuntimeError("GLONASS almanac conversion target time scale does not match request")
        chain = metadata.get("conversion_chain", "")
        if "Orekit-GLONASSAnalyticalPropagator" not in chain or "Orekit-DSST-mean" not in chain:
            raise RuntimeError("GLONASS almanac conversion omitted the reviewed authority chain")
        if not metadata.get("almanac_epoch") or not metadata.get("glonass_target_epoch"):
            raise RuntimeError("GLONASS almanac conversion omitted epoch attestation")
        return result


class RinexGlonassSatelliteMean(BaseModel):
    model_config = ConfigDict(frozen=True)
    satellite_id: str
    prn: int
    frequency_channel: int
    health_flags: int
    ephemeris_epoch: str
    ephemeris_age_s: float
    mean_orbit: MeanOrbit
    backend_metadata: dict[str, str]


class RinexGlonassToMeanResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    satellites: tuple[RinexGlonassSatelliteMean, ...]
    backend_metadata: dict[str, str]


class OrekitRinexGlonassMeanConversionClient:
    def __init__(self, base_url: str, timeout_s: float = 120.0) -> None:
        self._url = base_url.rstrip("/") + "/v1/orbits/rinex-glonass-to-mean"
        self._timeout_s = timeout_s

    def convert(
        self,
        *,
        source_name: str,
        source_text: str,
        frame: FrameName,
        target_epoch: datetime,
        target_time_scale: TimeScaleName,
        max_ephemeris_age_s: float,
        glonass_propagation_step_s: float,
        spacecraft: SpacecraftModel,
        force_model: ForceModelConfig,
    ) -> RinexGlonassToMeanResult:
        if force_model.gravity_model is None:
            raise RuntimeError("RINEX GLONASS conversion requires explicit gravity authority")
        fingerprint = force_model.fingerprint()
        payload = {
            "source_name": source_name,
            "source_text": source_text,
            "frame": frame.value,
            "target_epoch": target_epoch.isoformat().replace("+00:00", "Z"),
            "target_time_scale": target_time_scale.value,
            "max_ephemeris_age_s": max_ephemeris_age_s,
            "glonass_propagation_step_s": glonass_propagation_step_s,
            "spacecraft": spacecraft.model_dump(mode="json"),
            "force_model": force_model.model_dump(mode="json"),
            "force_model_fingerprint": fingerprint,
        }
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        request = Request(self._url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with open_orekit_url(request, self._timeout_s) as response:
                raw = response.read().decode()
        except HTTPError as error:
            detail = error.read().decode(errors="replace")
            raise RuntimeError(f"Orekit RINEX GLONASS HTTP {error.code}: {detail}") from error
        except URLError as error:
            raise RuntimeError(f"Orekit RINEX GLONASS connection failed: {error.reason}") from error
        except TimeoutError as error:
            raise RuntimeError(f"Orekit RINEX GLONASS exceeded {self._timeout_s:.0f} s") from error
        result = RinexGlonassToMeanResult.model_validate(json.loads(raw))
        if not result.satellites:
            raise RuntimeError("Orekit RINEX GLONASS returned no satellites")
        for item in result.satellites:
            if item.mean_orbit.definition.force_model_fingerprint != fingerprint:
                raise RuntimeError("RINEX GLONASS mean force-model fingerprint mismatch")
            if item.backend_metadata.get("source_authority") != "IGS-BKG-RINEX-NAV":
                raise RuntimeError("unexpected RINEX GLONASS source authority")
        return result


class RinexGnssSatelliteMean(BaseModel):
    model_config = ConfigDict(frozen=True)
    satellite_id: str
    prn: int
    ephemeris_epoch: str
    ephemeris_age_s: float
    mean_orbit: MeanOrbit
    backend_metadata: dict[str, str]


class RinexGnssToMeanResult(BaseModel):
    model_config = ConfigDict(frozen=True)
    satellites: tuple[RinexGnssSatelliteMean, ...]
    backend_metadata: dict[str, str]


class OrekitRinexGnssMeanConversionClient:
    def __init__(self, base_url: str, timeout_s: float = 120.0) -> None:
        self._url = base_url.rstrip("/") + "/v1/orbits/rinex-gnss-to-mean"
        self._timeout_s = timeout_s

    def convert(
        self,
        *,
        system: Literal["GPS", "Galileo", "BeiDou"],
        source_name: str,
        source_text: str,
        frame: FrameName,
        target_epoch: datetime,
        target_time_scale: TimeScaleName,
        max_ephemeris_age_s: float,
        spacecraft: SpacecraftModel,
        force_model: ForceModelConfig,
    ) -> RinexGnssToMeanResult:
        if force_model.gravity_model is None:
            raise RuntimeError("RINEX GNSS conversion requires explicit gravity authority")
        fingerprint = force_model.fingerprint()
        payload = {
            "system": system,
            "source_name": source_name,
            "source_text": source_text,
            "frame": frame.value,
            "target_epoch": target_epoch.isoformat().replace("+00:00", "Z"),
            "target_time_scale": target_time_scale.value,
            "max_ephemeris_age_s": max_ephemeris_age_s,
            "spacecraft": spacecraft.model_dump(mode="json"),
            "force_model": force_model.model_dump(mode="json"),
            "force_model_fingerprint": fingerprint,
        }
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        request = Request(self._url, data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with open_orekit_url(request, self._timeout_s) as response:
                raw = response.read().decode()
        except HTTPError as error:
            detail = error.read().decode(errors="replace")
            raise RuntimeError(f"Orekit RINEX GNSS HTTP {error.code}: {detail}") from error
        except URLError as error:
            raise RuntimeError(f"Orekit RINEX GNSS connection failed: {error.reason}") from error
        except TimeoutError as error:
            raise RuntimeError(f"Orekit RINEX GNSS exceeded {self._timeout_s:.0f} s") from error
        result = RinexGnssToMeanResult.model_validate(json.loads(raw))
        if not result.satellites:
            raise RuntimeError(f"Orekit RINEX {system} returned no satellites")
        for item in result.satellites:
            if item.mean_orbit.definition.force_model_fingerprint != fingerprint:
                raise RuntimeError("RINEX GNSS mean force-model fingerprint mismatch")
            if item.backend_metadata.get("source_authority") != "IGS-BKG-RINEX-NAV":
                raise RuntimeError("unexpected RINEX GNSS source authority")
        return result
