"""PLANA.CY Market Sync V1 — BuySell first, multi-source schema.

Run:
  python market_sync.py --source buysell --pages 100
  python market_sync.py --source buysell --pages 500

Required:
  SUPABASE_URL
  SUPABASE_SECRET_KEY

Collector uses BuySell's publicly documented AI-agent search URL structure and
unique listing IDs. It is deliberately rate-limited and preserves price history.
"""
from __future__ import annotations
import argparse, asyncio, json, os, re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from supabase import create_client

BASE="https://www.buysellcyprus.com"
SOURCE="buysell"
ID_RE=re.compile(r"-(\d+)\.html(?:$|\?)")
MONEY_RE=re.compile(r"€\s*([\d,.]+)")
M2_RE=re.compile(r"([\d,.]+)\s*(?:m²|m2|sqm)",re.I)

def now(): return datetime.now(timezone.utc).isoformat()
def number(v):
    if v is None:return None
    m=re.search(r"[\d,.]+",str(v))
    if not m:return None
    s=m.group().replace(",","")
    try:return float(s)
    except:return None
def text(v):
    if v is None:return None
    s=re.sub(r"\s+"," ",str(v)).strip()
    return s or None
def listing_id(url):
    m=ID_RE.search(url)
    return m.group(1) if m else None
def walk_json(x):
    if isinstance(x,dict):
        yield x
        for v in x.values(): yield from walk_json(v)
    elif isinstance(x,list):
        for v in x: yield from walk_json(v)
def jsonlds(soup):
    out=[]
    for tag in soup.select('script[type="application/ld+json"]'):
        try: out.extend(walk_json(json.loads(tag.string or tag.get_text())))
        except Exception: pass
    return out
def meta(soup,key):
    t=soup.find("meta",attrs={"property":key}) or soup.find("meta",attrs={"name":key})
    return text(t.get("content")) if t else None
def classify(title,url):
    s=(title+" "+url).lower()
    for k in ["apartment","house","villa","land","plot","office","shop","building","warehouse"]:
        if k in s:return k
    return None
def parse_location(url):
    parts=[x for x in urlparse(url).path.split("/") if x]
    if len(parts)>=4 and parts[0]=="property-for-sale":
        return parts[1].replace("-"," ").title(),parts[2].replace("-"," ").title()
    return None,None
def links_from_search(html):
    soup=BeautifulSoup(html,"html.parser"); seen={}
    for a in soup.find_all("a",href=True):
        u=urljoin(BASE,a["href"]).split("#")[0]
        lid=listing_id(u)
        if lid and "/property-for-sale/" in u: seen[lid]=u
    return list(seen.values())
def parse_detail(url,html):
    soup=BeautifulSoup(html,"html.parser"); lid=listing_id(url)
    title=meta(soup,"og:title") or text(soup.title.string if soup.title else None)
    desc=meta(soup,"og:description")
    district,locality=parse_location(url)
    data={"source":SOURCE,"source_listing_id":lid,"url":url,"status":"active",
          "listing_status":"sale","title":title,"district":district,"locality":locality,
          "property_type":classify(title or "",url),"description":desc,
          "last_seen_at":now(),"raw_data":{"parser":"market_sync_v1"}}
    candidates=jsonlds(soup)
    raw_text=text(soup.get_text(" "))
    for d in candidates:
        offers=d.get("offers")
        if isinstance(offers,dict) and data.get("price_eur") is None:
            data["price_eur"]=number(offers.get("price"))
        if data.get("price_eur") is None and d.get("priceCurrency")=="EUR":
            data["price_eur"]=number(d.get("price"))
        addr=d.get("address")
        if isinstance(addr,dict):
            data["locality"]=text(addr.get("addressLocality")) or data["locality"]
            data["district"]=text(addr.get("addressRegion")) or data["district"]
        geo=d.get("geo")
        if isinstance(geo,dict):
            data["latitude"]=number(geo.get("latitude"))
            data["longitude"]=number(geo.get("longitude"))
        if not data.get("provider_name"):
            seller=d.get("seller") or d.get("provider")
            if isinstance(seller,dict): data["provider_name"]=text(seller.get("name"))
        if not data.get("description"): data["description"]=text(d.get("description"))
    if data.get("price_eur") is None and raw_text:
        m=MONEY_RE.search(raw_text)
        if m:data["price_eur"]=number(m.group(1))
    # Conservative label-based extraction from visible text.
    patterns={
      "internal_area_m2":[r"(?:internal|covered|living)\s+area\s*:?\s*([\d,.]+)\s*(?:m²|m2|sqm)"],
      "plot_area_m2":[r"(?:plot|land)\s+(?:area|size)\s*:?\s*([\d,.]+)\s*(?:m²|m2|sqm)"],
      "bedrooms":[r"bedrooms?\s*:?\s*(\d+)"],
      "bathrooms":[r"bathrooms?\s*:?\s*(\d+)"],
    }
    low=(raw_text or "").lower()
    for field,ps in patterns.items():
        for p in ps:
            m=re.search(p,low,re.I)
            if m:data[field]=number(m.group(1)); break
    if data.get("latitude") is not None and data.get("longitude") is not None:
        data["geom"]=f"POINT({data['longitude']} {data['latitude']})"
    return data

