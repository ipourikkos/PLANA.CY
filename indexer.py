"""PLANA.CY Index V1 background parcel indexer.

Render command:
    python indexer.py

Recommended environment variables:
    SUPABASE_URL
    SUPABASE_SECRET_KEY
    PLANA_INDEX_BBOX=32.25,34.55,34.75,35.75
    PLANA_INDEX_GRID=24
    PLANA_INDEX_BATCH=120
    PLANA_INDEX_CONCURRENCY=12
    PLANA_INDEX_SLEEP_SECONDS=1.0

The default bbox covers the Republic of Cyprus broadly. The worker discovers DLS
parcels tile-by-tile, skips recently indexed parcels, computes PLANA intelligence,
and upserts compact searchable rows into Supabase.
"""
import asyncio
import json
import math
import os
import time
from datetime import datetime, timezone, timedelta

import httpx
from dotenv import load_dotenv
from supabase import create_client

import app as core


def env_int(name, default):
    try:
        return int(os.getenv(name, default))
    except Exception:
        return default


def env_float(name, default):
    try:
        return float(os.getenv(name, default))
    except Exception:
        return default


def bbox():
    raw=os.getenv("PLANA_INDEX_BBOX","32.25,34.55,34.75,35.75")
    values=[float(x.strip()) for x in raw.split(",")]
    if len(values)!=4:
        raise ValueError("PLANA_INDEX_BBOX must be west,south,east,north")
    return values


def compactness(area, perimeter):
    if not area or not perimeter:
        return None
    return round(max(0.0,min(1.0,4*math.pi*area/(perimeter*perimeter)))*100,1)


def unit_range(proposal):
    import re
    nums=[int(float(x)) for x in re.findall(r"\d+(?:\.\d+)?",str(proposal.get("headline") or ""))]
    return (min(nums[:2]),max(nums[:2])) if nums else (None,None)


def option_by_type(proposals, kind):
    return next((x for x in proposals.get("development_options") or [] if x.get("type")==kind),{})


def score_by_type(decision, kind):
    return next((x for x in decision.get("option_scores") or [] if x.get("type")==kind),{})


def parcel_row(details, feature):
    opportunity=core.analyse_parcel_opportunity(details,{})
    proposals=core.build_viable_development_options(details,None,opportunity)
    decision=core.build_decision_intelligence(details,proposals,opportunity)
    parcel=details.get("parcel") or {}
    potential=details.get("development_potential") or {}
    metrics=core.polygon_geometry_metrics(feature)
    area=core._num(parcel.get("parcel_extent_m2"))
    perimeter=core._num(metrics.get("approx_perimeter_m"))
    longest=core._num(metrics.get("longest_edge_m"))
    shortest=core._num(metrics.get("shortest_edge_m"))
    existing=core._num((details.get("registration_summary") or {}).get("total_enclosed_extent_m2")) or 0
    capacity=core._num(potential.get("theoretical_max_floor_area_m2")) or 0
    gap=max(capacity-existing,0)
    gap_pct=round(gap/capacity*100,1) if capacity else None
    zones=details.get("planning_zones") or []
    zone_text=" / ".join(str(z.get("zone")) for z in zones if z.get("zone"))
    primary=zones[0] if zones else {}
    apt=option_by_type(proposals,"apartments")
    houses=option_by_type(proposals,"house")
    mixed=option_by_type(proposals,"mixed_use")
    apt_low,apt_high=unit_range(apt)
    house_low,house_high=unit_range(houses)
    best=decision.get("best_use") or {}
    known=[
        score_by_type(decision,best.get("type")).get(k)
        for k in ("planning_score","development_efficiency_score","site_constraint_score","market_score","financial_score")
    ]
    confidence=round(100*sum(v is not None for v in known)/len(known)) if known else 0
    opportunity_score=core._num(best.get("score")) or 0
    # Confidence moderates rather than destroys a promising parcel.
    risk_adjusted=round(opportunity_score*(0.72+0.28*confidence/100),1)
    geom=feature.get("geometry")
    return {
        "parcel_id":int(parcel.get("parcel_id") or (feature.get("properties") or {}).get("SBPI_ID_NO")),
        "parcel_number":parcel.get("parcel_number"),
        "district":parcel.get("district"),
        "municipality":parcel.get("municipality"),
        "quarter":parcel.get("quarter"),
        "planning_zone":zone_text or None,
        "centroid_lat":metrics.get("centroid_lat"),
        "centroid_lon":metrics.get("centroid_lon"),
        "geom":geom,
        "parcel_area_m2":area,
        "perimeter_m":perimeter,
        "longest_edge_m":longest,
        "shortest_edge_m":shortest,
        "shape_ratio":round(shortest/longest,4) if longest and shortest else None,
        "compactness":compactness(area,perimeter),
        "orientation_deg":metrics.get("longest_edge_orientation_deg"),
        "density_percent":core._num(primary.get("density_percent")),
        "coverage_percent":core._num(primary.get("coverage_percent")),
        "max_floors":core._num(primary.get("max_floors")),
        "max_height_m":core._num(primary.get("max_height_m")),
        "floor_capacity_m2":capacity or None,
        "coverage_capacity_m2":core._num(potential.get("theoretical_max_coverage_m2")),
        "existing_enclosed_m2":existing or None,
        "development_gap_m2":round(gap,1) if capacity else None,
        "development_gap_percent":gap_pct,
        "planning_score":core._num(score_by_type(decision,best.get("type")).get("planning_score")),
        "development_score":core._num(score_by_type(decision,best.get("type")).get("development_efficiency_score")),
        "site_score":core._num(score_by_type(decision,best.get("type")).get("site_constraint_score")),
        "market_score":core._num(score_by_type(decision,best.get("type")).get("market_score")),
        "financial_score":core._num(score_by_type(decision,best.get("type")).get("financial_score")),
        "opportunity_score":opportunity_score,
        "data_confidence":confidence,
        "risk_adjusted_score":risk_adjusted,
        "apartment_units_low":apt_low,
        "apartment_units_high":apt_high,
        "house_units_low":house_low,
        "house_units_high":house_high,
        "apartment_status":apt.get("status"),
        "house_status":houses.get("status"),
        "mixed_use_status":mixed.get("status"),
        "best_use":best.get("label"),
        "score_version":"index-v1",
        "raw_summary":{
            "best_type":best.get("type"),
            "best_score":best.get("score"),
            "setbacks":proposals.get("setbacks"),
            "constraints":details.get("constraints"),
        },
        "indexed_at":datetime.now(timezone.utc).isoformat(),
        "dls_refreshed_at":datetime.now(timezone.utc).isoformat(),
    }


