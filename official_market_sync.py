"""PLANA.CY Official Market Sync V1
Database-only collector for public official market/cost datasets.

Run:
  python official_market_sync.py --all
  python official_market_sync.py --source dls
  python official_market_sync.py --source cbc
  python official_market_sync.py --source cystat

Required:
  SUPABASE_URL
  SUPABASE_SECRET_KEY
"""
from __future__ import annotations
import argparse, hashlib, io, json, os, re, time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import httpx
import pandas as pd
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from supabase import create_client

SOURCES={
"dls":[
 ("transfers","DLS Transfers of Sales","https://portal.dls.moi.gov.cy/en/stats_category/enimerosi/statistika/poliseon/"),
 ("contracts","DLS Contracts of Sales","https://portal.dls.moi.gov.cy/en/stats_category/enimerosi/statistika/politirion-engrafon/"),
 ("foreign_buyers","DLS Foreign Buyers","https://portal.dls.moi.gov.cy/en/stats_category/enimerosi/statistika/poliseon-se-allodapous/"),
 ("mortgages","DLS Mortgages","https://portal.dls.moi.gov.cy/en/stats_category/enimerosi/statistika/ypothikon/"),
],
"cbc":[
 ("rppi","CBC Residential Property Price Indices","https://www.centralbank.cy/en/publications/residential-property-price-indices"),
],
"cystat":[
 ("construction_cost_m2","CYSTAT Cost per Square Metre of Completed Private Buildings","https://www.cystat.gov.cy/en/KeyFiguresList?p=0&s=31&tID=3"),
],
}
EXTS=(".xlsx",".xls",".csv")
UA="PLANA.CY official public statistics sync/1.1"
REQUEST_DELAY_SECONDS=2.0
MAX_HTTP_RETRIES=6

def now(): return datetime.now(timezone.utc).isoformat()
def clean(v):
    if pd.isna(v): return None
    if hasattr(v,"item"):
        try:v=v.item()
        except Exception:pass
    if isinstance(v,(pd.Timestamp,)): return v.isoformat()
    if isinstance(v,str):
        v=re.sub(r"\s+"," ",v).strip()
        return v or None
    if isinstance(v,(int,float,bool)): return v
    return str(v)
def key_for(url):
    name=urlparse(url).path.rsplit("/",1)[-1] or "download"
    return re.sub(r"[^a-zA-Z0-9_.-]+","_",name)[:120]
def get(client,url):
    last_error=None
    for attempt in range(MAX_HTTP_RETRIES):
        try:
            r=client.get(url)
            if r.status_code == 429:
                retry_after=r.headers.get("retry-after")
                try:
                    wait=max(float(retry_after), 8.0)
                except Exception:
                    wait=min(15.0 * (attempt + 1), 90.0)
                print(f"  DLS/CBC/CYSTAT rate limited (429). Waiting {wait:.0f}s...")
                time.sleep(wait)
                last_error=httpx.HTTPStatusError(
                    f"429 Too Many Requests for {url}",
                    request=r.request,
                    response=r,
                )
                continue
            r.raise_for_status()
            time.sleep(REQUEST_DELAY_SECONDS)
            return r
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            last_error=exc
            wait=min(5.0 * (attempt + 1), 30.0)
            print(f"  temporary network error. Retrying in {wait:.0f}s...")
            time.sleep(wait)
    if last_error:
        raise last_error
    raise RuntimeError(f"Unable to fetch {url}")
def discover(client,page):
    html=get(client,page).text
    soup=BeautifulSoup(html,"html.parser")
    links=[]
    for a in soup.find_all("a",href=True):
        u=urljoin(page,a["href"])
        low=u.lower().split("?")[0]
        label=" ".join(a.stripped_strings)
        if low.endswith(EXTS) or "download" in label.lower():
            links.append(u)
    # Follow recent detail pages from category pages and discover their downloads.
    if not links:
        for a in soup.find_all("a",href=True):
            label=" ".join(a.stripped_strings).lower()
            u=urljoin(page,a["href"])
            if ("2026" in label or "2025" in label or "data series" in label or "cost per square metre" in label):
                try:
                    sub=BeautifulSoup(get(client,u).text,"html.parser")
                    for b in sub.find_all("a",href=True):
                        x=urljoin(u,b["href"]); xl=x.lower().split("?")[0]
                        bl=" ".join(b.stripped_strings).lower()
                        if xl.endswith(EXTS) or "download" in bl or "data series" in bl:
                            links.append(x)
                except Exception: pass
    return list(dict.fromkeys(links))

