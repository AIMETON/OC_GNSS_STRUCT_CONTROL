from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

from constellation_control.adapters.reviewed_http_fetch import fetch_reviewed_url


@dataclass(frozen=True)
class FcndDataFile:
    raw: dict[str, Any]

    @property
    def file_name(self) -> str | None:
        for key in ("file_name", "filename", "name"):
            value = self.raw.get(key)
            if isinstance(value, str) and value:
                return value
        return None


class FcndApiClient:
    """Small fail-closed client for the documented FCND HTTP API.

    The API documentation states that catalogue/filter/station methods return JSON,
    while the datafile endpoint returns the file payload itself.
    """

    def __init__(self, base_url: str = "https://fcnd.ru", timeout_s: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def _json_get(self, path: str, params: list[tuple[str, str]] | None = None) -> Any:
        url = self.base_url + path
        if params:
            url += "?" + urlencode(params)
        response = fetch_reviewed_url(url, timeout_s=self.timeout_s)
        try:
            return json.loads(response.raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"FCND API returned non-JSON payload for {path}") from exc

    def get_filter(self) -> Any:
        return self._json_get("/api/getFilter")

    def get_stations(self) -> Any:
        return self._json_get("/api/getStations")

    def get_station(self, station_id: int, lang: str = "EN") -> Any:
        if station_id <= 0:
            raise ValueError("FCND station id must be positive")
        return self._json_get("/api/getStation/", [("id", str(station_id)), ("lang", lang)])

    @staticmethod
    def _dt(value: datetime | str) -> str:
        if isinstance(value, datetime):
            return value.strftime("%d-%m-%Y %H:%M:%S")
        return value

    def list_data(
        self,
        *,
        time_begin: datetime | str,
        time_end: datetime | str,
        stations: Iterable[str] = (),
        content_type: str | None = None,
        data_type: str | None = None,
        meta_collections: Iterable[int] = (),
        orgs: Iterable[int] = (),
        limit: int | None = None,
    ) -> Any:
        params: list[tuple[str, str]] = [
            ("filter[time_begin]", self._dt(time_begin)),
            ("filter[time_end]", self._dt(time_end)),
        ]
        params.extend(("filter[station][]", str(value)) for value in stations)
        if content_type:
            params.append(("filter[content_type]", content_type))
        if data_type:
            params.append(("filter[data_type]", data_type))
        params.extend(("filter[meta_collection][]", str(value)) for value in meta_collections)
        params.extend(("filter[org][]", str(value)) for value in orgs)
        if limit is not None:
            if limit <= 0:
                raise ValueError("FCND limit must be positive")
            params.append(("filter[limit]", str(limit)))
        return self._json_get("/api/getData/", params)

    def download_datafile(self, *, time_begin: datetime | str, file_name: str) -> bytes:
        if not file_name.strip():
            raise ValueError("FCND file_name is required")
        value = time_begin.strftime("%Y-%m-%d %H:%M:%S") if isinstance(time_begin, datetime) else time_begin
        query = urlencode(
            [
                ("datafile[time_begin]", value),
                ("datafile[file_name]", file_name),
            ]
        )
        response = fetch_reviewed_url(self.base_url + "/api/getData/?" + query, timeout_s=self.timeout_s)
        if not response.raw:
            raise ValueError("FCND datafile response is empty")
        return response.raw
