"""PLANA.CY Direct ArcGIS Parcel Indexer V3 — 2GB crash-safe.

Render command:
    python indexer.py

Required environment variables:
    SUPABASE_URL
    SUPABASE_SECRET_KEY

Optional environment variables:
    PLANA_INDEX_PAGE_SIZE=1000
    PLANA_INDEX_STALE_DAYS=30
    PLANA_INDEX_SLEEP_SECONDS=0.15
    PLANA_INDEX_CYCLE_SECONDS=86400
    PLANA_INDEX_MAX_PAGES=0
    PLANA_INDEX_UPSERT_BATCH=200
    PLANA_INDEX_SOFT_RSS_MB=1500

Architecture:
- Pages directly through the official DLS ArcGIS Parcels layer.
- Loads Planning Zones and administrative code/name lookups from the official
  DLS ArcGIS map service.
- Spatially joins parcel polygons to planning-zone polygons locally.
- Reuses planning coefficients already learned in plana_parcels by zone code
  (median of existing non-null indexed values).
- Never calls GeneralParcelIdentify in the mass-indexing path.
- Does not import app.py; worker deploys are therefore decoupled from the web app.
"""
from __future__ import annotations

import asyncio
import gc
import json
import math
import os
import random
import statistics
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import httpx
from dotenv import load_dotenv
from pyproj import Geod
from shapely.geometry import shape
from shapely.strtree import STRtree
from supabase import create_client

DLS_MAPSERVER = "https://eservices.dls.moi.gov.cy/arcgis/rest/services/National/CadastralMap_EN/MapServer"
PARCEL_QUERY = f"{DLS_MAPSERVER}/0/query"
ZONE_QUERY = f"{DLS_MAPSERVER}/12/query"
DISTRICT_QUERY = f"{DLS_MAPSERVER}/15/query"
MUNICIPALITY_QUERY = f"{DLS_MAPSERVER}/16/query"
QUARTER_QUERY = f"{DLS_MAPSERVER}/17/query"