def parse_file(content,url):
    low=url.lower().split("?")[0]
    if low.endswith(".csv"):
        return {"csv":pd.read_csv(io.BytesIO(content),header=None)}
    try:
        book=pd.read_excel(io.BytesIO(content),sheet_name=None,header=None)
        return book
    except Exception:
        return {}

def write_file(sb,source,base_key,name,page,url,content):
    sha=hashlib.sha256(content).hexdigest()
    dataset_key=f"{base_key}:{key_for(url)}"
    sheets=parse_file(content,url)
    if not sheets:return 0,0
    sb.table("official_market_datasets").upsert({
      "source":source,"dataset_key":dataset_key,"dataset_name":name,
      "source_page":page,"file_url":url,"file_sha256":sha,
      "last_synced_at":now(),"raw_meta":{"sheets":list(sheets),"bytes":len(content)}
    },on_conflict="source,dataset_key").execute()
    total=0
    # Replace rows for this immutable dataset snapshot key.
    sb.table("official_market_rows").delete().eq("source",source).eq("dataset_key",dataset_key).execute()
    for sheet,df in sheets.items():
        rows=[]
        for idx,row in df.iterrows():
            vals={f"c{i+1}":clean(v) for i,v in enumerate(row.tolist())}
            vals={k:v for k,v in vals.items() if v is not None}
            if not vals:continue
            rows.append({"source":source,"dataset_key":dataset_key,"sheet_name":str(sheet),
                         "row_number":int(idx)+1,"row_data":vals,"synced_at":now()})
            if len(rows)>=500:
                for j in range(0,len(rows),100):
                    sb.table("official_market_rows").insert(rows[j:j+100]).execute()
                total+=len(rows); rows=[]
        if rows:
            for j in range(0,len(rows),100):
                sb.table("official_market_rows").insert(rows[j:j+100]).execute()
            total+=len(rows)
    return 1,total

def sync_source(sb,source):
    started=now(); datasets=rows=0
    sb.table("official_market_sync_state").upsert({
      "source":source,"status":"running","last_started_at":started,"updated_at":started
    },on_conflict="source").execute()
    try:
        with httpx.Client(timeout=60,follow_redirects=True,headers={"User-Agent":UA,"Accept-Language":"en-GB,en;q=0.9"}) as client:
            for base_key,name,page in SOURCES[source]:
                links=discover(client,page)
                print(f"{name}: {len(links)} downloadable candidates")
                for url in links:
                    try:
                        r=get(client,url)
                        ct=(r.headers.get("content-type") or "").lower()
                        if len(r.content)<100 or ("html" in ct and not url.lower().split("?")[0].endswith(EXTS)):
                            continue
                        d,n=write_file(sb,source,base_key,name,page,str(r.url),r.content)
                        datasets+=d; rows+=n
                        if d: print(f"  imported {n:,} rows: {r.url}")
                    except Exception as e:
                        print(f"  skip {url}: {e}")
        completed=now()
        sb.table("official_market_sync_state").upsert({
          "source":source,"status":"done","datasets_written":datasets,"rows_written":rows,
          "last_error":None,"last_started_at":started,"last_completed_at":completed,"updated_at":completed
        },on_conflict="source").execute()
        print(f"{source}: {datasets} datasets, {rows:,} rows")
    except Exception as e:
        sb.table("official_market_sync_state").upsert({
          "source":source,"status":"error","last_error":str(e)[:2000],
          "last_started_at":started,"updated_at":now()
        },on_conflict="source").execute()
        raise

def main():
    load_dotenv()
    ap=argparse.ArgumentParser()
    g=ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all",action="store_true")
    g.add_argument("--source",choices=list(SOURCES))
    a=ap.parse_args()
    sb=create_client(os.environ["SUPABASE_URL"],os.environ["SUPABASE_SECRET_KEY"])
    failed=[]
    for source in list(SOURCES) if a.all else [a.source]:
        try:
            sync_source(sb,source)
        except Exception as exc:
            failed.append((source,str(exc)))
            print(f"{source}: sync failed but remaining sources will continue: {exc}")
    if failed:
        print("Completed with source errors:")
        for source,error in failed:
            print(f"  {source}: {error}")
if __name__=="__main__":main()
