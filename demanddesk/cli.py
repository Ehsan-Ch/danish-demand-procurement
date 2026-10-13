import argparse
import json
from .data import download
from .pipeline import run

def main():
    p=argparse.ArgumentParser(description="Delayed-data demand forecasts and paper procurement")
    s=p.add_subparsers(dest="command",required=True)
    d=s.add_parser("download");d.add_argument("--data",default="data")
    r=s.add_parser("run");r.add_argument("--data",default="data");r.add_argument("--out",default="reports/run")
    args=p.parse_args()
    if args.command=="download":
        result=download(args.data);print({k:v["records"] for k,v in result.items()})
    else:
        result=run(args.data,args.out)
        print(json.dumps({"models":{a:{"selected":m["selected"],"test":m["test"][m["selected"]],
            "coverage90":m["interval90_coverage"]} for a,m in result["areas"].items()},
            "policies":[{"policy":b["policy"],"capped":b["capped_orders"],
                         "service_fraction":b["service_fraction"],
                         "penalty_eur":b["scenarios"][1]["penalty_eur"]} for b in result["policies"]]},indent=2))

if __name__=="__main__":main()