GEOD = Geod(ellps="WGS84")


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def force_2d_geometry(geom: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(geom, dict):
        return None

    def xy(value: Any) -> Any:
        if isinstance(value, list):
            if value and all(isinstance(item, (int, float)) for item in value):
                return value[:2]
            return [xy(item) for item in value]
        return value

    return {**geom, "coordinates": xy(geom.get("coordinates"))}


def geometry_metrics(geom_json: dict[str, Any]) -> dict[str, float | None]:
    geom = shape(force_2d_geometry(geom_json) or geom_json)
    centroid = geom.representative_point()

    try:
        signed_area, perimeter = GEOD.geometry_area_perimeter(geom)
        area_m2 = abs(float(signed_area))
        perimeter_m = abs(float(perimeter))
    except Exception:
        area_m2 = 0.0
        perimeter_m = 0.0

    edge_lengths: list[float] = []
    edge_bearings: list[float] = []
    polygons = [geom] if geom.geom_type == "Polygon" else list(getattr(geom, "geoms", []))
    for polygon in polygons:
        coords = list(polygon.exterior.coords)
        for first, second in zip(coords, coords[1:]):
            lon1, lat1 = first[:2]
            lon2, lat2 = second[:2]
            azimuth, _, distance = GEOD.inv(lon1, lat1, lon2, lat2)
            if distance > 0:
                edge_lengths.append(float(distance))
                edge_bearings.append(float(azimuth % 180.0))

    longest = max(edge_lengths) if edge_lengths else None
    shortest = min(edge_lengths) if edge_lengths else None
    orientation = None
    if longest is not None:
        index = edge_lengths.index(longest)
        orientation = edge_bearings[index]

    compactness = None
    if area_m2 > 0 and perimeter_m > 0:
        compactness = clamp(4.0 * math.pi * area_m2 / (perimeter_m * perimeter_m), 0.0, 1.0) * 100.0

    return {
        "centroid_lat": round(float(centroid.y), 8),
        "centroid_lon": round(float(centroid.x), 8),
        "area_m2": round(area_m2, 2) if area_m2 else None,
        "perimeter_m": round(perimeter_m, 2) if perimeter_m else None,
        "longest_edge_m": round(longest, 2) if longest else None,
        "shortest_edge_m": round(shortest, 2) if shortest else None,
        "orientation_deg": round(orientation, 1) if orientation is not None else None,
        "compactness": round(compactness, 1) if compactness is not None else None,
    }


async def arcgis_json(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any],
    *,
    attempts: int = 5,
    timeout: float = 30.0,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = await client.get(url, params=params, timeout=timeout)
            if response.status_code == 200:
                payload = response.json()
                if isinstance(payload, dict) and not payload.get("error"):
                    return payload
                raise RuntimeError(f"ArcGIS payload error: {payload.get('error') if isinstance(payload, dict) else payload!r}")
            raise RuntimeError(f"HTTP {response.status_code}")
        except Exception as exc:
            last_error = exc
            if attempt >= attempts:
                break
            delay = min(2 ** attempt, 30)
            print(f"ArcGIS retry {attempt}/{attempts} url={url.rsplit('/', 2)[-2]} delay={delay}s error={exc!r}", flush=True)
            await asyncio.sleep(delay)
    raise RuntimeError(f"ArcGIS request failed after {attempts} attempts: {last_error!r}")



async def fetch_all_features(
    client: httpx.AsyncClient,
    url: str,
    *,
    out_fields: str,
    return_geometry: bool,
    page_size: int = 1000,
) -> list[dict[str, Any]]:
    """Load a reference layer with OBJECTID keyset pagination.

    Offset paging can skip or duplicate rows when ArcGIS changes while a long
    crawl is running. The reference layers expose OBJECTID, so use the same
    monotonic keyset strategy as the parcel crawler.
    """
    features: list[dict[str, Any]] = []
    last_objectid = 0
    while True:
        params = {
            "f": "geojson",
            "where": f"OBJECTID > {last_objectid}",
            "outFields": out_fields,
            "returnGeometry": str(return_geometry).lower(),
            "outSR": "4326",
            "orderByFields": "OBJECTID ASC",
            "resultRecordCount": page_size,
        }
        payload = await arcgis_json(client, url, params)
        page = payload.get("features") or []
        if not page:
            if payload.get("exceededTransferLimit") is True:
                raise RuntimeError(f"Reference ArcGIS transfer limit reported without a resumable feature page: {url}")
            break
        objectids: list[int] = []
        for feature in page:
            try:
                objectids.append(int(property_dict(feature).get("OBJECTID")))
            except Exception:
                continue
        if not objectids:
            raise RuntimeError(f"Reference ArcGIS page returned rows without usable OBJECTID values: {url}")
        next_objectid = max(objectids)
        if next_objectid <= last_objectid:
            raise RuntimeError(f"Reference ArcGIS OBJECTID cursor did not advance: {url}")
        features.extend(page)
        print(
            f"loaded reference layer={url.rsplit('/', 2)[-2]} after_objectid={last_objectid} "
            f"rows={len(page)} total={len(features)}",
            flush=True,
        )
        last_objectid = next_objectid
    return features


def property_dict(feature: dict[str, Any]) -> dict[str, Any]:
    return feature.get("properties") or feature.get("attributes") or {}


def lookup_from_features(
    features: Iterable[dict[str, Any]],
    code_fields: tuple[str, ...],
    name_field: str,
) -> dict[tuple[int, ...], str]:
    result: dict[tuple[int, ...], str] = {}
    for feature in features:
        props = property_dict(feature)
        try:
            key = tuple(int(props[field]) for field in code_fields)
        except Exception:
            continue
        name = clean_text(props.get(name_field))
        if name:
            result[key] = name
    return result



def load_zone_coefficients(sb: Any) -> dict[str, dict[str, float | None]]:
    """Load zone coefficients without materialising the whole parcel table.

    The SQL RPC deliberately learns from single-zone rows only. Splitting a
    weighted multi-zone parcel such as ``Ka4 / Ka5`` and assigning its weighted
    coefficient to both individual zones contaminates future medians.
    """
    try:
        response = sb.rpc("get_plana_zone_coefficients").execute()
        result: dict[str, dict[str, float | None]] = {}
        for row in response.data or []:
            zone = clean_text(row.get("zone"))
            if not zone:
                continue
            result[zone] = {
                field: num(row.get(field))
                for field in ("density_percent", "coverage_percent", "max_floors", "max_height_m")
            }
        print(f"loaded coefficients for {len(result)} planning zones from Supabase RPC", flush=True)
        return result
    except Exception as exc:
        print(f"zone coefficient RPC fallback: {exc!r}", flush=True)

    grouped: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    offset = 0
    page_size = 1000
    while True:
        response = (
            sb.table("plana_parcels")
            .select("planning_zone,density_percent,coverage_percent,max_floors,max_height_m")
            .not_.is_("planning_zone", "null")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        rows = response.data or []
        for row in rows:
            zone = clean_text(row.get("planning_zone"))
            if not zone or "/" in zone:
                continue
            for field in ("density_percent", "coverage_percent", "max_floors", "max_height_m"):
                value = num(row.get(field))
                if value is not None:
                    grouped[zone][field].append(value)
        if len(rows) < page_size:
            break
        offset += page_size

    result = {
        zone: {
            field: round(float(statistics.median(values)), 2) if values else None
            for field, values in fields.items()
        }
        for zone, fields in grouped.items()
    }
    print(f"learned coefficients for {len(result)} single planning zones via bounded fallback", flush=True)
    return result


class ZoneIndex:
    """Compact Cyprus-wide planning-zone spatial index.

    Only geometry + small metadata tuples are retained. Raw GeoJSON features are
    released after construction, avoiding the duplicate feature/geometry storage
    used by V2.
    """

    def __init__(self, features: list[dict[str, Any]]):
        self.geometries: list[Any] = []
        self.meta: list[tuple[str, str | None]] = []
        for feature in features:
            geom_json = force_2d_geometry(feature.get("geometry"))
            if not geom_json:
                continue
            props = property_dict(feature)
            name = clean_text(props.get("PLNZNT_NAME"))
            if not name:
                continue
            try:
                geom = shape(geom_json)
            except Exception:
                continue
            if geom.is_empty:
                continue
            self.geometries.append(geom)
            self.meta.append((name, clean_text(props.get("PLNZNT_DESC"))))
        self.tree = STRtree(self.geometries)

    def zones_for(self, parcel_geom_json: dict[str, Any], parcel_area_m2: float | None) -> list[dict[str, Any]]:
        parcel_geom = shape(force_2d_geometry(parcel_geom_json) or parcel_geom_json)
        candidates = self.tree.query(parcel_geom)
        rows: list[dict[str, Any]] = []
        for candidate in candidates:
            try:
                index = int(candidate.item()) if hasattr(candidate, "item") else int(candidate)
                zone_geom = self.geometries[index]
            except Exception:
                # Compatibility path for older Shapely STRtree return values.
                zone_geom = candidate
                try:
                    index = self.geometries.index(candidate)
                except ValueError:
                    continue
            if not zone_geom.intersects(parcel_geom):
                continue
            name, description = self.meta[index]
            overlap_percent = None
            try:
                intersection = zone_geom.intersection(parcel_geom)
                signed_area, _ = GEOD.geometry_area_perimeter(intersection)
                intersection_area = abs(float(signed_area))
                if parcel_area_m2 and parcel_area_m2 > 0:
                    overlap_percent = round(clamp(intersection_area / parcel_area_m2 * 100.0), 2)
            except Exception:
                pass
            rows.append(
                {
                    "zone": name,
                    "description": description,
                    "overlap_percent": overlap_percent,
                }
            )
        rows.sort(key=lambda item: item.get("overlap_percent") or 0.0, reverse=True)
        return rows


def coefficient_for_zones(
    zones: list[dict[str, Any]],
    zone_coefficients: dict[str, dict[str, float | None]],
) -> tuple[dict[str, float | None], str]:
    if not zones:
        return {"density_percent": None, "coverage_percent": None, "max_floors": None, "max_height_m": None}, "missing"

    weighted: dict[str, list[tuple[float, float]]] = defaultdict(list)
    matched = 0
    for zone in zones:
        coeff = zone_coefficients.get(zone["zone"])
        if not coeff:
            continue
        matched += 1
        weight = (num(zone.get("overlap_percent")) or 0.0) / 100.0
        if weight <= 0:
            weight = 1.0 / max(len(zones), 1)
        for field, value in coeff.items():
            if value is not None:
                weighted[field].append((float(value), weight))

    output: dict[str, float | None] = {}
    for field in ("density_percent", "coverage_percent", "max_floors", "max_height_m"):
        values = weighted.get(field) or []
        if not values:
            output[field] = None
            continue
        total_weight = sum(weight for _, weight in values)
        output[field] = round(sum(value * weight for value, weight in values) / total_weight, 2)
    status = "matched_all" if matched == len(zones) else ("matched_partial" if matched else "missing")
    return output, status


def status_and_units(area: float | None, density: float | None, floors: float | None) -> dict[str, Any]:
    capacity = area * density / 100.0 if area and density is not None else None
    apt_low = apt_high = house_low = house_high = None
    apartment_status = house_status = mixed_status = "screening_pending_zone_coefficients"
    best_use = "Parcel screening"
    if capacity and capacity > 0:
        apt_mid = capacity * 0.825 / 85.0
        apt_low = max(int(math.floor(apt_mid * 0.85)), 1)
        apt_high = max(int(math.ceil(apt_mid * 1.15)), apt_low)
        house_mid = capacity * 0.78 / 180.0
        house_low = max(int(math.floor(house_mid * 0.85)), 1)
        house_high = max(int(math.ceil(house_mid * 1.15)), house_low)
        house_status = "preliminary_capacity_fit"
        if (floors or 0) >= 2 and density >= 60:
            apartment_status = "preliminary_capacity_fit"
            best_use = "Apartments" if apt_high >= 4 else "Residential development"
        else:
            apartment_status = "lower_density_screen"
            best_use = "Houses"
        mixed_status = "conditional_use_screen"
    return {
        "floor_capacity_m2": round(capacity, 1) if capacity else None,
        "apartment_units_low": apt_low,
        "apartment_units_high": apt_high,
        "house_units_low": house_low,
        "house_units_high": house_high,
        "apartment_status": apartment_status,
        "house_status": house_status,
        "mixed_use_status": mixed_status,
        "best_use": best_use,
    }


def preliminary_scores(
    *,
    area: float | None,
    compactness: float | None,
    shape_ratio: float | None,
    density: float | None,
    coverage: float | None,
    floors: float | None,
    zone_status: str,
) -> dict[str, float | None]:
    planning = 35.0
    if zone_status == "matched_all":
        planning = 84.0
    elif zone_status == "matched_partial":
        planning = 70.0
    elif density is not None:
        planning = 76.0
    elif zone_status != "missing":
        planning = 54.0

    density_signal = clamp((density or 0.0) / 1.4, 0.0, 100.0)
    floor_signal = clamp((floors or 0.0) * 18.0, 0.0, 100.0)
    scale_signal = 50.0
    if area:
        # Broad sweet spot for development screening, without forcing a single scale.
        log_distance = abs(math.log(max(area, 1.0)) - math.log(1800.0))
        scale_signal = clamp(100.0 - log_distance * 28.0, 25.0, 100.0)
    development = round(0.45 * density_signal + 0.25 * floor_signal + 0.30 * scale_signal, 1)

    compact_signal = compactness if compactness is not None else 50.0
    ratio_signal = clamp((shape_ratio or 0.35) * 130.0, 20.0, 100.0)
    site = round(0.55 * compact_signal + 0.45 * ratio_signal, 1)

    opportunity = round(0.36 * planning + 0.39 * development + 0.25 * site, 1)
    confidence = 72.0 if zone_status == "matched_all" else (58.0 if zone_status == "matched_partial" else (44.0 if zone_status != "missing" else 30.0))
    risk_adjusted = round(opportunity * (0.80 + 0.20 * confidence / 100.0), 1)
    return {
        "planning_score": round(planning, 1),
        "development_score": development,
        "site_score": site,
        "market_score": None,
        "financial_score": None,
        "opportunity_score": opportunity,
        "data_confidence": confidence,
        "risk_adjusted_score": risk_adjusted,
    }



def parcel_row(
    feature: dict[str, Any],
    *,
    zone_index: ZoneIndex,
    zone_coefficients: dict[str, dict[str, float | None]],
    district_lookup: dict[tuple[int, ...], str],
    municipality_lookup: dict[tuple[int, ...], str],
    quarter_lookup: dict[tuple[int, ...], str],
) -> dict[str, Any] | None:
    props = property_dict(feature)
    geom_json = force_2d_geometry(feature.get("geometry"))
    if not geom_json:
        return None
    try:
        parcel_id = int(float(props.get("SBPI_ID_NO")))
        source_objectid = int(props.get("OBJECTID"))
    except Exception:
        return None

    metrics = geometry_metrics(geom_json)
    area = num(props.get("SHAPE.STArea()")) or num(props.get("Parcel Extend")) or num(metrics.get("area_m2"))
    raw_zones = zone_index.zones_for(geom_json, area)
    zones: list[dict[str, Any]] = []
    for zone in raw_zones:
        coeff = zone_coefficients.get(str(zone.get("zone"))) or {}
        zones.append({
            **zone,
            "density_percent": num(coeff.get("density_percent")),
            "coverage_percent": num(coeff.get("coverage_percent")),
            "max_floors": num(coeff.get("max_floors")),
            "max_height_m": num(coeff.get("max_height_m")),
        })
    coeff, coeff_status = coefficient_for_zones(zones, zone_coefficients)

    density = num(coeff.get("density_percent"))
    coverage = num(coeff.get("coverage_percent"))
    floors = num(coeff.get("max_floors"))
    max_height = num(coeff.get("max_height_m"))
    longest = num(metrics.get("longest_edge_m"))
    shortest = num(metrics.get("shortest_edge_m"))
    shape_ratio = round(shortest / longest, 4) if longest and shortest else None
    compactness = num(metrics.get("compactness"))

    capacity = status_and_units(area, density, floors)
    score = preliminary_scores(
        area=area,
        compactness=compactness,
        shape_ratio=shape_ratio,
        density=density,
        coverage=coverage,
        floors=floors,
        zone_status=coeff_status if zones else "missing",
    )

    try:
        dist_code = int(props.get("DIST_CODE"))
    except Exception:
        dist_code = None
    try:
        vil_code = int(props.get("VIL_CODE"))
    except Exception:
        vil_code = None
    try:
        qrtr_code = int(props.get("QRTR_CODE"))
    except Exception:
        qrtr_code = None

    district = district_lookup.get((dist_code,)) if dist_code is not None else None
    municipality = municipality_lookup.get((dist_code, vil_code)) if dist_code is not None and vil_code is not None else None
    if municipality is None and vil_code is not None:
        municipality = municipality_lookup.get((vil_code,))
    quarter = quarter_lookup.get((dist_code, vil_code, qrtr_code)) if None not in (dist_code, vil_code, qrtr_code) else None
    if quarter is None and qrtr_code is not None:
        quarter = quarter_lookup.get((qrtr_code,))

    zone_text = " / ".join(str(zone["zone"]) for zone in zones) or None
    overlap_total = sum(num(zone.get("overlap_percent")) or 0.0 for zone in zones) if zones else None
    timestamp = datetime.now(timezone.utc).isoformat()

    return {
        "_source_objectid": source_objectid,
        "parcel_id": parcel_id,
        "parcel_number": props.get("PARCEL_NBR"),
        "district": district,
        "municipality": municipality,
        "quarter": quarter,
        "planning_zone": zone_text,
        "centroid_lat": metrics.get("centroid_lat"),
        "centroid_lon": metrics.get("centroid_lon"),
        "geom": geom_json,
        "parcel_area_m2": round(area, 1) if area else None,
        "perimeter_m": metrics.get("perimeter_m"),
        "longest_edge_m": metrics.get("longest_edge_m"),
        "shortest_edge_m": metrics.get("shortest_edge_m"),
        "shape_ratio": shape_ratio,
        "compactness": compactness,
        "orientation_deg": metrics.get("orientation_deg"),
        "density_percent": density,
        "coverage_percent": coverage,
        "max_floors": floors,
        "max_height_m": max_height,
        "floor_capacity_m2": capacity["floor_capacity_m2"],
        "coverage_capacity_m2": round(area * coverage / 100.0, 1) if area and coverage is not None else None,
        "existing_enclosed_m2": None,
        "development_gap_m2": None,
        "development_gap_percent": None,
        **score,
        "apartment_units_low": capacity["apartment_units_low"],
        "apartment_units_high": capacity["apartment_units_high"],
        "house_units_low": capacity["house_units_low"],
        "house_units_high": capacity["house_units_high"],
        "apartment_status": capacity["apartment_status"],
        "house_status": capacity["house_status"],
        "mixed_use_status": capacity["mixed_use_status"],
        "best_use": capacity["best_use"],
        "score_version": "direct-arcgis-index-v3-resumable-v8",
        "raw_summary": {
            "index_source": "official_dls_arcgis_bulk",
            "coefficient_source": "single_zone_plana_medians" if coeff_status != "missing" else "pending",
            "coefficient_match_status": coeff_status,
            "primary_zone_overlap_percent": zones[0].get("overlap_percent") if zones else None,
            "zone_overlap_total_percent": round(overlap_total, 2) if overlap_total is not None else None,
            "zones": zones,
            "sheet": clean_text(props.get("SHEET")),
            "plan": clean_text(props.get("PLAN_NBR")),
            "block": props.get("BLCK_CODE"),
            "district_code": dist_code,
            "municipality_code": vil_code,
            "quarter_code": qrtr_code,
            "source_objectid": source_objectid,
        },
        "indexed_at": timestamp,
        "dls_refreshed_at": timestamp,
    }


STATE_TABLE = "plana_data_worker_state"
INDEX_PIPELINE = "index_parcels"
INDEX_WORKER_LEASE = "worker:index"
ACTIVE_WORKER_RUN_ID: str | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_rss_mb() -> float | None:
    """Return current Linux RSS, not peak RSS."""
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return float(line.split()[1]) / 1024.0
    except Exception:
        return None
    return None


async def db_retry(callable_, *, attempts: int = 5, label: str = "database"):
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await asyncio.to_thread(callable_)
        except Exception as exc:
            last_error = exc
            if attempt >= attempts:
                break
            delay = min(1.5 * (2 ** (attempt - 1)) + random.random(), 20.0)
            print(f"{label} retry {attempt}/{attempts} delay={delay:.1f}s error={exc!r}", flush=True)
            await asyncio.sleep(delay)
    raise RuntimeError(f"{label} failed after {attempts} attempts: {last_error!r}")


async def state_get(sb: Any, pipeline: str) -> dict[str, Any]:
    response = await db_retry(
        lambda: sb.table(STATE_TABLE).select("*").eq("pipeline", pipeline).limit(1).execute(),
        label=f"state read {pipeline}",
    )
    rows = response.data or []
    return rows[0] if rows else {}



async def state_put(sb: Any, pipeline: str, **values: Any) -> None:
    payload = {"updated_at": now_iso(), **values}
    if not ACTIVE_WORKER_RUN_ID:
        raise RuntimeError("Checkpoint attempted before index worker lease activation")
    await db_retry(
        lambda: sb.rpc(
            "checkpoint_plana_worker_state",
            {
                "p_lease_pipeline": INDEX_WORKER_LEASE,
                "p_worker_run_id": ACTIVE_WORKER_RUN_ID,
                "p_pipeline": pipeline,
                "p_values": payload,
            },
        ).execute(),
        label=f"lease-fenced checkpoint write {pipeline}",
    )



async def claim_worker(sb: Any, pipeline: str, run_id: str, lease_seconds: int = 900) -> bool:
    response = await db_retry(
        lambda: sb.rpc(
            "claim_plana_worker",
            {"p_pipeline": pipeline, "p_run_id": run_id, "p_lease_seconds": lease_seconds},
        ).execute(),
        label=f"claim worker {pipeline}",
    )
    return bool(response.data)


async def heartbeat_worker(sb: Any, pipeline: str, run_id: str, lease_seconds: int = 900) -> None:
    response = await db_retry(
        lambda: sb.rpc(
            "heartbeat_plana_worker",
            {"p_pipeline": pipeline, "p_run_id": run_id, "p_lease_seconds": lease_seconds},
        ).execute(),
        label=f"heartbeat {pipeline}",
    )
    if response.data is False:
        raise RuntimeError(f"Lost worker lease for {pipeline}")


async def release_worker(sb: Any, pipeline: str, run_id: str) -> None:
    try:
        await db_retry(
            lambda: sb.rpc(
                "release_plana_worker",
                {"p_pipeline": pipeline, "p_run_id": run_id},
            ).execute(),
            attempts=3,
            label=f"release worker {pipeline}",
        )
    except Exception as exc:
        print(f"lease release warning {pipeline}: {exc!r}", flush=True)



async def record_worker_failure(
    sb: Any,
    *,
    pipeline: str,
    source_key: str,
    error: str,
    payload: dict[str, Any] | None = None,
) -> None:
    await db_retry(
        lambda: sb.rpc(
            "record_plana_worker_failure",
            {
                "p_lease_pipeline": INDEX_WORKER_LEASE,
                "p_worker_run_id": ACTIVE_WORKER_RUN_ID,
                "p_pipeline": pipeline,
                "p_source_key": source_key,
                "p_error": error[:4000],
                "p_payload": payload or {},
            },
        ).execute(),
        label=f"durable failure {pipeline}/{source_key}",
    )


async def resolve_worker_failures(sb: Any, pipeline: str, source_keys: list[str]) -> None:
    if not source_keys:
        return
    await db_retry(
        lambda: sb.rpc(
            "resolve_plana_worker_failures",
            {
                "p_lease_pipeline": INDEX_WORKER_LEASE,
                "p_worker_run_id": ACTIVE_WORKER_RUN_ID,
                "p_pipeline": pipeline,
                "p_source_keys": source_keys,
            },
        ).execute(),
        label=f"resolve failures {pipeline} rows={len(source_keys)}",
    )


async def upsert_rows(sb: Any, rows: list[dict[str, Any]], batch_size: int) -> tuple[int, int]:
    """Idempotent parcel upsert with durable singleton-failure disposition.

    A page cursor may advance only after every source OBJECTID has either been
    committed to ``plana_parcels`` or committed to ``plana_worker_failures``.
    The next full index cycle retries quarantined OBJECTIDs because they are not
    present as fresh parcel rows.
    """
    written = 0
    failed = 0

    async def write_chunk(chunk: list[dict[str, Any]]) -> None:
        nonlocal written, failed
        if not chunk:
            return
        db_rows = [{k: v for k, v in row.items() if not k.startswith("_")} for row in chunk]
        try:
            await db_retry(
                lambda data=db_rows: sb.rpc(
                    "upsert_plana_parcels",
                    {"p_worker_run_id": ACTIVE_WORKER_RUN_ID, "p_rows": data},
                ).execute(),
                attempts=4,
                label=f"lease-fenced parcel upsert rows={len(chunk)}",
            )
            written += len(chunk)
            await resolve_worker_failures(
                sb,
                INDEX_PIPELINE,
                [str(row.get("_source_objectid")) for row in chunk if row.get("_source_objectid") is not None],
            )
            return
        except Exception as exc:
            if len(chunk) == 1:
                failed += 1
                row = chunk[0]
                source_key = str(row.get("_source_objectid") or row.get("parcel_id") or "unknown")
                await record_worker_failure(
                    sb,
                    pipeline=INDEX_PIPELINE,
                    source_key=source_key,
                    error=f"{type(exc).__name__}: {exc}",
                    payload={
                        "parcel_id": row.get("parcel_id"),
                        "source_objectid": row.get("_source_objectid"),
                    },
                )
                print(f"durably quarantined parcel source_objectid={source_key} error={exc!r}", flush=True)
                return
            middle = len(chunk) // 2
            print(f"split failing parcel batch rows={len(chunk)}", flush=True)
            await write_chunk(chunk[:middle])
            await write_chunk(chunk[middle:])

    for start in range(0, len(rows), batch_size):
        await write_chunk(rows[start : start + batch_size])
    return written, failed


async def run_cycle(sb: Any, client: httpx.AsyncClient, run_id: str, cycle_seconds: int) -> None:
    page_size = max(250, min(env_int("PLANA_INDEX_PAGE_SIZE", 1000), 1000))
    upsert_batch = max(50, min(env_int("PLANA_INDEX_UPSERT_BATCH", 200), 300))
    stale_days = max(1, env_int("PLANA_INDEX_STALE_DAYS", 30))
    sleep_seconds = max(0.0, env_float("PLANA_INDEX_SLEEP_SECONDS", 0.15))
    max_pages = max(0, env_int("PLANA_INDEX_MAX_PAGES", 0))
    soft_rss_mb = max(512, env_int("PLANA_INDEX_SOFT_RSS_MB", 1500))
    cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)

    state = await state_get(sb, INDEX_PIPELINE)
    last_objectid = max(0, int(state.get("cursor_offset") or 0))

    print("PLANA Direct ArcGIS Index V3 2GB RESUMABLE starting", flush=True)
    print(
        f"page_size={page_size} upsert_batch={upsert_batch} stale_days={stale_days} "
        f"resume_objectid={last_objectid} max_pages={max_pages or 'all'}",
        flush=True,
    )
    print("loading compact DLS reference indexes...", flush=True)

    district_features, municipality_features, quarter_features, zone_features = await asyncio.gather(
        fetch_all_features(client, DISTRICT_QUERY, out_fields="DIST_CODE,DIST_NM_E,OBJECTID", return_geometry=False),
        fetch_all_features(client, MUNICIPALITY_QUERY, out_fields="DIST_CODE,VIL_CODE,VIL_NM_E,OBJECTID", return_geometry=False),
        fetch_all_features(client, QUARTER_QUERY, out_fields="DIST_CODE,VIL_CODE,QRTR_CODE,QRTR_NM_E,OBJECTID", return_geometry=False),
        fetch_all_features(client, ZONE_QUERY, out_fields="PLNZNT_NAME,PLNZNT_DESC,OBJECTID", return_geometry=True),
    )

    district_lookup = lookup_from_features(district_features, ("DIST_CODE",), "DIST_NM_E")
    municipality_lookup = lookup_from_features(municipality_features, ("DIST_CODE", "VIL_CODE"), "VIL_NM_E")
    municipality_lookup.update(lookup_from_features(municipality_features, ("VIL_CODE",), "VIL_NM_E"))
    quarter_lookup = lookup_from_features(quarter_features, ("DIST_CODE", "VIL_CODE", "QRTR_CODE"), "QRTR_NM_E")
    quarter_lookup.update(lookup_from_features(quarter_features, ("QRTR_CODE",), "QRTR_NM_E"))
    zone_index = ZoneIndex(zone_features)

    del district_features, municipality_features, quarter_features, zone_features
    gc.collect()

    zone_coefficients = await asyncio.to_thread(load_zone_coefficients, sb)
    rss = current_rss_mb()
    ready = (
        f"reference indexes ready districts={len(district_lookup)} municipalities={len(municipality_lookup)} "
        f"quarters={len(quarter_lookup)} zones={len(zone_index.geometries)} coefficient_zones={len(zone_coefficients)}"
    )
    print(f"{ready} rss_mb={rss:.1f}" if rss is not None else ready, flush=True)

    page_number = 0
    total_discovered = 0
    total_written = 0
    total_skipped_fresh = 0
    total_failed = 0
    await state_put(
        sb,
        INDEX_PIPELINE,
        status="running",
        run_id=run_id,
        cursor_offset=last_objectid,
        last_error=None,
        last_heartbeat_at=now_iso(),
        meta={"cursor_mode": "oid_keyset_v8"},
    )

    while True:
        page_number += 1
        if max_pages and page_number > max_pages:
            print(f"index max_pages={max_pages} reached; preserving OBJECTID checkpoint={last_objectid}", flush=True)
            return

        await heartbeat_worker(sb, INDEX_WORKER_LEASE, run_id, env_int("PLANA_INDEX_LEASE_SECONDS", 900))
        started = time.monotonic()
        params = {
            "f": "geojson",
            "where": f"OBJECTID > {last_objectid}",
            "outFields": "SBPI_ID_NO,DIST_CODE,VIL_CODE,QRTR_CODE,BLCK_CODE,PARCEL_NBR,SHEET,PLAN_NBR,SRC_SL_CODE,SHAPE.STArea(),OBJECTID",
            "returnGeometry": "true",
            "outSR": "4326",
            "orderByFields": "OBJECTID ASC",
            "resultRecordCount": page_size,
        }
        payload = await arcgis_json(client, PARCEL_QUERY, params, attempts=6, timeout=45.0)
        features = payload.get("features") or []
        if not features:
            if payload.get("exceededTransferLimit") is True:
                raise RuntimeError("Parcel ArcGIS transfer limit reported without a resumable feature page")
            next_cycle_at = (datetime.now(timezone.utc) + timedelta(seconds=cycle_seconds)).isoformat()
            print(f"parcel keyset paging complete after OBJECTID={last_objectid}", flush=True)
            await state_put(
                sb,
                INDEX_PIPELINE,
                status="sleeping",
                cursor_offset=0,
                processed_count=total_written,
                last_completed_at=now_iso(),
                last_error=None,
                last_heartbeat_at=now_iso(),
                meta={
                    "cursor_mode": "oid_keyset_v8",
                    "last_completed_objectid": last_objectid,
                    "next_cycle_at": next_cycle_at,
                },
            )
            break

        page_objectids: list[int] = []
        by_id: dict[int, dict[str, Any]] = {}
        invalid_identity: list[tuple[int, str, dict[str, Any]]] = []
        for feature in features:
            props = property_dict(feature)
            try:
                objectid = int(props.get("OBJECTID"))
            except Exception as exc:
                raise RuntimeError(f"ArcGIS page row missing usable OBJECTID: {exc}") from exc
            page_objectids.append(objectid)
            try:
                parcel_id = int(float(props.get("SBPI_ID_NO")))
            except Exception as exc:
                invalid_identity.append((objectid, f"{type(exc).__name__}: {exc}", {"properties": props}))
                continue
            by_id[parcel_id] = feature

        if not page_objectids:
            raise RuntimeError("ArcGIS page returned no usable OBJECTID values")
        next_objectid = max(page_objectids)
        if next_objectid <= last_objectid:
            raise RuntimeError("ArcGIS OBJECTID keyset cursor did not advance")
        total_discovered += len(features)

        for objectid, error, failure_payload in invalid_identity:
            await record_worker_failure(
                sb,
                pipeline=INDEX_PIPELINE,
                source_key=str(objectid),
                error=error,
                payload=failure_payload,
            )

        ids = list(by_id)
        existing: dict[int, str | None] = {}
        for start in range(0, len(ids), 500):
            subset = ids[start : start + 500]
            response = await db_retry(
                lambda values=subset: sb.table("plana_parcels")
                .select("parcel_id,indexed_at")
                .in_("parcel_id", values)
                .execute(),
                label=f"existing parcel lookup rows={len(subset)}",
            )
            for row in response.data or []:
                try:
                    existing[int(row["parcel_id"])] = row.get("indexed_at")
                except Exception:
                    continue

        work: list[dict[str, Any]] = []
        skipped_fresh = 0
        for parcel_id, feature in by_id.items():
            stamp = existing.get(parcel_id)
            if stamp:
                try:
                    parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                    if parsed > cutoff:
                        skipped_fresh += 1
                        continue
                except Exception:
                    pass
            work.append(feature)

        rows: list[dict[str, Any]] = []
        parse_failed = len(invalid_identity)
        for feature in work:
            props = property_dict(feature)
            source_objectid = str(props.get("OBJECTID"))
            try:
                row = parcel_row(
                    feature,
                    zone_index=zone_index,
                    zone_coefficients=zone_coefficients,
                    district_lookup=district_lookup,
                    municipality_lookup=municipality_lookup,
                    quarter_lookup=quarter_lookup,
                )
                if row is None:
                    raise ValueError("parcel row could not be constructed")
                rows.append(row)
            except Exception as exc:
                parse_failed += 1
                await record_worker_failure(
                    sb,
                    pipeline=INDEX_PIPELINE,
                    source_key=source_objectid,
                    error=f"{type(exc).__name__}: {exc}",
                    payload={"parcel_id": props.get("SBPI_ID_NO"), "source_objectid": props.get("OBJECTID")},
                )
                print(f"durably quarantined parse source_objectid={source_objectid} error={exc!r}", flush=True)

        written, write_failed = await upsert_rows(sb, rows, upsert_batch)
        total_written += written
        total_skipped_fresh += skipped_fresh
        total_failed += parse_failed + write_failed

        # Every source OBJECTID now has a durable outcome: fresh existing data,
        # committed parcel data, or a committed failure-ledger record.
        last_objectid = next_objectid
        page_failures = parse_failed + write_failed
        await heartbeat_worker(sb, INDEX_WORKER_LEASE, run_id, env_int("PLANA_INDEX_LEASE_SECONDS", 900))
        await state_put(
            sb,
            INDEX_PIPELINE,
            status="running",
            run_id=run_id,
            cursor_offset=last_objectid,
            processed_count=total_written,
            consecutive_failures=0 if page_failures == 0 else page_failures,
            last_error=None if page_failures == 0 else f"durably quarantined page rows={page_failures}",
            last_heartbeat_at=now_iso(),
            meta={
                "cursor_mode": "oid_keyset_v8",
                "page_number": page_number,
                "discovered": len(features),
                "work": len(work),
                "fresh": skipped_fresh,
                "written": written,
                "quarantined": page_failures,
            },
        )

        elapsed = time.monotonic() - started
        rss = current_rss_mb()
        rss_text = f" rss_mb={rss:.1f}" if rss is not None else ""
        print(
            f"page {page_number} after_objectid={last_objectid} discovered={len(features)} work={len(work)} "
            f"fresh={skipped_fresh} written={written} quarantined={page_failures} "
            f"total_written={total_written} elapsed={elapsed:.1f}s{rss_text}",
            flush=True,
        )

        del payload, features, by_id, ids, existing, work, rows, page_objectids, invalid_identity
        if rss is not None and rss > soft_rss_mb:
            print(f"soft RSS threshold exceeded ({rss:.1f}>{soft_rss_mb} MB); forcing cleanup and cooling 5s", flush=True)
            gc.collect()
            await asyncio.sleep(5)
        elif page_number % 10 == 0:
            gc.collect()

        if sleep_seconds:
            await asyncio.sleep(sleep_seconds)

    print(
        f"PLANA Direct ArcGIS Index V3 cycle complete discovered={total_discovered} "
        f"written={total_written} fresh_skipped={total_skipped_fresh} quarantined={total_failed}",
        flush=True,
    )



def _future_wait_seconds(value: Any) -> float:
    if not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (parsed.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return 0.0


async def index_lease_heartbeat_loop(run_id: str, lease_seconds: int) -> None:
    lease_sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
    interval = max(30, min(120, lease_seconds // 3))
    while True:
        await asyncio.sleep(interval)
        await heartbeat_worker(lease_sb, INDEX_WORKER_LEASE, run_id, lease_seconds)


async def index_worker_loop(
    sb: Any,
    client: httpx.AsyncClient,
    run_id: str,
    cycle_seconds: int,
) -> None:
    while True:
        checkpoint = await state_get(sb, INDEX_PIPELINE)
        wait_seconds = _future_wait_seconds((checkpoint.get("meta") or {}).get("next_cycle_at"))
        if checkpoint.get("status") in {"done", "sleeping"} and wait_seconds > 0:
            print(
                f"index durable cycle gate active; next cycle in {int(wait_seconds)}s "
                f"(last_completed_objectid={(checkpoint.get('meta') or {}).get('last_completed_objectid')})",
                flush=True,
            )
            await asyncio.sleep(min(wait_seconds, 300.0))
            continue
        try:
            await run_cycle(sb, client, run_id, cycle_seconds)
        except Exception as exc:
            print(f"index cycle failed: {exc!r}", flush=True)
            await state_put(
                sb,
                INDEX_PIPELINE,
                status="error",
                run_id=run_id,
                last_error=f"{type(exc).__name__}: {exc}",
                last_heartbeat_at=now_iso(),
            )
            print("sleeping 300 seconds before exact-checkpoint resume", flush=True)
            await asyncio.sleep(300)


async def main() -> None:
    load_dotenv()
    supabase_url = require_env("SUPABASE_URL")
    supabase_key = require_env("SUPABASE_SECRET_KEY")
    sb = create_client(supabase_url, supabase_key)
    cycle_seconds = max(3600, env_int("PLANA_INDEX_CYCLE_SECONDS", 86400))
    lease_seconds = max(300, env_int("PLANA_INDEX_LEASE_SECONDS", 900))
    run_id = str(uuid.uuid4())

    claimed = await claim_worker(sb, INDEX_WORKER_LEASE, run_id, lease_seconds=lease_seconds)
    if not claimed:
        raise RuntimeError("Another PLANA INDEX worker owns the active lease; refusing duplicate crawl")
    global ACTIVE_WORKER_RUN_ID
    ACTIVE_WORKER_RUN_ID = run_id

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(45.0, connect=10.0),
        limits=httpx.Limits(max_connections=12, max_keepalive_connections=6),
        follow_redirects=True,
        headers={
            "User-Agent": "PLANA.CY-Direct-ArcGIS-Indexer/3.0",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://eservices.dls.moi.gov.cy/",
        },
    ) as client:
        worker_task = asyncio.create_task(index_worker_loop(sb, client, run_id, cycle_seconds))
        heartbeat_task = asyncio.create_task(index_lease_heartbeat_loop(run_id, lease_seconds))
        try:
            done, pending = await asyncio.wait(
                {worker_task, heartbeat_task},
                return_when=asyncio.FIRST_EXCEPTION,
            )
            for task in done:
                exc = task.exception()
                if exc is not None:
                    raise exc
            raise RuntimeError("PLANA INDEX worker task stopped unexpectedly")
        finally:
            for task in (worker_task, heartbeat_task):
                task.cancel()
            await asyncio.gather(worker_task, heartbeat_task, return_exceptions=True)
            await release_worker(sb, INDEX_WORKER_LEASE, run_id)


if __name__ == "__main__":
    asyncio.run(main())