async def discover_tile(client, west, south, east, north, limit):
    params={
        "f":"geojson","where":"1=1",
        "geometry":json.dumps({"xmin":west,"ymin":south,"xmax":east,"ymax":north,"spatialReference":{"wkid":4326}}),
        "geometryType":"esriGeometryEnvelope","inSR":"4326","outSR":"4326",
        "spatialRel":"esriSpatialRelIntersects",
        "outFields":"SBPI_ID_NO,PARCEL_NBR,SHEET,PLAN_NBR,BLCK_CODE",
        "returnGeometry":"true","resultRecordCount":limit,
    }
    try:
        r=await client.get(core.PARCEL_QUERY,params=params,timeout=12)
        d=r.json() if r.status_code==200 else {}
        return d.get("features") or []
    except Exception:
        return []


async def main():
    load_dotenv()
    sb=create_client(core.require_env("SUPABASE_URL"),core.require_env("SUPABASE_SECRET_KEY"))
    concurrency=env_int("PLANA_INDEX_CONCURRENCY",12)
    grid=env_int("PLANA_INDEX_GRID",24)
    batch=env_int("PLANA_INDEX_BATCH",120)
    sleep_seconds=env_float("PLANA_INDEX_SLEEP_SECONDS",1.0)
    stale_days=env_int("PLANA_INDEX_STALE_DAYS",30)
    west,south,east,north=bbox()
    dx=(east-west)/grid;dy=(north-south)/grid
    core.state["supabase"]=sb
    core.state["http"]=httpx.AsyncClient(
        timeout=httpx.Timeout(12,connect=5),
        limits=httpx.Limits(max_connections=40,max_keepalive_connections=20),
        follow_redirects=True,
        headers={"User-Agent":"PLANA.CY-Indexer/1.0"},
    )
    semaphore=asyncio.Semaphore(concurrency)
    indexed=0
    try:
        for row in range(grid):
            for col in range(grid):
                x1=west+col*dx;x2=east if col==grid-1 else x1+dx
                y1=south+row*dy;y2=north if row==grid-1 else y1+dy
                features=await discover_tile(core.state["http"],x1,y1,x2,y2,batch)
                ids=[]
                by_id={}
                for feature in features:
                    try: pid=int((feature.get("properties") or {}).get("SBPI_ID_NO"))
                    except Exception: continue
                    ids.append(pid);by_id[pid]=feature
                if not ids:
                    continue
                existing={}
                for start in range(0,len(ids),100):
                    result=sb.table("plana_parcels").select("parcel_id,indexed_at").in_("parcel_id",ids[start:start+100]).execute()
                    existing.update({int(x["parcel_id"]):x.get("indexed_at") for x in (result.data or [])})
                cutoff=datetime.now(timezone.utc)-timedelta(days=stale_days)
                work=[]
                for pid,feature in by_id.items():
                    stamp=existing.get(pid)
                    if stamp:
                        try:
                            parsed=datetime.fromisoformat(stamp.replace("Z","+00:00"))
                            if parsed>cutoff: continue
                        except Exception: pass
                    work.append((pid,feature))
                async def analyse(pid,feature):
                    async with semaphore:
                        try:
                            details=await asyncio.wait_for(core.get_canonical_parcel_details(pid),timeout=8)
                            return parcel_row(details,feature)
                        except Exception as exc:
                            print(f"skip {pid}: {exc!r}")
                            return None
                results=await asyncio.gather(*(analyse(pid,f) for pid,f in work))
                rows=[x for x in results if x and x.get("centroid_lat") is not None and x.get("centroid_lon") is not None]
                for start in range(0,len(rows),100):
                    sb.table("plana_parcels").upsert(rows[start:start+100],on_conflict="parcel_id").execute()
                indexed+=len(rows)
                print(f"tile {row+1}/{grid}:{col+1}/{grid} discovered={len(features)} refreshed={len(rows)} total={indexed}")
                await asyncio.sleep(sleep_seconds)
    finally:
        await core.state["http"].aclose()
    print(f"PLANA Index V1 complete. Refreshed {indexed} parcels.")


if __name__=="__main__":
    asyncio.run(main())
