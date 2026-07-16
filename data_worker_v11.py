"""PLANA.CY Data Worker V11 2GB Crash-Safe

One persistent Render Background Worker for:
- BuySell sale listings
- BuySell rental listings
- DLS supporting ArcGIS layers
- Official market datasets: DLS statistics, CBC RPPI/rates, CYSTAT construction costs
- CYSTAT building permits, construction-material inflation, population/housing context

The parcel indexer intentionally stays separate.

Required environment variables:
  SUPABASE_URL
  SUPABASE_SECRET_KEY

Recommended optional variables:
  PLANA_DATA_CYCLE_SECONDS=900
  PLANA_MARKET_MAX_PAGE=500
  PLANA_MARKET_PAGES_PER_CYCLE=5
  PLANA_MARKET_DETAIL_DELAY=0.75
  PLANA_MARKET_BLOCK_BACKOFF_SECONDS=1800
  PLANA_DLS_LAYERS=11,12,13,15,16,17,18,19,21,23,28,30,31,35,36,37
  PLANA_DLS_BATCH=200
  PLANA_OFFICIAL_EVERY_CYCLES=24
  PLANA_DLS_EVERY_CYCLES=8
"""
from __future__ import annotations

import asyncio
import gc
import hashlib
import io
import json
import os
import random
import re
import time
import tempfile
import uuid
from pathlib import Path as FsPath
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse, unquote
from urllib import robotparser

import httpx
import pandas as pd
import openpyxl
import xlrd
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from pypdf import PdfReader
from shapely.geometry import shape
from shapely import force_2d
from supabase import create_client

# -----------------------------------------------------------------------------
# Environment parsing helpers
# -----------------------------------------------------------------------------

def _coerce_integral(value: Any) -> int | None:
    """Return an exact Python int, or None when the value is not integral.

    Accepts integers, integral floats, and decimal-like strings such as ``"3.0"``.
    Blank, boolean, invalid, non-finite, and non-integral values are rejected.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value

    text = str(value).strip()
    if not text:
        return None
    try:
        numeric = Decimal(text.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    if not numeric.is_finite() or numeric != numeric.to_integral_value():
        return None
    return int(numeric)


def env_int(name: str, default: int) -> int:
    """Read an integer environment variable without truncating decimals."""
    parsed = _coerce_integral(os.getenv(name))
    return default if parsed is None else parsed

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

BUYSELL_BASE = "https://www.buysellcyprus.com"
DLS_ARCGIS_BASE = "https://eservices.dls.moi.gov.cy/arcgis/rest/services/National/CadastralMap_EN/MapServer"
CBC_RPPI_PAGE = "https://www.centralbank.cy/en/publications/residential-property-price-indices"
CBC_STAT_BULLETIN_PAGE = "https://www.centralbank.cy/en/publications/statistical-bulletin"
CBC_BANK_RATES_PAGE = "https://www.centralbank.cy/en/publications/monetary-and-financial-statistics/euro-area-statistics/bank-interest-rates"
CYSTAT_COST_PAGE = "https://www.cystat.gov.cy/en/KeyFiguresList?p=0&s=31&tID=3"

PXWEB_DATASETS = [
    (
        "building_permits_monthly",
        "CYSTAT Building Permits by District and Area, Monthly",
        "https://cystatdb23px.cystat.gov.cy/pxweb/en/8.CYSTAT-DB/8.CYSTAT-DB__Construction__Building%20Permits/1440010E.px/",
    ),
    (
        "building_permits_annual",
        "CYSTAT Building Permits by District and Area, Annual",
        "https://cystatdb23px.cystat.gov.cy/pxweb/en/8.CYSTAT-DB/8.CYSTAT-DB__Construction__Building%20Permits/1440011E.px/",
    ),
    (
        "construction_materials_index",
        "CYSTAT Price Index of Construction Materials, Annual",
        "https://cystatdb23px.cystat.gov.cy/pxweb/en/8.CYSTAT-DB/8.CYSTAT-DB__Construction__Price%20Index%20of%20Construction%20Materials/1420012E.px/",
    ),
    (
        "population_housing_postcode_2021",
        "CYSTAT Housing Units, Households and Population by Municipality and Postal Code, 2021",
        "https://cystatdb23px.cystat.gov.cy/pxweb/en/8.CYSTAT-DB/8.CYSTAT-DB__Population__Census%20of%20Population%20and%20Housing%202021__Population__Population%20-%20Place%20of%20Residence/1891168E.px/",
    ),
    (
        "population_housing_quarter_2021",
        "CYSTAT Housing Units, Households and Population by Municipality and Quarter, 2021",
        "https://cystatdb23px.cystat.gov.cy/pxweb/en/8.CYSTAT-DB/8.CYSTAT-DB__Population__Census%20of%20Population%20and%20Housing%202021__Population__Population%20-%20Place%20of%20Residence/1891164E.px/",
    ),
]

DLS_STATS = {
    "transfers": (
        "DLS Transfers of Sales",
        "https://portal.dls.moi.gov.cy/en/stats_category/enimerosi/statistika/poliseon/",
    ),
    "contracts": (
        "DLS Contracts of Sales",
        "https://portal.dls.moi.gov.cy/en/stats_category/enimerosi/statistika/politirion-engrafon/",
    ),
    "foreign_buyers": (
        "DLS Foreign Buyers",
        "https://portal.dls.moi.gov.cy/en/stats_category/enimerosi/statistika/poliseon-se-allodapous/",
    ),
    "mortgages": (
        "DLS Mortgages",
        "https://portal.dls.moi.gov.cy/en/stats_category/enimerosi/statistika/ypothikon/",
    ),
}

DLS_LAYERS = {
    11: "Development Plans",
    12: "Planning Zones",
    13: "Postal Code Areas",
    15: "Districts",
    16: "Municipalities Communities",
    17: "Quarters",
    18: "Blocks",
    19: "Localities",
    21: "Topographic Points",
    22: "Topographic Lines",
    23: "Topographic Areas",
    28: "Buildings",
    30: "Contour Lines 1993",
    31: "Coast Protection Zone",
    32: "State Land",
    35: "Sporadic Survey Parcels",
    36: "Surveyed Parcels",
    37: "White Zones",
    50: "Municipalities Clusters",
}

ID_RE = re.compile(r"-(\d+)\.html(?:$|\?)")
MONEY_RE = re.compile(r"€\s*([\d,.]+)")
EXTS = (".xlsx", ".xls", ".csv", ".pdf")

DATA_CYCLE_SECONDS = max(60, env_int("PLANA_DATA_CYCLE_SECONDS", 900))
MARKET_MAX_PAGE = max(1, env_int("PLANA_MARKET_MAX_PAGE", 500))
MARKET_PAGES_PER_CYCLE = max(1, min(20, env_int("PLANA_MARKET_PAGES_PER_CYCLE", 5)))
MARKET_DETAIL_DELAY = max(0.25, float(os.getenv("PLANA_MARKET_DETAIL_DELAY", "0.75")))
MARKET_DETAIL_CONCURRENCY = max(1, min(6, env_int("PLANA_MARKET_DETAIL_CONCURRENCY", 4)))
MARKET_DB_BATCH = max(25, min(200, env_int("PLANA_MARKET_DB_BATCH", 100)))
MARKET_BLOCK_BACKOFF_SECONDS = max(300, env_int("PLANA_MARKET_BLOCK_BACKOFF_SECONDS", 1800))
DLS_BATCH = max(50, min(300, env_int("PLANA_DLS_BATCH", 200)))
DLS_EVERY_CYCLES = max(1, env_int("PLANA_DLS_EVERY_CYCLES", 8))
OFFICIAL_EVERY_CYCLES = max(1, env_int("PLANA_OFFICIAL_EVERY_CYCLES", 24))
DATA_SOFT_RSS_MB = max(768, env_int("PLANA_DATA_SOFT_RSS_MB", 1500))
DATA_LEASE_SECONDS = max(300, env_int("PLANA_DATA_LEASE_SECONDS", 900))
DLS_FULL_SCAN_SECONDS = max(3600, env_int("PLANA_DLS_FULL_SCAN_SECONDS", 604800))
MARKET_BUYSELL_ENABLED = os.getenv("PLANA_MARKET_BUYSELL_ENABLED", "false").strip().lower() == "true"
MARKET_BUYSELL_LICENSED = os.getenv("PLANA_MARKET_BUYSELL_LICENSED", "false").strip().lower() == "true"
OFFICIAL_MAX_FILE_BYTES = max(10, env_int("PLANA_OFFICIAL_MAX_FILE_MB", 100)) * 1024 * 1024

MARKET_FEED_ENABLED = os.getenv("PLANA_MARKET_FEED_ENABLED", "false").strip().lower() == "true"
MARKET_FEED_SOURCES_JSON = os.getenv("PLANA_MARKET_FEED_SOURCES_JSON", "[]")
CYSTAT_PXWEB_ENABLED = os.getenv("PLANA_CYSTAT_PXWEB_ENABLED", "false").strip().lower() == "true"

MARKET_PUBLIC_ENABLED = os.getenv("PLANA_MARKET_PUBLIC_ENABLED", "true").strip().lower() == "true"
MARKET_PUBLIC_PAGES_PER_SOURCE = max(1, min(20, env_int("PLANA_MARKET_PUBLIC_PAGES_PER_SOURCE", 4)))
MARKET_PUBLIC_DETAILS_PER_SOURCE = max(10, min(250, env_int("PLANA_MARKET_PUBLIC_DETAILS_PER_SOURCE", 80)))
MARKET_PUBLIC_TIMEOUT_SECONDS = max(10, min(90, env_int("PLANA_MARKET_PUBLIC_TIMEOUT_SECONDS", 35)))
MARKET_PUBLIC_SOURCES_JSON = os.getenv("PLANA_MARKET_PUBLIC_SOURCES_JSON", "")

_raw_layers = os.getenv("PLANA_DLS_LAYERS", "11,12,13,15,16,17,18,19,21,23,28,30,31,35,36,37")
ACTIVE_DLS_LAYERS = [int(x.strip()) for x in _raw_layers.split(",") if x.strip() and int(x.strip()) in DLS_LAYERS]

UA = "PLANA.CY public market and geodata research collector/11.0"


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(message: str) -> None:
    print(message, flush=True)


def number(value: Any) -> float | None:
    if value is None:
        return None
    match = re.search(r"[\d,.]+", str(value))
    if not match:
        return None
    try:
        return float(match.group().replace(",", ""))
    except ValueError:
        return None


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    value = re.sub(r"\s+", " ", str(value)).strip()
    return value or None


def clean_cell(value: Any) -> Any:
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        try:
            value = value.item()
        except Exception:
            pass
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, (int, float, bool)):
        return value
    return str(value)


# -----------------------------------------------------------------------------
# Persistent checkpoint state + worker lease
# -----------------------------------------------------------------------------

STATE_TABLE = "plana_data_worker_state"
DATA_WORKER_LEASE = "worker:data"
ACTIVE_WORKER_RUN_ID: str | None = None


def current_rss_mb() -> float | None:
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return float(line.split()[1]) / 1024.0
    except Exception:
        return None
    return None


def db_retry(callable_, *, attempts: int = 5, label: str = "database"):
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return callable_()
        except Exception as exc:
            last_error = exc
            if attempt >= attempts:
                break
            delay = min(1.5 * (2 ** (attempt - 1)) + random.random(), 20.0)
            log(f"{label} retry {attempt}/{attempts} delay={delay:.1f}s: {type(exc).__name__}: {exc}")
            time.sleep(delay)
    raise RuntimeError(f"{label} failed after {attempts} attempts: {last_error!r}")


def state_get(sb, pipeline: str) -> dict[str, Any]:
    response = db_retry(
        lambda: sb.table(STATE_TABLE).select("*").eq("pipeline", pipeline).limit(1).execute(),
        label=f"STATE {pipeline} read",
    )
    rows = response.data or []
    return rows[0] if rows else {}



def state_put(sb, pipeline: str, **values: Any) -> None:
    """Lease-fenced durable checkpoint mutation."""
    if not ACTIVE_WORKER_RUN_ID:
        raise RuntimeError("Checkpoint attempted before data worker lease activation")
    payload = {"updated_at": now(), **values}
    db_retry(
        lambda: sb.rpc(
            "checkpoint_plana_worker_state",
            {
                "p_lease_pipeline": DATA_WORKER_LEASE,
                "p_worker_run_id": ACTIVE_WORKER_RUN_ID,
                "p_pipeline": pipeline,
                "p_values": payload,
            },
        ).execute(),
        label=f"lease-fenced STATE {pipeline} checkpoint",
    )



def claim_worker(sb, pipeline: str, run_id: str, lease_seconds: int) -> bool:
    response = db_retry(
        lambda: sb.rpc(
            "claim_plana_worker",
            {"p_pipeline": pipeline, "p_run_id": run_id, "p_lease_seconds": lease_seconds},
        ).execute(),
        label=f"LEASE {pipeline} claim",
    )
    return bool(response.data)


def heartbeat_worker(sb, pipeline: str, run_id: str, lease_seconds: int) -> None:
    response = db_retry(
        lambda: sb.rpc(
            "heartbeat_plana_worker",
            {"p_pipeline": pipeline, "p_run_id": run_id, "p_lease_seconds": lease_seconds},
        ).execute(),
        label=f"LEASE {pipeline} heartbeat",
    )
    if response.data is False:
        raise RuntimeError(f"Lost worker lease for {pipeline}")


def release_worker(sb, pipeline: str, run_id: str) -> None:
    try:
        db_retry(
            lambda: sb.rpc(
                "release_plana_worker",
                {"p_pipeline": pipeline, "p_run_id": run_id},
            ).execute(),
            attempts=3,
            label=f"LEASE {pipeline} release",
        )
    except Exception as exc:
        log(f"LEASE {pipeline} release warning: {type(exc).__name__}: {exc}")


def parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def backoff_active(state: dict[str, Any]) -> tuple[bool, int]:
    stamp = parse_timestamp(state.get("backoff_until"))
    if stamp is None:
        return False, 0
    seconds = int((stamp - datetime.now(timezone.utc)).total_seconds())
    return seconds > 0, max(seconds, 0)


def backoff_timestamp(seconds: int) -> str:
    return datetime.fromtimestamp(time.time() + seconds, tz=timezone.utc).isoformat()


def memory_guard(label: str) -> None:
    rss = current_rss_mb()
    if rss is None:
        return
    if rss >= DATA_SOFT_RSS_MB:
        log(f"MEMORY {label}: rss={rss:.1f} MB >= soft limit {DATA_SOFT_RSS_MB} MB; forcing cleanup")
        gc.collect()
        time.sleep(2)
    elif rss >= DATA_SOFT_RSS_MB * 0.8:
        log(f"MEMORY {label}: elevated rss={rss:.1f} MB")

# -----------------------------------------------------------------------------
# HTTP helpers
# -----------------------------------------------------------------------------


async def async_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    retries: int = 4,
    skip_statuses: Iterable[int] = (),
) -> httpx.Response | None:
    delay = 3.0
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = await client.get(url)
            if response.status_code in set(skip_statuses):
                log(f"  blocked/unavailable status={response.status_code} url={url}")
                return None
            response.raise_for_status()
            return response
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            last_error = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            log(f"  request failed {attempt}/{retries} status={status or 'network'} url={url}")
            if attempt < retries:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
    if last_error:
        log(f"  giving up url={url}: {last_error}")
    return None


def sync_get(client: httpx.Client, url: str, retries: int = 5) -> httpx.Response | None:
    delay = 4.0
    for attempt in range(1, retries + 1):
        try:
            response = client.get(url)
            if response.status_code == 429:
                wait = min(20 * attempt, 120)
                log(f"  rate limited 429; sleeping {wait}s: {url}")
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            log(f"  official request failed {attempt}/{retries} status={status or 'network'} url={url}")
            if attempt < retries:
                time.sleep(delay)
                delay = min(delay * 2, 40)
    return None


# -----------------------------------------------------------------------------
# BuySell sales + rentals
# -----------------------------------------------------------------------------


def listing_id(url: str) -> str | None:
    match = ID_RE.search(url)
    return match.group(1) if match else None


def walk_json(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_json(child)


def jsonlds(soup: BeautifulSoup) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            output.extend(x for x in walk_json(json.loads(tag.string or tag.get_text())) if isinstance(x, dict))
        except Exception:
            pass
    return output


def meta(soup: BeautifulSoup, key: str) -> str | None:
    tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
    return clean_text(tag.get("content")) if tag else None


def classify(title: str, url: str) -> str | None:
    text = f"{title} {url}".lower()
    checks = [
        ("apartment", ("apartment", "flat", "penthouse", "studio")),
        ("house", ("house", "villa", "bungalow", "maisonette", "townhouse")),
        ("land", ("land", "plot", "field")),
        ("office", ("office",)),
        ("shop", ("shop", "retail")),
        ("building", ("building", "block")),
        ("warehouse", ("warehouse", "industrial")),
    ]
    for kind, terms in checks:
        if any(term in text for term in terms):
            return kind
    return None


def parse_location(url: str, listing_status: str) -> tuple[str | None, str | None]:
    parts = [x for x in urlparse(url).path.split("/") if x]
    expected = f"property-for-{listing_status}"
    if len(parts) >= 4 and parts[0] == expected:
        return parts[1].replace("-", " ").title(), parts[2].replace("-", " ").title()
    return None, None


def links_from_search(html: str, listing_status: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    seen: dict[str, str] = {}
    needle = f"/property-for-{listing_status}/"
    for anchor in soup.find_all("a", href=True):
        url = urljoin(BUYSELL_BASE, anchor["href"]).split("#")[0]
        lid = listing_id(url)
        if lid and needle in url:
            seen[lid] = url
    return list(seen.values())



def parse_market_detail(url: str, html: str, listing_status: str) -> dict[str, Any]:
    """Parse a BuySell detail page into PLANA's canonical market-observation schema."""
    soup = BeautifulSoup(html, "html.parser")
    lid = listing_id(url)
    title = meta(soup, "og:title") or clean_text(soup.title.string if soup.title else None)
    description = meta(soup, "og:description")
    district, locality = parse_location(url, listing_status)
    observed_at = now()
    row: dict[str, Any] = {
        "observation_key": f"buysell:{listing_status}:{lid}" if lid else None,
        "source": "buysell",
        "source_id": "buysell",
        "source_class": "portal",
        "source_listing_id": lid,
        "source_url": url,
        "transaction_type": listing_status,
        "property_type": classify(title or "", url),
        "development_status": None,
        "bedrooms": None,
        "bathrooms": None,
        "covered_area_m2": None,
        "plot_area_m2": None,
        "asking_price_eur": None,
        "asking_rent_monthly_eur": None,
        "price_per_m2_eur": None,
        "rent_per_m2_month_eur": None,
        "latitude": None,
        "longitude": None,
        "district": district,
        "municipality": None,
        "locality": locality,
        "planning_zone": None,
        "title": title,
        "description": description,
        "status": "active",
        "first_seen_at": observed_at,
        "last_seen_at": observed_at,
        "price_changed_at": None,
        "original_price_eur": None,
        "current_price_eur": None,
        "confidence": 0.72,
        "source_adapter": "buysell_html_detail",
        "source_engine_version": "data_worker_v9",
        "raw_data": {"parser": "data_worker_v9", "transaction_type": listing_status},
    }

    raw_text = clean_text(soup.get_text(" ")) or ""
    price: float | None = None
    for data in jsonlds(soup):
        offers = data.get("offers")
        if isinstance(offers, dict) and price is None:
            price = number(offers.get("price"))
        if price is None and data.get("priceCurrency") == "EUR":
            price = number(data.get("price"))
        address = data.get("address")
        if isinstance(address, dict):
            row["locality"] = clean_text(address.get("addressLocality")) or row["locality"]
            row["district"] = clean_text(address.get("addressRegion")) or row["district"]
        geo = data.get("geo")
        if isinstance(geo, dict):
            row["latitude"] = number(geo.get("latitude"))
            row["longitude"] = number(geo.get("longitude"))
        if not row.get("description"):
            row["description"] = clean_text(data.get("description"))

    if price is None:
        match = MONEY_RE.search(raw_text)
        if match:
            price = number(match.group(1))

    patterns = {
        "covered_area_m2": [r"(?:internal|covered|living)\s+area\s*:?\s*([\d,.]+)\s*(?:m²|m2|sqm)"],
        "plot_area_m2": [r"(?:plot|land)\s+(?:area|size)\s*:?\s*([\d,.]+)\s*(?:m²|m2|sqm)"],
        "bedrooms": [r"bedrooms?\s*:?\s*(\d+)"],
        "bathrooms": [r"bathrooms?\s*:?\s*(\d+)"],
    }
    lower = raw_text.lower()
    for field, regexes in patterns.items():
        for regex in regexes:
            match = re.search(regex, lower, re.I)
            if match:
                row[field] = number(match.group(1))
                break

    area = number(row.get("covered_area_m2"))
    if listing_status == "rent":
        row["asking_rent_monthly_eur"] = price
        row["rent_per_m2_month_eur"] = (price / area) if price and area and area > 0 else None
    else:
        row["asking_price_eur"] = price
        row["price_per_m2_eur"] = (price / area) if price and area and area > 0 else None
    row["current_price_eur"] = price
    row["original_price_eur"] = price
    return row




