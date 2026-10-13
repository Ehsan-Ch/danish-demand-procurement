from collections import Counter,defaultdict
from datetime import datetime,timezone,timedelta
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
import hashlib
import json
import math

BASE="https://api.energidataservice.dk/"
AREAS=("DK1","DK2")
SOURCES={"demand":("GenerationProdTypeExchange","TimeUTC,PriceArea,Version,GrossCon"),
         "prices":("DayAheadPrices","TimeUTC,PriceArea,DayAheadPriceEUR")}

def stamp(value):
    dt=datetime.fromisoformat(value.replace("Z","+00:00"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)

def download(folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    manifest={}
    for label,(dataset,columns) in SOURCES.items():
        url=BASE+"dataset/"+dataset+"?"+urlencode({"start":"2025-10-01","end":"2026-10-01",
            "filter":json.dumps({"PriceArea":list(AREAS)}),"columns":columns,"sort":"TimeUTC,PriceArea","limit":0})
        with urlopen(url,timeout=120) as r:
            raw=r.read()
        obj=json.loads(raw)
        if not obj.get("records") or obj.get("total",len(obj["records"]))!=len(obj["records"]):
            raise ValueError("Empty or truncated response")
        with urlopen(BASE+"meta/dataset/"+dataset,timeout=30) as r:
            metadata=json.load(r)
        manifest[label]={"url":url,"retrieved_utc":datetime.now(timezone.utc).isoformat(),
            "sha256":hashlib.sha256(raw).hexdigest(),"records":len(obj["records"]),"metadata":metadata}
        (folder/(label+".json")).write_bytes(raw)
    (folder/"manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    return manifest

def normalize(demand,prices):
    series={a:{} for a in AREAS};quarters=defaultdict(list);seen=set();versions=Counter();quality=Counter()
    for row in demand["records"]:
        area=row["PriceArea"]
        if area not in AREAS:
            continue
        t=stamp(row["TimeUTC"]);key=("demand",area,t)
        if key in seen:
            raise ValueError("Duplicate demand hour")
        seen.add(key)
        if t.minute or t.second or t.microsecond:
            raise ValueError("Demand must be hourly MWh")
        value=row["GrossCon"]
        if value is None or not math.isfinite(float(value)) or value<0:
            quality["invalid_demand_hours"]+=1
            continue
        series[area][t]=float(value);versions[row.get("Version","unspecified")]+=1
    for row in prices["records"]:
        area=row["PriceArea"]
        if area not in AREAS:
            continue
        t=stamp(row["TimeUTC"]);key=("price",area,t)
        if key in seen:
            raise ValueError("Duplicate price quarter")
        seen.add(key)
        if t.minute not in (0,15,30,45) or t.second or t.microsecond:
            raise ValueError("Prices must be aligned quarters")
        key=(area,t.replace(minute=0));quarters[key]
        value=row["DayAheadPriceEUR"]
        if value is None or not math.isfinite(float(value)):
            quality["invalid_price_quarters"]+=1
            continue
        quarters[key].append(float(value))
    market={a:{} for a in AREAS}
    for (a,t),values in quarters.items():
        if len(values)==4:
            market[a][t]=sum(values)/4
        else:
            quality["incomplete_price_hours"]+=1
    for a in AREAS:
        ts=sorted(series[a])
        quality[a+"_demand_hours"]=len(ts)
        quality[a+"_missing_demand_hours"]=sum(int((y-x).total_seconds()/3600)-1 for x,y in zip(ts,ts[1:]))
    return series,market,{"counts":dict(quality),"versions":dict(versions)}

def load(folder):
    folder=Path(folder);manifest=json.loads((folder/"manifest.json").read_text(encoding="utf-8"));payload={}
    for label in SOURCES:
        raw=(folder/(label+".json")).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=manifest[label]["sha256"]:
            raise ValueError("Cached source fingerprint mismatch")
        payload[label]=json.loads(raw)
    series,market,quality=normalize(payload["demand"],payload["prices"])
    return series,market,quality,manifest

def issue_for(target):
    return target.replace(hour=9)-timedelta(days=1)

def available(observation,issue):
    return observation+timedelta(hours=1,days=12)<=issue