async def get(client,url,retries=5):
    delay=1
    for i in range(retries):
        try:
            r=await client.get(url)
            r.raise_for_status()
            return r.text
        except Exception:
            if i==retries-1: raise
            await asyncio.sleep(delay); delay=min(delay*2,16)

def save(sb,row):
    old=sb.table("market_listings").select("price_eur,status").eq("source",SOURCE).eq(
        "source_listing_id",row["source_listing_id"]).limit(1).execute().data
    changed=(not old or old[0].get("price_eur")!=row.get("price_eur") or old[0].get("status")!="active")
    sb.table("market_listings").upsert(row,on_conflict="source,source_listing_id").execute()
    if changed:
        sb.table("market_listing_history").insert({
          "source":SOURCE,"source_listing_id":row["source_listing_id"],
          "price_eur":row.get("price_eur"),"status":"active",
          "raw_data":{"url":row["url"],"title":row.get("title")}
        }).execute()
    return changed

async def run(pages,delay):
    load_dotenv()
    sb=create_client(os.environ["SUPABASE_URL"],os.environ["SUPABASE_SECRET_KEY"])
    started=now()
    sb.table("market_sync_state").upsert({"source":SOURCE,"status":"running",
      "last_started_at":started,"updated_at":started},on_conflict="source").execute()
    seen=set(); written=0
    headers={"User-Agent":"PLANA.CY market research collector/1.0","Accept-Language":"en-GB,en;q=0.9"}
    try:
        async with httpx.AsyncClient(timeout=45,follow_redirects=True,headers=headers) as client:
            for page in range(1,pages+1):
                search=f"{BASE}/properties-for-sale/sort-rl/page-{page}"
                html=await get(client,search)
                links=links_from_search(html)
                fresh=[u for u in links if listing_id(u) not in seen]
                print(f"page {page}: {len(links)} links, {len(fresh)} new")
                if not links:
                    print("No listing links found; stopping to avoid blind requests.")
                    break
                for u in fresh:
                    lid=listing_id(u); seen.add(lid)
                    detail=await get(client,u)
                    row=parse_detail(u,detail)
                    if row.get("source_listing_id"):
                        save(sb,row); written+=1
                    await asyncio.sleep(delay)
                await asyncio.sleep(delay)
        completed=now()
        sb.table("market_sync_state").upsert({"source":SOURCE,"status":"done",
          "pages_scanned":page,"listings_seen":len(seen),"listings_written":written,
          "last_error":None,"last_started_at":started,"last_completed_at":completed,
          "updated_at":completed},on_conflict="source").execute()
        print(f"BuySell complete: {len(seen):,} listings seen; {written:,} written.")
    except Exception as e:
        sb.table("market_sync_state").upsert({"source":SOURCE,"status":"error",
          "last_error":str(e)[:2000],"last_started_at":started,"updated_at":now()},
          on_conflict="source").execute()
        raise

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--source",choices=["buysell"],default="buysell")
    ap.add_argument("--pages",type=int,default=100)
    ap.add_argument("--delay",type=float,default=0.6)
    a=ap.parse_args()
    asyncio.run(run(max(1,a.pages),max(0.3,a.delay)))
if __name__=="__main__":main()