MARKET_INTEGER_FIELDS = ("bedrooms", "bathrooms")
POSTGRES_INTEGER_MIN = -(2**31)
POSTGRES_INTEGER_MAX = 2**31 - 1


def _sql_integer(value: Any) -> int | None:
    """Normalize one PostgreSQL ``integer`` value for a market RPC payload."""
    parsed = _coerce_integral(value)
    if parsed is None or not POSTGRES_INTEGER_MIN <= parsed <= POSTGRES_INTEGER_MAX:
        return None
    return parsed


def _normalize_market_row_types(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize every integer column in the canonical market row schema."""
    normalized = dict(row)
    for field in MARKET_INTEGER_FIELDS:
        normalized[field] = _sql_integer(normalized.get(field))
    return normalized


def _assert_market_integer_payload(rows: list[dict[str, Any]]) -> None:
    """Fail locally instead of allowing a decimal string to reach PostgreSQL."""
    for row in rows:
        for field in MARKET_INTEGER_FIELDS:
            value = row.get(field)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise ValueError(
                    f"market payload {field} must be int or null; "
                    f"got {value!r} ({type(value).__name__}) for "
                    f"{row.get('observation_key')}"
                )


def save_market_listings_bulk(sb, rows: list[dict[str, Any]], worker_run_id: str) -> int:
    """Atomically upsert canonical observations and history through a lease-fenced RPC."""
    valid = [
        dict(row)
        for row in rows
        if row.get("observation_key") and row.get("source_listing_id")
    ]
    if not valid:
        return 0

    def write_chunk(chunk: list[dict[str, Any]]) -> int:
        normalized_chunk = [_normalize_market_row_types(row) for row in chunk]
        _assert_market_integer_payload(normalized_chunk)
        try:
            response = db_retry(
                lambda: sb.rpc(
                    "upsert_plana_market_observations",
                    {"p_worker_run_id": worker_run_id, "p_rows": normalized_chunk},
                ).execute(),
                label=f"market atomic upsert rows={len(normalized_chunk)}",
            )
            return int(response.data or len(normalized_chunk))
        except Exception as exc:
            if len(normalized_chunk) == 1:
                row = normalized_chunk[0]
                db_retry(
                    lambda: sb.rpc(
                        "record_plana_worker_failure",
                        {
                            "p_lease_pipeline": DATA_WORKER_LEASE,
                            "p_worker_run_id": worker_run_id,
                            "p_pipeline": f"market_{row.get('transaction_type') or 'unknown'}",
                            "p_source_key": str(row.get("observation_key")),
                            "p_error": f"{type(exc).__name__}: {exc}"[:4000],
                            "p_payload": {"source_url": row.get("source_url")},
                        },
                    ).execute(),
                    label="market durable singleton failure",
                )
                log(f"  MARKET durable quarantine {row.get('observation_key')}: {type(exc).__name__}: {exc}")
                return 0
            midpoint = len(normalized_chunk) // 2
            return write_chunk(normalized_chunk[:midpoint]) + write_chunk(normalized_chunk[midpoint:])

    written = 0
    for start in range(0, len(valid), MARKET_DB_BATCH):
        written += write_chunk(valid[start : start + MARKET_DB_BATCH])
    return written




def save_market_listing(sb, row: dict[str, Any], worker_run_id: str) -> None:
    save_market_listings_bulk(sb, [row], worker_run_id)



def mark_market_observations_unavailable(sb, observation_keys: list[str], worker_run_id: str) -> int:
    """Idempotently mark known 404/410 listings unavailable and append one status history event."""
    keys = sorted({key for key in observation_keys if key})
    if not keys:
        return 0
    response = db_retry(
        lambda: sb.rpc(
            "mark_plana_market_observations_unavailable",
            {
                "p_worker_run_id": worker_run_id,
                "p_observation_keys": keys,
                "p_status": "gone",
            },
        ).execute(),
        label=f"market unavailable status rows={len(keys)}",
    )
    return int(response.data or 0)




async def fetch_market_detail_row(
    client: httpx.AsyncClient,
    url: str,
    listing_status: str,
    semaphore: asyncio.Semaphore,
) -> tuple[dict[str, Any] | None, str, str | None]:
    """Return (row, disposition, error); retryable pages must hold the page cursor."""
    async with semaphore:
        delay = 3.0
        last_error: str | None = None
        for attempt in range(1, 3):
            try:
                response = await client.get(url)
                status = response.status_code
                if status in (404, 410):
                    response.close()
                    return None, "gone", None
                if status in (403, 408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524):
                    last_error = f"HTTP {status}"
                    response.close()
                    if attempt < 2:
                        await asyncio.sleep(delay)
                        delay *= 2
                        continue
                    return None, "retryable", last_error
                response.raise_for_status()
                try:
                    row = parse_market_detail(url, response.text, listing_status)
                    return row, "ok", None
                except Exception as exc:
                    return None, "parse_error", f"{type(exc).__name__}: {exc}"
                finally:
                    response.close()
            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < 2:
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                return None, "retryable", last_error
        return None, "retryable", last_error or "unknown detail failure"




async def market_pipeline(sb, listing_status: str, worker_run_id: str) -> None:
    pipeline = f"market_{listing_status}"
    if not (MARKET_BUYSELL_ENABLED and MARKET_BUYSELL_LICENSED):
        state_put(
            sb,
            pipeline,
            status="disabled",
            last_error="BuySell collection requires PLANA_MARKET_BUYSELL_ENABLED=true and PLANA_MARKET_BUYSELL_LICENSED=true",
            last_heartbeat_at=now(),
        )
        log(f"MARKET {listing_status}: disabled; source permission/license flags are not both true")
        return

    state = state_get(sb, pipeline)
    blocked, wait = backoff_active(state)
    if blocked:
        log(f"MARKET {listing_status}: checkpoint blocked for another {wait}s")
        return

    page = max(1, int(state.get("cursor_page") or 1))
    pages_done = 0
    seen_total = 0
    written_total = 0
    headers = {
        "User-Agent": UA,
        "Accept-Language": "en-GB,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml",
    }
    limits = httpx.Limits(
        max_connections=max(MARKET_DETAIL_CONCURRENCY + 2, 6),
        max_keepalive_connections=max(MARKET_DETAIL_CONCURRENCY, 4),
    )

    async with httpx.AsyncClient(timeout=httpx.Timeout(45, connect=20), follow_redirects=True, headers=headers, limits=limits) as client:
        while pages_done < MARKET_PAGES_PER_CYCLE:
            search_url = f"{BUYSELL_BASE}/properties-for-{listing_status}/sort-rl/page-{page}"
            response = await async_get(client, search_url, retries=3, skip_statuses=(403, 429, 520, 521, 522, 523, 524))
            if response is None:
                state_put(
                    sb,
                    pipeline,
                    status="blocked",
                    cursor_page=page,
                    backoff_until=backoff_timestamp(MARKET_BLOCK_BACKOFF_SECONDS),
                    last_error=f"Search page unavailable: {search_url}",
                    last_heartbeat_at=now(),
                )
                log(f"MARKET {listing_status}: page {page} blocked; preserving checkpoint and backing off")
                return

            links = links_from_search(response.text, listing_status)
            response.close()
            log(f"MARKET {listing_status}: page {page} links={len(links)}")
            if not links:
                if page == 1:
                    state_put(
                        sb,
                        pipeline,
                        status="blocked",
                        cursor_page=1,
                        backoff_until=backoff_timestamp(MARKET_BLOCK_BACKOFF_SECONDS),
                        last_error="Empty first search page; treating as source anomaly instead of end-of-pagination",
                        last_heartbeat_at=now(),
                    )
                    log(f"MARKET {listing_status}: empty page 1; preserving checkpoint and backing off")
                    return
                page = 1
                state_put(sb, pipeline, status="wrapped", cursor_page=page, last_error=None, last_heartbeat_at=now())
                log(f"MARKET {listing_status}: end of pagination; wrapping checkpoint to page 1")
                return

            semaphore = asyncio.Semaphore(MARKET_DETAIL_CONCURRENCY)
            page_rows: list[dict[str, Any]] = []
            gone_keys: list[str] = []
            retryable: list[tuple[str, str | None]] = []
            parse_errors: list[tuple[str, str | None]] = []
            for start_index in range(0, len(links), MARKET_DETAIL_CONCURRENCY):
                group = links[start_index : start_index + MARKET_DETAIL_CONCURRENCY]
                results = await asyncio.gather(
                    *(fetch_market_detail_row(client, url, listing_status, semaphore) for url in group)
                )
                seen_total += len(group)
                for url, (row, disposition, error) in zip(group, results):
                    if disposition == "retryable":
                        retryable.append((url, error))
                    elif disposition == "parse_error":
                        parse_errors.append((url, error))
                    elif disposition == "gone":
                        lid = listing_id(url)
                        if lid:
                            gone_keys.append(f"buysell:{listing_status}:{lid}")
                    elif row and row.get("source_listing_id"):
                        page_rows.append(row)
                await asyncio.sleep(MARKET_DETAIL_DELAY)

            for url, error in parse_errors:
                source_key = f"buysell:{listing_status}:{listing_id(url) or url}"
                await asyncio.to_thread(
                    lambda sk=source_key, er=error, u=url: db_retry(
                        lambda: sb.rpc(
                            "record_plana_worker_failure",
                            {
                                "p_lease_pipeline": DATA_WORKER_LEASE,
                                "p_worker_run_id": worker_run_id,
                                "p_pipeline": pipeline,
                                "p_source_key": sk,
                                "p_error": (er or "parse error")[:4000],
                                "p_payload": {"source_url": u},
                            },
                        ).execute(),
                        label=f"market parse failure {sk}",
                    )
                )

            if page_rows:
                written_total += await asyncio.to_thread(save_market_listings_bulk, sb, page_rows, worker_run_id)
            if gone_keys:
                gone_written = await asyncio.to_thread(mark_market_observations_unavailable, sb, gone_keys, worker_run_id)
                log(f"MARKET {listing_status}: marked {gone_written} known listings unavailable")

            if retryable:
                detail_error = " | ".join(f"{url}: {err}" for url, err in retryable)[-2000:]
                state_put(
                    sb,
                    pipeline,
                    status="blocked",
                    cursor_page=page,
                    backoff_until=backoff_timestamp(MARKET_BLOCK_BACKOFF_SECONDS),
                    items_seen=seen_total,
                    items_written=written_total,
                    last_error=detail_error,
                    last_heartbeat_at=now(),
                )
                log(f"MARKET {listing_status}: {len(retryable)} retryable detail failures; preserving page {page}")
                return

            page += 1
            if page > MARKET_MAX_PAGE:
                page = 1
            pages_done += 1
            state_put(
                sb,
                pipeline,
                status="running",
                cursor_page=page,
                pages_scanned=pages_done,
                items_seen=seen_total,
                items_written=written_total,
                last_error=(f"Durably quarantined {len(parse_errors)} parse errors" if parse_errors else None),
                last_heartbeat_at=now(),
            )
            del page_rows, gone_keys, results, semaphore, retryable, parse_errors
            gc.collect()
            await asyncio.sleep(5)

    state_put(
        sb,
        pipeline,
        status="done",
        cursor_page=page,
        pages_scanned=pages_done,
        items_seen=seen_total,
        items_written=written_total,
        last_completed_at=now(),
        last_error=None,
        last_heartbeat_at=now(),
    )
    log(f"MARKET {listing_status}: cycle pages={pages_done} seen={seen_total} written={written_total} next_page={page}")





def _feed_value(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return value
    return None


def _feed_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("listings", "properties", "results", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
            if isinstance(value, dict):
                nested = _feed_items(value)
                if nested:
                    return nested
    return []


def canonical_feed_row(item: dict[str, Any], source_name: str, source_class: str = "licensed_feed") -> dict[str, Any] | None:
    listing_id_value = _feed_value(item, "id", "listing_id", "property_id", "reference", "ref", "uid")
    if listing_id_value is None:
        return None
    listing_id_text = str(listing_id_value)
    transaction = str(_feed_value(item, "transaction_type", "status_type", "deal_type", "purpose", "listing_type") or "sale").lower()
    transaction = "rent" if "rent" in transaction or "let" in transaction else "sale"
    price = number(_feed_value(item, "price", "asking_price", "sale_price", "rent", "monthly_rent"))
    area = number(_feed_value(item, "covered_area_m2", "covered_area", "internal_area", "area", "size_m2"))
    observed_at = now()
    row = {
        "observation_key": f"{source_name}:{transaction}:{listing_id_text}",
        "source": source_name,
        "source_id": source_name,
        "source_class": source_class,
        "source_listing_id": listing_id_text,
        "source_url": clean_text(_feed_value(item, "url", "source_url", "link")),
        "transaction_type": transaction,
        "property_type": clean_text(_feed_value(item, "property_type", "type", "category")),
        "development_status": clean_text(_feed_value(item, "development_status", "condition", "stage")),
        "bedrooms": number(_feed_value(item, "bedrooms", "beds")),
        "bathrooms": number(_feed_value(item, "bathrooms", "baths")),
        "covered_area_m2": area,
        "plot_area_m2": number(_feed_value(item, "plot_area_m2", "plot_area", "land_area")),
        "asking_price_eur": price if transaction == "sale" else None,
        "asking_rent_monthly_eur": price if transaction == "rent" else None,
        "price_per_m2_eur": (price / area) if transaction == "sale" and price and area and area > 0 else None,
        "rent_per_m2_month_eur": (price / area) if transaction == "rent" and price and area and area > 0 else None,
        "latitude": number(_feed_value(item, "latitude", "lat")),
        "longitude": number(_feed_value(item, "longitude", "lng", "lon")),
        "district": clean_text(_feed_value(item, "district", "region")),
        "municipality": clean_text(_feed_value(item, "municipality", "city")),
        "locality": clean_text(_feed_value(item, "locality", "area_name", "suburb", "location")),
        "planning_zone": clean_text(_feed_value(item, "planning_zone", "zone")),
        "title": clean_text(_feed_value(item, "title", "name")),
        "description": clean_text(_feed_value(item, "description", "summary")),
        "status": clean_text(_feed_value(item, "status", "availability")) or "active",
        "first_seen_at": observed_at,
        "last_seen_at": observed_at,
        "price_changed_at": None,
        "original_price_eur": price,
        "current_price_eur": price,
        "confidence": 0.95,
        "source_adapter": "authorized_json_feed_v1",
        "source_engine_version": "data_worker_v9",
        "raw_data": {"licensed_feed": True, "payload": item},
    }
    return row


async def authorized_market_feeds_pipeline(sb, worker_run_id: str) -> None:
    if not MARKET_FEED_ENABLED:
        log("MARKET authorized feeds: disabled; set PLANA_MARKET_FEED_ENABLED=true after obtaining API/feed permission")
        return
    try:
        sources = json.loads(MARKET_FEED_SOURCES_JSON)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"PLANA_MARKET_FEED_SOURCES_JSON is invalid JSON: {exc}")
    if not isinstance(sources, list) or not sources:
        log("MARKET authorized feeds: no configured sources")
        return
    async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=30), follow_redirects=True, headers={"User-Agent": UA}) as client:
        for config in sources:
            if not isinstance(config, dict):
                continue
            name = clean_text(config.get("name")) or "authorized_feed"
            url = clean_text(config.get("url"))
            if not url:
                continue
            headers = {"Accept": "application/json"}
            token_env = clean_text(config.get("token_env"))
            if token_env and os.getenv(token_env):
                headers["Authorization"] = f"Bearer {os.environ[token_env]}"
            api_key_env = clean_text(config.get("api_key_env"))
            api_key_header = clean_text(config.get("api_key_header")) or "X-API-Key"
            if api_key_env and os.getenv(api_key_env):
                headers[api_key_header] = os.environ[api_key_env]
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            payload = response.json()
            items = _feed_items(payload)
            rows = [row for item in items if (row := canonical_feed_row(item, name, clean_text(config.get("source_class")) or "licensed_feed"))]
            written = await asyncio.to_thread(save_market_listings_bulk, sb, rows, worker_run_id)
            log(f"MARKET authorized feed {name}: received={len(items)} written={written}")


# -----------------------------------------------------------------------------
# DLS ArcGIS supporting layers
# -----------------------------------------------------------------------------


def scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def geom_wkt(geometry: dict[str, Any] | None) -> str | None:
    if not geometry:
        return None
    geom = force_2d(shape(geometry))
    return None if geom.is_empty else geom.wkt


async def dls_json(client: httpx.AsyncClient, url: str, params: dict[str, Any], *, post: bool = False, retries: int = 5) -> dict[str, Any]:
    delay = 2.0
    for attempt in range(1, retries + 1):
        try:
            response = await (client.post(url, data=params) if post else client.get(url, params=params))
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict) and data.get("error"):
                raise RuntimeError(str(data["error"]))
            return data
        except (httpx.RequestError, httpx.HTTPStatusError, RuntimeError) as exc:
            if attempt == retries:
                raise
            log(f"  DLS retry {attempt}/{retries} {url}: {type(exc).__name__}")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)
    raise RuntimeError(f"DLS request failed: {url}")



def _future_iso(seconds: int) -> str:
    return datetime.fromtimestamp(time.time() + seconds, tz=timezone.utc).isoformat()


def _dls_upsert_rows(sb, rows: list[dict[str, Any]], worker_run_id: str, pipeline: str) -> tuple[int, int]:
    """Recursive bounded DLS writes; singleton failures are durably disposed."""
    if not rows:
        return 0, 0
    try:
        response = db_retry(
            lambda: sb.rpc(
                "upsert_plana_dls_features",
                {"p_worker_run_id": worker_run_id, "p_rows": rows},
            ).execute(),
            label=f"DLS RPC rows={len(rows)}",
        )
        keys = [str(row["source_object_id"]) for row in rows]
        db_retry(
            lambda: sb.rpc(
                "resolve_plana_worker_failures",
                {
                    "p_lease_pipeline": DATA_WORKER_LEASE,
                    "p_worker_run_id": worker_run_id,
                    "p_pipeline": pipeline,
                    "p_source_keys": keys,
                },
            ).execute(),
            label=f"resolve DLS failures {pipeline}",
        )
        return int(response.data or len(rows)), 0
    except Exception as exc:
        if len(rows) == 1:
            row = rows[0]
            db_retry(
                lambda: sb.rpc(
                    "record_plana_worker_failure",
                    {
                        "p_lease_pipeline": DATA_WORKER_LEASE,
                        "p_worker_run_id": worker_run_id,
                        "p_pipeline": pipeline,
                        "p_source_key": str(row.get("source_object_id")),
                        "p_error": f"{type(exc).__name__}: {exc}"[:4000],
                        "p_payload": {"layer_id": row.get("layer_id"), "cycle_key": row.get("sync_cycle_key")},
                    },
                ).execute(),
                label=f"durable DLS failure {pipeline}",
            )
            log(f"DLS {pipeline}: durably quarantined OBJECTID={row.get('source_object_id')}: {type(exc).__name__}: {exc}")
            return 0, 1
        midpoint = len(rows) // 2
        a = _dls_upsert_rows(sb, rows[:midpoint], worker_run_id, pipeline)
        b = _dls_upsert_rows(sb, rows[midpoint:], worker_run_id, pipeline)
        return a[0] + b[0], a[1] + b[1]



def _touch_dls_cycle(
    sb: Any,
    *,
    worker_run_id: str,
    layer_id: int,
    layer_name: str,
    source_url: str,
    object_id_field: str,
    geometry_type: str | None,
    source_last_edit_ms: Any,
    cycle_key: str,
    feature_count: int,
    started_at: str | None,
) -> None:
    db_retry(
        lambda: sb.rpc(
            "touch_plana_dls_cycle",
            {
                "p_worker_run_id": worker_run_id,
                "p_layer_id": layer_id,
                "p_layer_name": layer_name,
                "p_source_url": source_url,
                "p_object_id_field": object_id_field,
                "p_geometry_type": geometry_type,
                "p_source_last_edit_ms": source_last_edit_ms,
                "p_cycle_key": cycle_key,
                "p_feature_count": feature_count,
                "p_started_at": started_at,
            },
        ).execute(),
        label=f"lease-fenced DLS cycle touch layer {layer_id}",
    )


def _abort_dls_cycle(sb: Any, *, worker_run_id: str, layer_id: int, cycle_key: str, reason: str) -> int:
    response = db_retry(
        lambda: sb.rpc(
            "abort_plana_dls_cycle",
            {
                "p_worker_run_id": worker_run_id,
                "p_layer_id": layer_id,
                "p_cycle_key": cycle_key,
                "p_reason": reason[:4000],
            },
        ).execute(),
        label=f"lease-fenced DLS cycle abort layer {layer_id}",
    )
    return int(response.data or 0)


def _complete_dls_cycle(
    sb: Any,
    *,
    worker_run_id: str,
    layer_id: int,
    layer_name: str,
    source_url: str,
    object_id_field: str,
    geometry_type: str | None,
    source_last_edit_ms: Any,
    cycle_key: str,
    expected_feature_count: int,
    completed_at: str,
) -> int:
    response = db_retry(
        lambda: sb.rpc(
            "complete_plana_dls_cycle",
            {
                "p_worker_run_id": worker_run_id,
                "p_layer_id": layer_id,
                "p_layer_name": layer_name,
                "p_source_url": source_url,
                "p_object_id_field": object_id_field,
                "p_geometry_type": geometry_type,
                "p_source_last_edit_ms": source_last_edit_ms,
                "p_cycle_key": cycle_key,
                "p_expected_feature_count": expected_feature_count,
                "p_completed_at": completed_at,
            },
        ).execute(),
        label=f"lease-fenced DLS cycle promote layer {layer_id}",
    )
    return int(response.data or 0)


async def dls_layer_pipeline(sb, layer_id: int, worker_run_id: str) -> None:
    """OBJECTID-keyset one DLS layer with completed-cycle visibility and durable cursors."""
    name = DLS_LAYERS[layer_id]
    pipeline = f"dls_layer_{layer_id}"
    state = state_get(sb, pipeline)
    state_meta = state.get("meta") or {}
    url = f"{DLS_ARCGIS_BASE}/{layer_id}"
    query_url = f"{url}/query"
    limits = httpx.Limits(max_connections=4, max_keepalive_connections=2)

    async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=30), headers={"User-Agent": UA}, limits=limits) as client:
        try:
            layer_meta = await dls_json(client, url, {"f": "json"})
            oid_field = layer_meta.get("objectIdField") or next(
                (field["name"] for field in layer_meta.get("fields", []) if field.get("type") == "esriFieldTypeOID"),
                None,
            )
            if not oid_field:
                raise RuntimeError("No object ID field")
            source_edit = (layer_meta.get("editingInfo") or {}).get("lastEditDate")

            sync_rows = db_retry(
                lambda: sb.table("dls_sync_state").select("*").eq("layer_id", layer_id).limit(1).execute(),
                label=f"DLS sync state layer {layer_id} read",
            ).data or []
            sync_state = sync_rows[0] if sync_rows else {}

            active_cycle_key = clean_text(state.get("cycle_key"))
            cursor_mode = state_meta.get("cursor_mode")
            last_oid = max(0, int(state.get("cursor_offset") or 0)) if cursor_mode == "oid_keyset_v8" else 0
            cycle_source_edit = state_meta.get("source_last_edit_ms")
            if active_cycle_key and cycle_source_edit is not None and source_edit is not None and str(cycle_source_edit) != str(source_edit):
                reason = f"source edit version changed mid-cycle {cycle_source_edit}->{source_edit}; restarting snapshot"
                deleted = await asyncio.to_thread(
                    _abort_dls_cycle,
                    sb,
                    worker_run_id=worker_run_id,
                    layer_id=layer_id,
                    cycle_key=active_cycle_key,
                    reason=reason,
                )
                active_cycle_key = None
                last_oid = 0
                state_put(
                    sb,
                    pipeline,
                    status="aborted",
                    cursor_offset=0,
                    cursor_key=None,
                    cycle_key=None,
                    processed_count=0,
                    last_error=reason,
                    last_heartbeat_at=now(),
                    meta={"cursor_mode": "oid_keyset_v8", "source_last_edit_ms": source_edit},
                )
                state = state_get(sb, pipeline)
                state_meta = state.get("meta") or {}
                log(f"DLS layer {layer_id} {name}: {reason}; discarded staged_rows={deleted}")
            if not active_cycle_key:
                completed_edit = sync_state.get("source_last_edit_ms")
                next_scan = parse_timestamp(state_meta.get("next_full_scan_at"))
                due = next_scan is None or next_scan <= datetime.now(timezone.utc)
                changed = source_edit is not None and str(source_edit) != str(completed_edit)
                if sync_state.get("last_status") == "done" and not changed and not due:
                    log(f"DLS layer {layer_id} {name}: unchanged/not due; completed_cycle={sync_state.get('completed_cycle_key')}")
                    return
                active_cycle_key = str(uuid.uuid4())
                last_oid = 0
                state_put(
                    sb,
                    pipeline,
                    status="running",
                    cursor_offset=0,
                    cursor_key=None,
                    cycle_key=active_cycle_key,
                    processed_count=0,
                    last_error=None,
                    consecutive_failures=0,
                    last_heartbeat_at=now(),
                    meta={"cursor_mode": "oid_keyset_v8", "source_last_edit_ms": source_edit},
                )
                state = state_get(sb, pipeline)
                await asyncio.to_thread(
                    _touch_dls_cycle,
                    sb,
                    worker_run_id=worker_run_id,
                    layer_id=layer_id,
                    layer_name=name,
                    source_url=url,
                    object_id_field=oid_field,
                    geometry_type=layer_meta.get("geometryType"),
                    source_last_edit_ms=source_edit,
                    cycle_key=active_cycle_key,
                    feature_count=0,
                    started_at=now(),
                )
                log(f"DLS layer {layer_id} {name}: starting cycle {active_cycle_key} source_edit={source_edit}")

            count_data = await dls_json(client, query_url, {"f": "json", "where": "1=1", "returnCountOnly": "true"})
            total_items = int(count_data.get("count") or 0)
            data = await dls_json(
                client,
                query_url,
                {
                    "f": "geojson",
                    "where": f"{oid_field} > {last_oid}",
                    "outFields": "*",
                    "returnGeometry": "true",
                    "outSR": "4326",
                    "orderByFields": f"{oid_field} ASC",
                    "resultRecordCount": DLS_BATCH,
                    "resultType": "standard",
                },
                post=True,
            )
            features = data.get("features") or []
            if not features:
                if data.get("exceededTransferLimit") is True:
                    raise RuntimeError("DLS transfer limit reported without a resumable feature page")
                completion_meta = await dls_json(client, url, {"f": "json"})
                completion_edit = (completion_meta.get("editingInfo") or {}).get("lastEditDate")
                cycle_source_edit = (state.get("meta") or {}).get("source_last_edit_ms")
                if cycle_source_edit is not None and completion_edit is not None and str(cycle_source_edit) != str(completion_edit):
                    reason = f"source edit version changed before promotion {cycle_source_edit}->{completion_edit}; snapshot discarded"
                    deleted = await asyncio.to_thread(
                        _abort_dls_cycle,
                        sb,
                        worker_run_id=worker_run_id,
                        layer_id=layer_id,
                        cycle_key=active_cycle_key,
                        reason=reason,
                    )
                    state_put(
                        sb,
                        pipeline,
                        status="aborted",
                        cursor_offset=0,
                        cursor_key=None,
                        cycle_key=None,
                        processed_count=0,
                        last_error=reason,
                        last_heartbeat_at=now(),
                        meta={"cursor_mode": "oid_keyset_v8", "source_last_edit_ms": completion_edit},
                    )
                    log(f"DLS layer {layer_id} {name}: {reason}; discarded staged_rows={deleted}")
                    return
                completed_at = now()
                next_scan_at = _future_iso(DLS_FULL_SCAN_SECONDS)
                promoted_count = await asyncio.to_thread(
                    _complete_dls_cycle,
                    sb,
                    worker_run_id=worker_run_id,
                    layer_id=layer_id,
                    layer_name=name,
                    source_url=url,
                    object_id_field=oid_field,
                    geometry_type=layer_meta.get("geometryType"),
                    source_last_edit_ms=source_edit,
                    cycle_key=active_cycle_key,
                    expected_feature_count=total_items,
                    completed_at=completed_at,
                )
                state_put(
                    sb,
                    pipeline,
                    status="done",
                    cursor_offset=last_oid,
                    cursor_key=str(last_oid),
                    cycle_key=None,
                    processed_count=promoted_count,
                    total_items=total_items,
                    last_completed_at=completed_at,
                    last_error=None,
                    consecutive_failures=0,
                    last_heartbeat_at=completed_at,
                    meta={
                        "cursor_mode": "oid_keyset_v8",
                        "last_completed_oid": last_oid,
                        "completed_cycle_key": active_cycle_key,
                        "source_last_edit_ms": source_edit,
                        "next_full_scan_at": next_scan_at,
                    },
                )
                log(f"DLS layer {layer_id} {name}: atomic promotion complete cycle={active_cycle_key} oid={last_oid:,}; total={promoted_count:,}")
                return

            rows: list[dict[str, Any]] = []
            page_oids: list[int] = []
            invalid_count = 0
            for feature in features:
                props = {str(key): scalar(value) for key, value in (feature.get("properties") or {}).items()}
                object_id = props.get(oid_field)
                try:
                    object_id = int(object_id)
                except (TypeError, ValueError):
                    invalid_count += 1
                    source_key = hashlib.sha256(json.dumps(props, sort_keys=True, default=str).encode()).hexdigest()[:32]
                    await asyncio.to_thread(
                        lambda sk=source_key, pp=props: db_retry(
                            lambda: sb.rpc(
                                "record_plana_worker_failure",
                                {
                                    "p_lease_pipeline": DATA_WORKER_LEASE,
                                    "p_worker_run_id": worker_run_id,
                                    "p_pipeline": pipeline,
                                    "p_source_key": sk,
                                    "p_error": f"Missing/invalid OBJECTID field {oid_field}",
                                    "p_payload": {"properties": pp, "cycle_key": active_cycle_key},
                                },
                            ).execute(),
                            label=f"DLS invalid OBJECTID {pipeline}",
                        )
                    )
                    continue
                page_oids.append(object_id)
                rows.append(
                    {
                        "layer_id": layer_id,
                        "layer_name": name,
                        "source_object_id": object_id,
                        "geom": geom_wkt(feature.get("geometry")),
                        "properties": props,
                        "source_url": url,
                        "source_last_edit_ms": source_edit,
                        "sync_cycle_key": active_cycle_key,
                        "synced_at": now(),
                    }
                )

            if not page_oids:
                raise RuntimeError("DLS page had no usable object IDs; cannot prove cursor progress")
            next_oid = max(page_oids)
            if next_oid <= last_oid:
                raise RuntimeError(f"DLS OBJECTID cursor did not advance: {last_oid} -> {next_oid}")

            written, quarantined = await asyncio.to_thread(_dls_upsert_rows, sb, rows, worker_run_id, pipeline)
            processed_count = int(state.get("processed_count") or 0) + len(features)
            heartbeat_worker(sb, DATA_WORKER_LEASE, worker_run_id, DATA_LEASE_SECONDS)
            state_put(
                sb,
                pipeline,
                status="running",
                cursor_offset=next_oid,
                cursor_key=str(next_oid),
                cycle_key=active_cycle_key,
                processed_count=processed_count,
                items_seen=len(features),
                items_written=written,
                total_items=total_items,
                last_error=(f"durably quarantined={quarantined + invalid_count}" if quarantined or invalid_count else None),
                consecutive_failures=0,
                last_heartbeat_at=now(),
                meta={"cursor_mode": "oid_keyset_v8", "source_last_edit_ms": source_edit},
            )
            await asyncio.to_thread(
                _touch_dls_cycle,
                sb,
                worker_run_id=worker_run_id,
                layer_id=layer_id,
                layer_name=name,
                source_url=url,
                object_id_field=oid_field,
                geometry_type=layer_meta.get("geometryType"),
                source_last_edit_ms=source_edit,
                cycle_key=active_cycle_key,
                feature_count=min(processed_count, total_items),
                started_at=sync_state.get("last_started_at") or now(),
            )
            log(f"DLS layer {layer_id} {name}: oid {last_oid:,}->{next_oid:,}; batch={len(features):,} wrote={written:,} quarantined={quarantined + invalid_count}")
        except Exception as exc:
            latest_state = state_get(sb, pipeline)
            state_put(
                sb,
                pipeline,
                status="error",
                cursor_offset=max(0, int(latest_state.get("cursor_offset") or 0)),
                cursor_key=latest_state.get("cursor_key"),
                cycle_key=latest_state.get("cycle_key"),
                processed_count=int(latest_state.get("processed_count") or 0),
                consecutive_failures=int(latest_state.get("consecutive_failures") or 0) + 1,
                last_error=f"{type(exc).__name__}: {exc}",
                last_heartbeat_at=now(),
                meta={**(latest_state.get("meta") or {}), "cursor_mode": "oid_keyset_v8"},
            )
            raise
        finally:
            memory_guard(f"DLS layer {layer_id}")




async def dls_pipeline(sb, worker_run_id: str) -> None:
    for layer_id in ACTIVE_DLS_LAYERS:
        try:
            await dls_layer_pipeline(sb, layer_id, worker_run_id)
        except Exception as exc:
            log(f"DLS layer {layer_id} failed; continuing with unrelated layers: {type(exc).__name__}: {exc}")
        finally:
            memory_guard(f"DLS layer {layer_id}")
        await asyncio.sleep(1)



# -----------------------------------------------------------------------------
# Official market datasets
# -----------------------------------------------------------------------------


def pxweb_api_url(page_url: str) -> str:
    """Convert a CYSTAT PxWeb browser URL to the corresponding API v1 URL."""
    return page_url.replace("/pxweb/en/", "/pxweb/api/v1/en/").rstrip("/")



def write_structured_official_rows(
    sb,
    *,
    worker_run_id: str,
    source: str,
    dataset_key: str,
    dataset_name: str,
    source_page: str,
    file_url: str,
    file_sha256: str,
    rows: Iterable[dict[str, Any]],
    raw_meta: dict[str, Any],
) -> tuple[int, int]:
    """Stage rows in bounded idempotent batches, then lease-fenced atomic promotion."""
    if _official_sha_is_current(sb, source, dataset_key, file_sha256):
        return 0, 0

    run_id = _prepare_official_stage(sb, source, dataset_key)
    written = 0
    batch: list[dict[str, Any]] = []
    try:
        for index, row in enumerate(rows, start=1):
            cleaned = {str(k): clean_cell(v) for k, v in row.items() if clean_cell(v) is not None}
            if not cleaned:
                continue
            batch.append({"source": source, "dataset_key": dataset_key, "sheet_name": "pxweb", "row_number": index, "row_data": cleaned, "synced_at": now()})
            if len(batch) >= 250:
                written += _stage_official_batch(sb, run_id, batch)
        written += _stage_official_batch(sb, run_id, batch)
        if written == 0:
            raise RuntimeError("Dataset parsed to zero rows; live dataset preserved")
        _promote_official_stage(
            sb,
            worker_run_id=worker_run_id,
            source=source,
            dataset_key=dataset_key,
            run_id=run_id,
            dataset_name=dataset_name,
            source_page=source_page,
            file_url=file_url,
            file_sha256=file_sha256,
            raw_meta={**raw_meta, "rows": written},
        )
    finally:
        batch.clear()
        gc.collect()
    return 1, written




def sync_pxweb_dataset(sb, worker_run_id: str, prefix: str, name: str, page_url: str) -> tuple[int, int]:
    """Stream bounded PxWeb CSV, hash it, skip unchanged data, then atomically promote."""
    api_url = pxweb_api_url(page_url)
    headers = {"User-Agent": UA, "Accept": "application/json"}
    temp_path: str | None = None
    metadata: dict[str, Any] = {}
    variables: list[dict[str, Any]] = []
    try:
        with httpx.Client(timeout=120, follow_redirects=True, headers=headers) as client:
            meta_response = sync_get(client, api_url, retries=4)
            if meta_response is None:
                raise RuntimeError("PxWeb metadata unavailable")
            metadata = meta_response.json()
            variables = metadata.get("variables") or []
            if not variables:
                raise RuntimeError("PxWeb metadata has no variables")
            query = [{"code": variable["code"], "selection": {"filter": "all", "values": ["*"]}} for variable in variables]
            digest = hashlib.sha256()
            size = 0
            with client.stream("POST", api_url, json={"query": query, "response": {"format": "csv"}}, headers={**headers, "Content-Type": "application/json"}) as response:
                response.raise_for_status()
                with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as temp:
                    temp_path = temp.name
                    for chunk in response.iter_bytes(1024 * 512):
                        size += len(chunk)
                        if size > OFFICIAL_MAX_FILE_BYTES:
                            raise RuntimeError(f"PxWeb response exceeds PLANA_OFFICIAL_MAX_FILE_MB ({size / 1048576:.1f} MB)")
                        digest.update(chunk)
                        temp.write(chunk)

        file_sha256 = digest.hexdigest()
        dataset_key = f"{prefix}:{metadata.get('title') or prefix}"
        if _official_sha_is_current(sb, "cystat", dataset_key, file_sha256):
            return 0, 0

        def row_iter() -> Iterable[dict[str, Any]]:
            assert temp_path is not None
            for dataframe in pd.read_csv(temp_path, chunksize=1000):
                try:
                    for row in dataframe.to_dict(orient="records"):
                        yield row
                finally:
                    del dataframe
                    memory_guard(f"pxweb {prefix}")

        return write_structured_official_rows(
            sb,
            worker_run_id=worker_run_id,
            source="cystat",
            dataset_key=dataset_key,
            dataset_name=name,
            source_page=page_url,
            file_url=api_url,
            file_sha256=file_sha256,
            rows=row_iter(),
            raw_meta={"parser": "pxweb_api_csv_stream_atomic_v8", "matrix": metadata.get("title"), "variables": [v.get("code") for v in variables]},
        )
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass
        gc.collect()




def pxweb_pipeline(sb, worker_run_id: str) -> None:
    for prefix, name, page_url in PXWEB_DATASETS:
        pipeline = f"official_cystat_{prefix}"
        try:
            state_put(sb, pipeline, status="running", last_error=None, last_heartbeat_at=now())
            datasets_written, rows_written = sync_pxweb_dataset(sb, worker_run_id, prefix, name, page_url)
            state_put(sb, pipeline, status="done", cursor_offset=0, processed_count=rows_written, last_completed_at=now(), last_error=None, consecutive_failures=0, last_heartbeat_at=now())
            log(f"OFFICIAL cystat/{prefix}: {'imported ' + format(rows_written, ',') + ' rows' if datasets_written else 'unchanged; skipped parse/write'} via PxWeb API")
        except Exception as exc:
            previous = state_get(sb, pipeline)
            state_put(sb, pipeline, status="error", last_error=f"{type(exc).__name__}: {exc}", consecutive_failures=int(previous.get("consecutive_failures") or 0) + 1, last_heartbeat_at=now())
            log(f"OFFICIAL cystat/{prefix} failed; continuing: {type(exc).__name__}: {exc}")
        finally:
            memory_guard(f"official cystat/{prefix}")



def dataset_key_for(prefix: str, url: str) -> str:
    filename = urlparse(url).path.rsplit("/", 1)[-1] or "download"
    filename = re.sub(r"[^a-zA-Z0-9_.-]+", "_", filename)[:150]
    return f"{prefix}:{filename}"



def discover_downloads(client: httpx.Client, page_url: str, *, follow_detail_pages: bool = True) -> list[str]:
    response = sync_get(client, page_url)
    if response is None:
        return []
    soup = BeautifulSoup(response.text, "html.parser")
    direct: list[str] = []
    details: list[str] = []
    for anchor in soup.find_all("a", href=True):
        url = urljoin(page_url, anchor["href"])
        label = " ".join(anchor.stripped_strings).lower()
        path = url.lower().split("?", 1)[0]
        if path.endswith(EXTS) or "download the file" in label or "data series" in label:
            direct.append(url)
        elif follow_detail_pages and (re.search(r"\b20(?:2[3-9]|[3-9]\d)\b", label) or "/statistics/" in url.lower()):
            details.append(url)
    if follow_detail_pages:
        for detail_url in sorted(set(details))[:30]:
            detail = sync_get(client, detail_url, retries=3)
            if detail is None:
                continue
            detail_soup = BeautifulSoup(detail.text, "html.parser")
            for anchor in detail_soup.find_all("a", href=True):
                url = urljoin(detail_url, anchor["href"])
                label = " ".join(anchor.stripped_strings).lower()
                path = url.lower().split("?", 1)[0]
                if path.endswith(EXTS) or "download the file" in label or "data series" in label:
                    direct.append(url)
            time.sleep(0.5)
    return sorted(set(direct))




def _stage_official_batch(sb, run_id: str, batch: list[dict[str, Any]]) -> int:
    if not batch:
        return 0
    staged = [{**row, "run_id": run_id, "staged_at": now()} for row in batch]

    def write_chunk(chunk: list[dict[str, Any]]) -> int:
        try:
            db_retry(
                lambda: sb.table("official_market_rows_stage").upsert(
                    chunk,
                    on_conflict="run_id,source,dataset_key,sheet_name,row_number",
                ).execute(),
                label=f"official idempotent stage rows={len(chunk)}",
            )
            return len(chunk)
        except Exception:
            if len(chunk) == 1:
                raise
            midpoint = len(chunk) // 2
            return write_chunk(chunk[:midpoint]) + write_chunk(chunk[midpoint:])

    count = write_chunk(staged)
    batch.clear()
    return count




def _prepare_official_stage(sb, source: str, dataset_key: str) -> str:
    # run_id isolates concurrent/stale staging attempts. Old runs are removed by
    # cleanup_plana_official_stage; never delete another worker's in-flight run.
    return str(uuid.uuid4())



def _official_sha_is_current(sb, source: str, dataset_key: str, sha256: str) -> bool:
    response = db_retry(
        lambda: sb.table("official_market_datasets")
        .select("file_sha256")
        .eq("source", source)
        .eq("dataset_key", dataset_key)
        .limit(1)
        .execute(),
        label=f"official hash lookup {source}/{dataset_key}",
    )
    rows = response.data or []
    return bool(rows and rows[0].get("file_sha256") == sha256)



def _promote_official_stage(
    sb,
    *,
    worker_run_id: str,
    source: str,
    dataset_key: str,
    run_id: str,
    dataset_name: str,
    source_page: str,
    file_url: str,
    file_sha256: str,
    raw_meta: dict[str, Any],
) -> None:
    db_retry(
        lambda: sb.rpc(
            "promote_plana_official_dataset",
            {
                "p_worker_run_id": worker_run_id,
                "p_source": source,
                "p_dataset_key": dataset_key,
                "p_run_id": run_id,
                "p_dataset_name": dataset_name,
                "p_source_page": source_page,
                "p_file_url": file_url,
                "p_file_sha256": file_sha256,
                "p_raw_meta": raw_meta,
            },
        ).execute(),
        label=f"official lease-fenced atomic promote {source}/{dataset_key}",
    )



def stream_official_download(
    client: httpx.Client,
    url: str,
    *,
    max_bytes: int,
    retries: int = 4,
) -> tuple[str, str, str, str, int] | None:
    """Stream an official file to disk so file bytes never accumulate in RAM."""
    for attempt in range(1, retries + 1):
        temp_path = ""
        try:
            hasher = hashlib.sha256()
            size = 0
            with client.stream("GET", url) as response:
                status = response.status_code
                if status != 200:
                    if status in (403, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524) and attempt < retries:
                        time.sleep(min(5 * attempt, 20))
                        continue
                    log(f"  official download failed status={status}: {url}")
                    return None
                content_type = response.headers.get("content-type", "")
                final_url = str(response.url)
                lower = final_url.lower().split("?", 1)[0]
                suffix = ".pdf" if lower.endswith(".pdf") or "application/pdf" in content_type.lower() else (".csv" if lower.endswith(".csv") or "csv" in content_type.lower() else (".xls" if lower.endswith(".xls") else ".xlsx"))
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp:
                    temp_path = temp.name
                    for chunk in response.iter_bytes(chunk_size=256 * 1024):
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > max_bytes:
                            raise MemoryError(f"official file exceeds configured limit ({max_bytes / 1048576:.0f} MB)")
                        hasher.update(chunk)
                        temp.write(chunk)
            if size < 100:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
                return None
            return temp_path, hasher.hexdigest(), content_type, final_url, size
        except Exception as exc:
            if temp_path:
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass
            if attempt >= retries:
                log(f"  official stream failed after {retries} attempts: {url}: {type(exc).__name__}: {exc}")
                return None
            time.sleep(min(5 * attempt, 20))
    return None



def write_official_dataset(
    sb,
    *,
    worker_run_id: str,
    source: str,
    prefix: str,
    name: str,
    source_page: str,
    url: str,
    temp_path: str,
    file_sha256: str,
    content_type: str,
) -> tuple[int, int]:
    dataset_key = dataset_key_for(prefix, url)
    if _official_sha_is_current(sb, source, dataset_key, file_sha256):
        return 0, 0

    run_id = _prepare_official_stage(sb, source, dataset_key)
    batch: list[dict[str, Any]] = []
    written = 0
    sheet_names: list[str] = []
    path = urlparse(url).path.lower().split("?", 1)[0]
    try:
        if path.endswith(".pdf") or "pdf" in content_type.lower():
            parser = "pdf_page_text_atomic"
            reader = PdfReader(temp_path)
            for index, page in enumerate(reader.pages, start=1):
                text = clean_text(page.extract_text())
                if not text:
                    continue
                batch.append({"source": source, "dataset_key": dataset_key, "sheet_name": "pdf", "row_number": index, "row_data": {"page_text": text}, "synced_at": now()})
                if len(batch) >= 100:
                    written += _stage_official_batch(sb, run_id, batch)
                memory_guard(f"PDF {source}/{prefix} page={index}")
        elif path.endswith(".csv") or "csv" in content_type.lower():
            parser = "csv_chunk_atomic"
            row_number = 0
            for dataframe in pd.read_csv(temp_path, chunksize=1000):
                try:
                    for row in dataframe.to_dict(orient="records"):
                        row_number += 1
                        values = {str(key): clean_cell(value) for key, value in row.items()}
                        values = {key: value for key, value in values.items() if value is not None}
                        if not values:
                            continue
                        batch.append({"source": source, "dataset_key": dataset_key, "sheet_name": "csv", "row_number": row_number, "row_data": values, "synced_at": now()})
                        if len(batch) >= 250:
                            written += _stage_official_batch(sb, run_id, batch)
                finally:
                    del dataframe
                    memory_guard(f"CSV {source}/{prefix}")
        elif path.endswith(".xls") and not path.endswith(".xlsx"):
            parser = "xls_ondemand_atomic"
            workbook = xlrd.open_workbook(temp_path, on_demand=True)
            try:
                for sheet_name in workbook.sheet_names():
                    sheet_names.append(str(sheet_name))
                    sheet = workbook.sheet_by_name(sheet_name)
                    for row_index in range(sheet.nrows):
                        values = {f"c{i + 1}": clean_cell(value) for i, value in enumerate(sheet.row_values(row_index))}
                        values = {key: value for key, value in values.items() if value is not None}
                        if not values:
                            continue
                        batch.append({"source": source, "dataset_key": dataset_key, "sheet_name": str(sheet_name), "row_number": row_index + 1, "row_data": values, "synced_at": now()})
                        if len(batch) >= 250:
                            written += _stage_official_batch(sb, run_id, batch)
                    workbook.unload_sheet(sheet_name)
                    memory_guard(f"XLS {source}/{prefix} sheet={sheet_name}")
            finally:
                workbook.release_resources()
        else:
            parser = "xlsx_read_only_atomic"
            workbook = openpyxl.load_workbook(temp_path, read_only=True, data_only=True)
            try:
                for worksheet in workbook.worksheets:
                    sheet_name = str(worksheet.title)
                    sheet_names.append(sheet_name)
                    for row_index, row in enumerate(worksheet.iter_rows(values_only=True), start=1):
                        values = {f"c{i + 1}": clean_cell(value) for i, value in enumerate(row)}
                        values = {key: value for key, value in values.items() if value is not None}
                        if not values:
                            continue
                        batch.append({"source": source, "dataset_key": dataset_key, "sheet_name": sheet_name, "row_number": row_index, "row_data": values, "synced_at": now()})
                        if len(batch) >= 250:
                            written += _stage_official_batch(sb, run_id, batch)
                    memory_guard(f"XLSX {source}/{prefix} sheet={sheet_name}")
            finally:
                workbook.close()

        written += _stage_official_batch(sb, run_id, batch)
        if written == 0:
            raise RuntimeError("Dataset parsed to zero rows; live dataset preserved")
        _promote_official_stage(
            sb,
            worker_run_id=worker_run_id,
            source=source,
            dataset_key=dataset_key,
            run_id=run_id,
            dataset_name=name,
            source_page=source_page,
            file_url=url,
            file_sha256=file_sha256,
            raw_meta={"sheets": sheet_names[:200], "parser": parser, "content_type": content_type, "rows": written},
        )
        return 1, written
    finally:
        batch.clear()
        gc.collect()




def filter_official_downloads(source: str, prefix: str, downloads: list[str]) -> list[str]:
    """Keep only files that clearly belong to the requested official dataset."""
    if source != "dls":
        return downloads
    terms = {
        "transfers": ("transfer", "sale-20", "sales-20", "μεταβιβ"),
        "contracts": ("contract", "πωλητηρ"),
        "foreign_buyers": ("foreign-buyer", "foreign_buy", "αλλοδαπ"),
        "mortgages": ("mortgage", "υποθηκ"),
    }.get(prefix, ())
    excluded = ("private-cadastral-surveyor", "cadastral-surveyor", "list_of_private", "χάρτης", "map-")
    kept = []
    for url in downloads:
        text = unquote(url).lower().replace("_", "-")
        if any(x in text for x in excluded):
            continue
        if terms and not any(x in text for x in terms):
            continue
        kept.append(url)
    return kept


def sync_official_source(sb, worker_run_id: str, source: str, datasets: list[tuple[str, str, str]]) -> None:
    source_started = now()
    source_dataset_count = 0
    source_row_count = 0
    source_errors: list[str] = []
    db_retry(
        lambda: sb.rpc(
            "set_plana_official_sync_state",
            {
                "p_worker_run_id": worker_run_id,
                "p_source": source,
                "p_values": {"status": "running", "last_started_at": source_started, "updated_at": source_started},
            },
        ).execute(),
        label=f"official sync state {source} start",
    )

    headers = {"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.9"}
    with httpx.Client(timeout=90, follow_redirects=True, headers=headers) as client:
        for prefix, name, page_url in datasets:
            pipeline = f"official_{source}_{prefix}"
            state = state_get(sb, pipeline)
            dataset_count = 0
            row_count = 0
            try:
                downloads = discover_downloads(client, page_url, follow_detail_pages=source == "dls")
                if source == "cbc":
                    downloads = [url for url in downloads if url.lower().split("?", 1)[0].endswith((".xls", ".xlsx", ".csv"))]
                downloads = filter_official_downloads(source, prefix, sorted(set(downloads)))
                last_completed_url = clean_text(state.get("cursor_key"))
                if last_completed_url and last_completed_url in downloads:
                    candidate_index = downloads.index(last_completed_url) + 1
                elif last_completed_url:
                    candidate_index = 0
                else:
                    candidate_index = max(0, min(int(state.get("cursor_offset") or 0), len(downloads)))

                state_put(sb, pipeline, status="running", cursor_offset=candidate_index, last_error=None, last_heartbeat_at=now())
                log(f"OFFICIAL {source}/{prefix}: candidates={len(downloads)} resume_index={candidate_index}")
                failed = False
                for index in range(candidate_index, len(downloads)):
                    url = downloads[index]
                    try:
                        streamed = stream_official_download(client, url, max_bytes=OFFICIAL_MAX_FILE_BYTES, retries=4)
                        if streamed is None:
                            raise RuntimeError("download unavailable or above configured file limit")
                        temp_path, file_sha256, content_type, final_url, file_size = streamed
                        try:
                            if "text/html" in content_type.lower() and not final_url.lower().split("?", 1)[0].endswith(EXTS):
                                raise RuntimeError("HTML response instead of dataset")
                            datasets_written, rows_written = write_official_dataset(
                                sb,
                                worker_run_id=worker_run_id,
                                source=source,
                                prefix=prefix,
                                name=name,
                                source_page=page_url,
                                url=final_url,
                                temp_path=temp_path,
                                file_sha256=file_sha256,
                                content_type=content_type,
                            )
                            dataset_count += datasets_written
                            row_count += rows_written
                            log(f"  {'imported ' + format(rows_written, ',') + ' rows' if datasets_written else 'unchanged; skipped parse/write'} ({file_size / 1048576:.1f} MB streamed): {final_url}")
                        finally:
                            try:
                                os.unlink(temp_path)
                            except OSError:
                                pass
                        # Advance only after committed promotion or unchanged-hash proof.
                        state_put(
                            sb,
                            pipeline,
                            status="running",
                            cursor_offset=index + 1,
                            cursor_key=url[:1000],
                            processed_count=row_count,
                            last_error=None,
                            last_heartbeat_at=now(),
                        )
                    except Exception as exc:
                        message = f"{url}: {type(exc).__name__}: {exc}"
                        source_errors.append(f"{prefix}:{message}")
                        failed = True
                        state_put(
                            sb,
                            pipeline,
                            status="error",
                            cursor_offset=index,
                            cursor_key=last_completed_url,
                            consecutive_failures=int(state.get("consecutive_failures") or 0) + 1,
                            last_error=message[-2000:],
                            last_heartbeat_at=now(),
                        )
                        log(f"  official candidate failed; preserving checkpoint for retry: {message}")
                        break
                    finally:
                        memory_guard(f"official {source}/{prefix} candidate={index + 1}")

                if not failed:
                    state_put(
                        sb,
                        pipeline,
                        status="done",
                        cursor_offset=0,
                        cursor_key=None,
                        processed_count=row_count,
                        consecutive_failures=0,
                        last_completed_at=now(),
                        last_error=None,
                        last_heartbeat_at=now(),
                    )
            except Exception as exc:
                message = f"{prefix}: {type(exc).__name__}: {exc}"
                source_errors.append(message)
                previous = state_get(sb, pipeline)
                state_put(sb, pipeline, status="error", consecutive_failures=int(previous.get("consecutive_failures") or 0) + 1, last_error=message[-2000:], last_heartbeat_at=now())
                log(f"OFFICIAL {source}/{prefix} failed; continuing with unrelated prefixes: {message}")
            finally:
                source_dataset_count += dataset_count
                source_row_count += row_count
                memory_guard(f"official {source}/{prefix} end")

    completed = now()
    db_retry(
        lambda: sb.rpc(
            "set_plana_official_sync_state",
            {
                "p_worker_run_id": worker_run_id,
                "p_source": source,
                "p_values": {"status": "done" if not source_errors else "partial", "datasets_written": source_dataset_count, "rows_written": source_row_count, "last_error": " | ".join(source_errors)[-2000:] if source_errors else None, "last_started_at": source_started, "last_completed_at": completed, "updated_at": completed},
            },
        ).execute(),
        label=f"official sync state {source} complete",
    )
    log(f"OFFICIAL {source}: datasets={source_dataset_count} rows={source_row_count:,} errors={len(source_errors)}")




def official_pipeline(sb, worker_run_id: str) -> None:
    sync_official_source(sb, worker_run_id, "cbc", [("rppi", "CBC Residential Property Price Indices", CBC_RPPI_PAGE), ("statistical_bulletin", "CBC Statistical Bulletin", CBC_STAT_BULLETIN_PAGE), ("bank_interest_rates", "CBC Bank Interest Rates", CBC_BANK_RATES_PAGE)])
    sync_official_source(sb, worker_run_id, "dls", [(prefix, name, page) for prefix, (name, page) in DLS_STATS.items()])
    sync_official_source(sb, worker_run_id, "cystat", [("construction_cost_m2", "CYSTAT Cost per Square Metre of Completed Private Buildings", CYSTAT_COST_PAGE)])
    if CYSTAT_PXWEB_ENABLED:
        pxweb_pipeline(sb, worker_run_id)
    else:
        log("OFFICIAL cystat PxWeb: disabled until dataset URLs are revalidated")




# -----------------------------------------------------------------------------
# Public market collectors: sitemap/category discovery + JSON-LD/meta parsing
# -----------------------------------------------------------------------------

DEFAULT_PUBLIC_MARKET_SOURCES = [
    {"name":"index", "base":"https://index.cy", "sale":["https://index.cy/for-sale/"], "rent":["https://index.cy/for-rent/"], "include":[r"/property/", r"/ad/", r"/listing/", r"/properties/[^/?#]+$"]},
    {"name":"homecy", "base":"https://home.cy", "sale":["https://home.cy/real-estate-for-sale"], "rent":["https://home.cy/real-estate-to-rent"], "include":[r"/property/", r"/properties/", r"/real-estate/", r"/[^/]+-for-(?:sale|rent)-in-"]},
    {"name":"propertyportal", "base":"https://propertyportal.com.cy", "sale":["https://propertyportal.com.cy/properties-for-sale/"], "rent":["https://propertyportal.com.cy/properties-for-rent/"], "include":[r"/property-\d+/"]},
    {"name":"altamira", "base":"https://www.altamirarealestate.com.cy", "sale":["https://www.altamirarealestate.com.cy/houses-for-sale", "https://www.altamirarealestate.com.cy/flats-for-sale", "https://www.altamirarealestate.com.cy/land-for-sale"], "rent":[], "include":[r"/property/", r"/properties/", r"/asset/", r"/pr\d+", r"/cp\d+"]},
    {"name":"dom", "base":"https://dom.com.cy", "sale":["https://dom.com.cy/en/catalog/sale/"], "rent":["https://dom.com.cy/en/catalog/rent/"], "include":[r"/en/catalog/[^/?#]+/\d+", r"/en/live/[^/?#]+/\d+"]},
]

PUBLIC_EXCLUDE_PARTS = (
    "/login", "/register", "/about", "/contact", "/articles", "/blog", "/agents", "/developers",
    "/privacy", "/terms", "/cookie", "/map", "/search", "mailto:", "javascript:", "#"
)


def public_sources() -> list[dict[str, Any]]:
    if not MARKET_PUBLIC_SOURCES_JSON.strip():
        return DEFAULT_PUBLIC_MARKET_SOURCES
    try:
        value = json.loads(MARKET_PUBLIC_SOURCES_JSON)
        return value if isinstance(value, list) else DEFAULT_PUBLIC_MARKET_SOURCES
    except Exception as exc:
        log(f"MARKET public sources JSON invalid; using built-ins: {exc}")
        return DEFAULT_PUBLIC_MARKET_SOURCES


def robots_allows(client: httpx.Client, base: str, url: str) -> bool:
    robots_url = urljoin(base.rstrip('/') + '/', 'robots.txt')
    try:
        response = client.get(robots_url, timeout=15, follow_redirects=True)
        if response.status_code >= 400:
            return True
        parser = robotparser.RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(response.text.splitlines())
        return parser.can_fetch(UA, url)
    except Exception:
        return True


def _flatten_jsonld(value: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(value, dict):
        out.append(value)
        for child in value.values():
            out.extend(_flatten_jsonld(child))
    elif isinstance(value, list):
        for child in value:
            out.extend(_flatten_jsonld(child))
    return out


def _status_from_text(text: str, default: str) -> str:
    lower = text.lower()
    if any(x in lower for x in ("to rent", "for rent", "monthly", "per month", "/rent/", "rental")):
        return "rent"
    if any(x in lower for x in ("for sale", "to buy", "/sale/", "reserve price", "indicative price")):
        return "sale"
    return default


def _source_listing_id(source: str, url: str, soup: BeautifulSoup | None = None) -> str:
    path = urlparse(url).path.rstrip('/')
    for pattern in (r"property-(\d+)", r"(?:^|[-_/])(PR\d+|CP\d+)(?:$|[-_/])", r"(?:^|[-_/])(\d{4,})(?:$|[-_/])"):
        m = re.search(pattern, path, re.I)
        if m:
            return m.group(1).upper()
    canonical = None
    if soup:
        tag = soup.find("link", rel="canonical")
        canonical = tag.get("href") if tag else None
    basis = canonical or url
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:24]


def _property_jsonld(soup: BeautifulSoup) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for root in jsonlds(soup):
        for item in _flatten_jsonld(root):
            kind = str(item.get("@type") or "").lower()
            if any(x in kind for x in ("product", "offer", "residence", "house", "apartment", "accommodation", "realestate", "place")):
                candidates.append(item)
    return candidates


def parse_public_market_detail(source: str, url: str, html: str, default_status: str) -> dict[str, Any] | None:
    soup = BeautifulSoup(html, "html.parser")
    title = meta(soup, "og:title") or clean_text(soup.title.string if soup.title else None)
    description = meta(soup, "og:description")
    text = clean_text(soup.get_text(" ")) or ""
    status = _status_from_text(f"{title or ''} {url} {text[:2500]}", default_status)
    lid = _source_listing_id(source, url, soup)
    observed = now()
    price = None
    area = beds = baths = lat = lon = None
    locality = district = address_text = None
    property_type = classify(title or "", url)

    for item in _property_jsonld(soup):
        offers = item.get("offers")
        offer_items = offers if isinstance(offers, list) else [offers] if isinstance(offers, dict) else []
        for offer in offer_items:
            price = price or number(offer.get("price") or offer.get("lowPrice"))
        price = price or number(item.get("price"))
        area_obj = item.get("floorSize") or item.get("area")
        if isinstance(area_obj, dict):
            area = area or number(area_obj.get("value"))
        else:
            area = area or number(area_obj)
        beds = beds or number(item.get("numberOfBedrooms") or item.get("numberOfRooms"))
        baths = baths or number(item.get("numberOfBathroomsTotal"))
        geo = item.get("geo")
        if isinstance(geo, dict):
            lat = lat or number(geo.get("latitude"))
            lon = lon or number(geo.get("longitude"))
        address = item.get("address")
        if isinstance(address, dict):
            locality = locality or clean_text(address.get("addressLocality"))
            district = district or clean_text(address.get("addressRegion"))
            address_text = address_text or clean_text(address.get("streetAddress"))
        elif address:
            address_text = address_text or clean_text(address)
        description = description or clean_text(item.get("description"))
        property_type = property_type or classify(str(item.get("@type") or ""), url)

    if price is None:
        m = MONEY_RE.search(text)
        price = number(m.group(1)) if m else None
    if area is None:
        for pat in (r"(?:covered|internal|total|living|area|size)\s*:?[ ]*([\d,.]+)\s*(?:m²|m2|sqm)", r"([\d,.]+)\s*(?:m²|m2|sqm)"):
            m = re.search(pat, text, re.I)
            if m:
                area = number(m.group(1)); break
    if beds is None:
        m = re.search(r"(\d+)\s*(?:bedrooms?|beds?)", text, re.I); beds = number(m.group(1)) if m else None
    if baths is None:
        m = re.search(r"(\d+)\s*(?:bathrooms?|baths?)", text, re.I); baths = number(m.group(1)) if m else None
    if not title or not price:
        return None

    row = {
        "observation_key": f"{source}:{status}:{lid}", "source": source, "source_id": source,
        "source_class": "public_portal", "source_listing_id": lid, "source_url": url,
        "transaction_type": status, "property_type": property_type, "development_status": None,
        "bedrooms": beds, "bathrooms": baths, "covered_area_m2": area, "plot_area_m2": None,
        "asking_price_eur": price if status == "sale" else None,
        "asking_rent_monthly_eur": price if status == "rent" else None,
        "price_per_m2_eur": (price / area) if status == "sale" and price and area and area > 0 else None,
        "rent_per_m2_month_eur": (price / area) if status == "rent" and price and area and area > 0 else None,
        "latitude": lat, "longitude": lon, "district": district, "municipality": None,
        "locality": locality or address_text, "planning_zone": None, "title": title,
        "description": description, "status": "active", "first_seen_at": observed, "last_seen_at": observed,
        "price_changed_at": None, "original_price_eur": price, "current_price_eur": price,
        "confidence": 0.66, "source_adapter": "public_html_jsonld_v1", "source_engine_version": "data_worker_v11",
        "raw_data": {"parser":"public_html_jsonld_v1", "robots_checked":True},
    }
    return row


def discover_public_links(source: dict[str, Any], html: str, page_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    base = str(source.get("base") or page_url)
    host = urlparse(base).netloc.lower().removeprefix("www.")
    patterns = [re.compile(x, re.I) for x in source.get("include", [])]
    ranked: list[tuple[int, str]] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        url = urljoin(base, href).split("#")[0]
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or parsed.netloc.lower().removeprefix("www.") != host:
            continue
        lower = url.lower()
        if any(part in lower for part in PUBLIC_EXCLUDE_PARTS):
            continue
        if url in seen:
            continue
        context = clean_text(anchor.get_text(" ")) or ""
        parent = anchor.parent
        if parent:
            context += " " + (clean_text(parent.get_text(" ")) or "")[:700]
        score = 0
        if any(p.search(parsed.path) for p in patterns): score += 5
        if "€" in context: score += 3
        if re.search(r"\b\d+\s*(?:m²|m2|sqm|bed|bedroom)", context, re.I): score += 2
        if any(x in context.lower() for x in ("for sale", "for rent", "apartment", "house", "villa", "land", "office")): score += 1
        if score >= 5:
            seen.add(url); ranked.append((score, url))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    return [u for _, u in ranked]


async def public_market_pipeline(sb, worker_run_id: str) -> None:
    if not MARKET_PUBLIC_ENABLED:
        log("MARKET public portals: disabled")
        return
    sources = public_sources()
    headers = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"}
    timeout = httpx.Timeout(MARKET_PUBLIC_TIMEOUT_SECONDS, connect=15)
    async with httpx.AsyncClient(headers=headers, timeout=timeout, follow_redirects=True, limits=httpx.Limits(max_connections=6, max_keepalive_connections=4)) as client:
        for source in sources:
            name = clean_text(source.get("name")) or "public"
            base = clean_text(source.get("base")) or ""
            if not base:
                continue
            sync_client = httpx.Client(headers=headers, follow_redirects=True)
            try:
                links_by_status: dict[str, list[str]] = {"sale": [], "rent": []}
                for status in ("sale", "rent"):
                    for start_url in source.get(status, []) or []:
                        if not robots_allows(sync_client, base, start_url):
                            log(f"MARKET {name} {status}: robots.txt disallows {start_url}; skipped")
                            continue
                        page_url = start_url
                        for page_no in range(1, MARKET_PUBLIC_PAGES_PER_SOURCE + 1):
                            try:
                                response = await client.get(page_url)
                                if response.status_code in (401, 403):
                                    log(f"MARKET {name} {status}: access denied {response.status_code}; stopped without bypass")
                                    break
                                if response.status_code == 429:
                                    log(f"MARKET {name} {status}: rate limited; stopped")
                                    break
                                response.raise_for_status()
                                found = discover_public_links(source, response.text, page_url)
                                links_by_status[status].extend(found)
                                soup = BeautifulSoup(response.text, "html.parser")
                                nxt = soup.find("a", rel=lambda x: x and "next" in x) or soup.find("a", string=re.compile(r"next|›|»", re.I))
                                if nxt and nxt.get("href"):
                                    next_url = urljoin(page_url, nxt.get("href"))
                                else:
                                    sep = "&" if "?" in start_url else "?"
                                    next_url = f"{start_url}{sep}page={page_no+1}"
                                if not found and page_no > 1:
                                    break
                                page_url = next_url
                                await asyncio.sleep(MARKET_DETAIL_DELAY)
                            except Exception as exc:
                                log(f"MARKET {name} {status}: discovery page failed: {type(exc).__name__}: {exc}")
                                break

                rows: list[dict[str, Any]] = []
                for status in ("sale", "rent"):
                    unique = list(dict.fromkeys(links_by_status[status]))[:MARKET_PUBLIC_DETAILS_PER_SOURCE]
                    for url in unique:
                        if not robots_allows(sync_client, base, url):
                            continue
                        try:
                            response = await client.get(url)
                            if response.status_code in (401, 403, 429, 404, 410):
                                continue
                            response.raise_for_status()
                            row = parse_public_market_detail(name, str(response.url), response.text, status)
                            if row:
                                rows.append(row)
                            await asyncio.sleep(MARKET_DETAIL_DELAY)
                        except Exception as exc:
                            log(f"  MARKET {name} detail failed {url}: {type(exc).__name__}: {exc}")
                written = save_market_listings_bulk(sb, rows, worker_run_id)
                log(f"MARKET public {name}: discovered={sum(len(v) for v in links_by_status.values())} parsed={len(rows)} written={written}")
                state_put(sb, f"market_public_{name}", status="ok", processed_count=written, last_error=None, last_heartbeat_at=now())
            except Exception as exc:
                log(f"MARKET public {name} failed; continuing: {type(exc).__name__}: {exc}")
                state_put(sb, f"market_public_{name}", status="error", last_error=f"{type(exc).__name__}: {exc}", last_heartbeat_at=now())
            finally:
                sync_client.close()
                memory_guard(f"market public {name}")


# -----------------------------------------------------------------------------
# Main persistent orchestrator
# -----------------------------------------------------------------------------



async def run_cycle(sb, cycle_number: int, worker_run_id: str) -> None:
    log(f"\n=== PLANA DATA V11 cycle {cycle_number} started {now()} ===")
    try:
        await authorized_market_feeds_pipeline(sb, worker_run_id)
    except Exception as exc:
        log(f"MARKET authorized feeds failed; continuing: {type(exc).__name__}: {exc}")
        state_put(sb, "market_authorized_feeds", status="error", last_error=f"{type(exc).__name__}: {exc}", last_heartbeat_at=now())

    try:
        await public_market_pipeline(sb, worker_run_id)
    except Exception as exc:
        log(f"MARKET public portals failed; continuing: {type(exc).__name__}: {exc}")
        state_put(sb, "market_public", status="error", last_error=f"{type(exc).__name__}: {exc}", last_heartbeat_at=now())

    for status in ("sale", "rent"):
        try:
            await market_pipeline(sb, status, worker_run_id)
        except Exception as exc:
            log(f"MARKET {status} pipeline failed; continuing: {type(exc).__name__}: {exc}")
            previous = state_get(sb, f"market_{status}")
            state_put(sb, f"market_{status}", status="error", consecutive_failures=int(previous.get("consecutive_failures") or 0) + 1, last_error=f"{type(exc).__name__}: {exc}", last_heartbeat_at=now())
        finally:
            memory_guard(f"market {status}")

    if cycle_number == 1 or cycle_number % DLS_EVERY_CYCLES == 0:
        try:
            await dls_pipeline(sb, worker_run_id)
        except Exception as exc:
            log(f"DLS pipeline failed; continuing: {type(exc).__name__}: {exc}")
        finally:
            memory_guard("DLS pipeline")

    if cycle_number == 1 or cycle_number % OFFICIAL_EVERY_CYCLES == 0:
        try:
            await asyncio.to_thread(official_pipeline, sb, worker_run_id)
        except Exception as exc:
            log(f"OFFICIAL pipeline failed; continuing: {type(exc).__name__}: {exc}")
        finally:
            memory_guard("OFFICIAL pipeline")

    completed_at = now()
    next_cycle_at = _future_iso(DATA_CYCLE_SECONDS)
    state_put(
        sb,
        "data_orchestrator",
        status="sleeping",
        cursor_page=cycle_number + 1,
        last_completed_at=completed_at,
        last_error=None,
        consecutive_failures=0,
        last_heartbeat_at=completed_at,
        meta={"completed_cycle": cycle_number, "next_cycle_at": next_cycle_at},
    )
    log(f"=== PLANA DATA V11 cycle {cycle_number} complete; next_cycle_at={next_cycle_at} ===")




async def lease_heartbeat_loop(run_id: str) -> None:
    lease_sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
    interval = max(30, min(120, DATA_LEASE_SECONDS // 3))
    while True:
        await asyncio.sleep(interval)
        await asyncio.to_thread(heartbeat_worker, lease_sb, DATA_WORKER_LEASE, run_id, DATA_LEASE_SECONDS)




async def main() -> None:
    load_dotenv()
    missing = [name for name in ("SUPABASE_URL", "SUPABASE_SECRET_KEY") if not os.getenv(name)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
    run_id = str(uuid.uuid4())
    claim_retry_seconds = max(5, env_int("PLANA_DATA_LEASE_CLAIM_RETRY_SECONDS", 15))
    while True:
        claimed = await asyncio.to_thread(
            claim_worker, sb, DATA_WORKER_LEASE, run_id, DATA_LEASE_SECONDS
        )
        if claimed:
            break
        log(
            "PLANA DATA lease is currently owned by another instance; "
            f"waiting {claim_retry_seconds}s and retrying instead of crashing"
        )
        await asyncio.sleep(claim_retry_seconds)

    global ACTIVE_WORKER_RUN_ID
    ACTIVE_WORKER_RUN_ID = run_id
    log(f"PLANA DATA lease acquired run_id={run_id}")

    try:
        cleaned = await asyncio.to_thread(lambda: sb.rpc("cleanup_plana_official_stage").execute().data)
        if cleaned:
            log(f"OFFICIAL stage cleanup removed {cleaned} stale rows")
    except Exception as exc:
        log(f"OFFICIAL stage cleanup warning: {type(exc).__name__}: {exc}")

    orchestrator_state = await asyncio.to_thread(state_get, sb, "data_orchestrator")
    cycle_number = max(1, int(orchestrator_state.get("cursor_page") or 1))
    log("PLANA DATA WORKER V11.4 2GB CRASH-SAFE + MAX PUBLIC MARKET COLLECTION starting")
    log(
        "market_pages_per_cycle="
        f"{MARKET_PAGES_PER_CYCLE} market_max_page={MARKET_MAX_PAGE} detail_concurrency={MARKET_DETAIL_CONCURRENCY} "
        f"market_db_batch={MARKET_DB_BATCH} market_buysell_enabled={MARKET_BUYSELL_ENABLED and MARKET_BUYSELL_LICENSED} "
        f"dls_batch={DLS_BATCH} dls_layers={ACTIVE_DLS_LAYERS} resume_cycle={cycle_number} cycle_seconds={DATA_CYCLE_SECONDS}"
    )

    async def worker_loop() -> None:
        nonlocal cycle_number, orchestrator_state
        while True:
            next_cycle_at = parse_timestamp((orchestrator_state.get("meta") or {}).get("next_cycle_at"))
            if next_cycle_at and next_cycle_at > datetime.now(timezone.utc):
                wait_seconds = max(1.0, (next_cycle_at - datetime.now(timezone.utc)).total_seconds())
                log(f"DATA durable schedule gate: sleeping {wait_seconds:.0f}s before cycle {cycle_number}")
                await asyncio.sleep(wait_seconds)
            try:
                await run_cycle(sb, cycle_number, run_id)
                cycle_number += 1
                orchestrator_state = await asyncio.to_thread(state_get, sb, "data_orchestrator")
            except Exception as exc:
                log(f"UNEXPECTED CYCLE ERROR: {type(exc).__name__}: {exc}")
                previous = await asyncio.to_thread(state_get, sb, "data_orchestrator")
                await asyncio.to_thread(
                    state_put,
                    sb,
                    "data_orchestrator",
                    status="error",
                    cursor_page=cycle_number,
                    consecutive_failures=int(previous.get("consecutive_failures") or 0) + 1,
                    last_error=f"{type(exc).__name__}: {exc}",
                    last_heartbeat_at=now(),
                    meta={**(previous.get("meta") or {}), "next_cycle_at": _future_iso(120)},
                )
                orchestrator_state = await asyncio.to_thread(state_get, sb, "data_orchestrator")

    heartbeat_task = asyncio.create_task(lease_heartbeat_loop(run_id), name="plana-data-heartbeat")
    worker_task = asyncio.create_task(worker_loop(), name="plana-data-worker")
    try:
        done, pending = await asyncio.wait({heartbeat_task, worker_task}, return_when=asyncio.FIRST_EXCEPTION)
        for task in done:
            exc = task.exception()
            if exc:
                raise exc
        raise RuntimeError("PLANA DATA worker task exited unexpectedly")
    finally:
        for task in (heartbeat_task, worker_task):
            task.cancel()
        await asyncio.gather(heartbeat_task, worker_task, return_exceptions=True)
        await asyncio.to_thread(release_worker, sb, DATA_WORKER_LEASE, run_id)



if __name__ == "__main__":
    asyncio.run(main())