"""PLANA.CY automatic official DLS ArcGIS synchroniser V2.

Examples:
  python dls_sync.py --priority
  python dls_sync.py --all
  python dls_sync.py --layers 11,12,28,30,31,32
  python dls_sync.py --priority --enrich

Required:
  SUPABASE_URL
  SUPABASE_SECRET_KEY
"""
from __future__ import annotations
import argparse, asyncio, json, os, time
from datetime import datetime, timezone
from typing import Any
import httpx
from dotenv import load_dotenv
from shapely.geometry import shape
from supabase import create_client

BASE="https://eservices.dls.moi.gov.cy/arcgis/rest/services/National/CadastralMap_EN/MapServer"
LAYERS={
  0:"Parcels",11:"Development Plans",12:"Planning Zones",13:"Postal Code Areas",
  15:"Districts",50:"Municipalities Clusters",16:"Municipalities Communities",
  17:"Quarters",18:"Blocks",19:"Localities",21:"Topographic Points",
  22:"Topographic Lines",23:"Topographic Areas",28:"Buildings",
  30:"Contour Lines 1993",31:"Coast Protection Zone",32:"State Land",
  35:"Sporadic Survey Parcels",36:"Surveyed Parcels",37:"White Zones",
}
PRIORITY=[11,12,13,15,16,17,18,19,28,30,31,32,35,36,37]
CONCURRENCY=max(1,min(int(os.getenv("DLS_SYNC_CONCURRENCY","4")),8))
BATCH=max(50,min(int(os.getenv("DLS_SYNC_BATCH","500")),900))
UPSERT_BATCH=100

def now(): return datetime.now(timezone.utc).isoformat()
def scalar(v):
    if v is None or isinstance(v,(str,int,float,bool)): return v
    return str(v)
def clean_props(d): return {str(k):scalar(v) for k,v in (d or {}).items()}
def geom_wkt(g):
    if not g: return None
    x=shape(g)
    return None if x.is_empty else x.wkt

async def get_json(client,url,params,retries=6):
    delay=1.0
    for attempt in range(retries):
        try:
            r=await client.get(url,params=params)
            r.raise_for_status()
            data=r.json()
            if isinstance(data,dict) and data.get("error"): raise RuntimeError(str(data["error"]))
            return data
        except Exception:
            if attempt==retries-1: raise
            await asyncio.sleep(delay); delay=min(delay*2,20)

def write_rows(sb,rows):
    for i in range(0,len(rows),UPSERT_BATCH):
        sb.table("dls_arcgis_features").upsert(rows[i:i+UPSERT_BATCH],
          on_conflict="layer_id,source_object_id").execute()

async def sync_layer(client,sb,layer_id):
    name=LAYERS[layer_id]; url=f"{BASE}/{layer_id}"
    started=now()
    sb.table("dls_sync_state").upsert({"layer_id":layer_id,"layer_name":name,"source_url":url,
      "last_status":"running","last_started_at":started,"updated_at":started},on_conflict="layer_id").execute()
    try:
        meta=await get_json(client,url,{"f":"json"})
        oid=meta.get("objectIdField") or next((f["name"] for f in meta.get("fields",[]) if f.get("type")=="esriFieldTypeOID"),None)
        if not oid: raise RuntimeError("No object ID field exposed")
        edit=((meta.get("editingInfo") or {}).get("lastEditDate"))
        q=f"{url}/query"
        ids_data=await get_json(client,q,{"f":"json","where":"1=1","returnIdsOnly":"true"})
        ids=sorted(set(ids_data.get("objectIds") or []))
        print(f"[{layer_id:>2}] {name}: {len(ids):,} IDs")
        sem=asyncio.Semaphore(CONCURRENCY)
        written=0
        async def fetch(chunk):
            async with sem:
                return await get_json(client,q,{"f":"geojson","objectIds":",".join(map(str,chunk)),
                  "outFields":"*","returnGeometry":"true","outSR":"4326"})
        for window in range(0,len(ids),BATCH*CONCURRENCY):
            chunks=[ids[i:i+BATCH] for i in range(window,min(window+BATCH*CONCURRENCY,len(ids)),BATCH)]
            results=await asyncio.gather(*(fetch(c) for c in chunks))
            rows=[]
            for data in results:
                for f in data.get("features") or []:
                    p=clean_props(f.get("properties")); object_id=p.get(oid)
                    if object_id is None: continue
                    rows.append({"layer_id":layer_id,"layer_name":name,"source_object_id":int(object_id),
                      "geom":geom_wkt(f.get("geometry")),"properties":p,"source_url":url,
                      "source_last_edit_ms":edit,"synced_at":now()})
            write_rows(sb,rows); written+=len(rows)
            print(f"     {min(window+BATCH*CONCURRENCY,len(ids)):,}/{len(ids):,} fetched; {written:,} written")
        # Delete stale source IDs only when ID list is reasonably sized for REST filters; otherwise upsert is non-destructive.
        completed=now()
        sb.table("dls_sync_state").upsert({"layer_id":layer_id,"layer_name":name,"source_url":url,
          "object_id_field":oid,"geometry_type":meta.get("geometryType"),"source_last_edit_ms":edit,
          "feature_count":written,"last_status":"done","last_error":None,
          "last_started_at":started,"last_completed_at":completed,"updated_at":completed},
          on_conflict="layer_id").execute()
        return written
    except Exception as e:
        sb.table("dls_sync_state").upsert({"layer_id":layer_id,"layer_name":name,"source_url":url,
          "last_status":"error","last_error":str(e)[:2000],"last_started_at":started,"updated_at":now()},
          on_conflict="layer_id").execute()
        raise

async def run(layers,enrich):
    load_dotenv()
    sb=create_client(os.environ["SUPABASE_URL"],os.environ["SUPABASE_SECRET_KEY"])
    limits=httpx.Limits(max_connections=CONCURRENCY+2,max_keepalive_connections=CONCURRENCY+2)
    async with httpx.AsyncClient(timeout=httpx.Timeout(90,connect=30),limits=limits,
      headers={"User-Agent":"PLANA.CY DLS Sync/2.0"}) as client:
        for lid in layers:
            await sync_layer(client,sb,lid)
    if enrich:
        print("Running PostGIS V1 zone enrichment...")
        print(sb.rpc("refresh_plana_dls_enrichment",{"p_limit":100000}).execute().data)
        print("Running PostGIS V2 DLS enrichment...")
        print(sb.rpc("refresh_plana_dls_v2",{"p_limit":100000}).execute().data)
    print("DLS Sync V2 complete.")

def main():
    ap=argparse.ArgumentParser()
    g=ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--priority",action="store_true"); g.add_argument("--all",action="store_true")
    g.add_argument("--layers")
    ap.add_argument("--enrich",action="store_true")
    a=ap.parse_args()
    layers=list(LAYERS) if a.all else PRIORITY if a.priority else [int(x.strip()) for x in a.layers.split(",")]
    unknown=[x for x in layers if x not in LAYERS]
    if unknown: ap.error(f"Unsupported layer IDs: {unknown}")
    asyncio.run(run(layers,a.enrich))
if __name__=="__main__": main()
