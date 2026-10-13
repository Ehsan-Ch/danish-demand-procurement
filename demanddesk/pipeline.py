from collections import Counter,defaultdict
from datetime import timedelta
from pathlib import Path
import csv
import json
import numpy as np
from .data import load,stamp,AREAS
from .model import fit_area
from .book import Desk

SHARE=0.001
POLICIES=("q50","q80","q90")
COSTS=((50,50),(200,50),(450,50))

def simulate(forecasts,policy="q80",path=":memory:"):
    if policy not in POLICIES:raise ValueError("Unknown policy")
    desk=Desk(path);actions=[]
    try:
        if desk.orders():raise ValueError("Use a fresh paper book")
        for r in forecasts:
            oid=r["area"]+"-"+r["delivery"]
            actions.append((stamp(r["issue"]),1,oid,r))
            actions.append((stamp(r["delivery"])+timedelta(days=12,hours=1),0,oid,r))
        for when,kind,oid,r in sorted(actions,key=lambda x:(x[0],x[1],x[2])):
            if kind:
                desk.submit(oid,r["area"],r["issue"],r["delivery"],r[policy]*SHARE)
            else:
                desk.reconcile(oid,when.isoformat(),r["actual"]*SHARE,r["spot"])
        orders,events=desk.orders(),desk.events()
    finally:
        desk.close()
    lookup={r["area"]+"-"+r["delivery"]:r for r in forecasts}
    scenarios=[]
    for shortage,surplus in COSTS:
        total=uncapped=short_mwh=long_mwh=0
        daily=defaultdict(lambda:{"penalty":0,"spot_cost":0,"hours":0})
        for o in orders:
            r=lookup[o["id"]];actual=r["actual"]*SHARE;q=o["accepted"];wanted=o["requested"]
            short=max(actual-q,0);long=max(q-actual,0)
            penalty=short*shortage+long*surplus
            total+=penalty;short_mwh+=short;long_mwh+=long
            uncapped+=max(actual-wanted,0)*shortage+max(wanted-actual,0)*surplus
            d=daily[o["delivery"][:10]]
            d["penalty"]+=penalty;d["spot_cost"]+=actual*r["spot"];d["hours"]+=1
        complete=[{"date":d,**v} for d,v in sorted(daily.items()) if v["hours"]==48]
        scenarios.append({"shortage":shortage,"surplus":surplus,
            "critical_quantile":shortage/(shortage+surplus),
            "penalty_eur":total,"uncapped_penalty_eur":uncapped,"cap_penalty_change_eur":total-uncapped,
            "short_mwh":short_mwh,"surplus_mwh":long_mwh,"daily":complete})
    return {"policy":policy,"orders":orders,"events":events,"scenarios":scenarios,
        "status_counts":dict(Counter(o["status"] for o in orders)),
        "capped_orders":sum(o["accepted"]+1e-8<o["requested"] for o in orders),
        "requested_mwh":sum(o["requested"] for o in orders),
        "accepted_mwh":sum(o["accepted"] for o in orders),
        "service_fraction":float(np.mean([o["actual"]<=o["accepted"] for o in orders]))}

def write_csv(path,rows):
    if not rows:path.write_text("",encoding="utf-8");return
    with path.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def build(series,market,source,quality,book_path=":memory:"):
    forecasts=[];areas={}
    for a in AREAS:
        rows,areas[a]=fit_area(series[a])
        for r in rows:
            t=stamp(r["delivery"])
            if t not in market[a]:raise ValueError("Test demand hour has no complete spot price")
            forecasts.append({"area":a,**r,"spot":market[a][t]})
    forecasts.sort(key=lambda r:(r["delivery"],r["area"]))
    # The published benchmark uses complete paired UTC days for comparable books.
    counts=Counter(r["delivery"][:10] for r in forecasts)
    if any(n!=48 for n in counts.values()) or len(counts)!=29:
        raise ValueError("Require all 29 complete paired test days")
    books=[simulate(forecasts,p,book_path if p=="q80" else ":memory:") for p in POLICIES]
    return {"meta":{"title":"Danish Demand & Procurement Desk","source":source,"quality":quality,
        "areas":areas,"share":SHARE,"hourly_limit_mwh":4,"daily_limit_mwh":120,
        "availability_days":12,"test_start":"2026-09-01","test_end_exclusive":"2026-09-30",
        "note":"Revised gross consumption, synthetic customer share and assumed shortage/surplus charges"},
        "forecasts":forecasts,"books":books}

def run(data_folder,out_folder):
    from .report import render
    series,market,quality,source=load(data_folder)
    out=Path(out_folder);out.mkdir(parents=True,exist_ok=True)
    db=out/"procurement.sqlite"
    if db.exists():raise ValueError("Choose a fresh output folder; existing book is preserved")
    result=build(series,market,source,quality,str(db))
    summary={**result["meta"],"policies":[{k:v for k,v in b.items() if k not in ("orders","events")} for b in result["books"]]}
    (out/"metrics.json").write_text(json.dumps(summary,indent=2,allow_nan=False),encoding="utf-8")
    write_csv(out/"forecasts.csv",result["forecasts"])
    for b in result["books"]:
        write_csv(out/(b["policy"]+"_orders.csv"),b["orders"])
    default=next(b for b in result["books"] if b["policy"]=="q80")
    write_csv(out/"q80_events.csv",default["events"])
    render(out/"report.html",result)
    return summary

