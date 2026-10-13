from datetime import timedelta
import math
import numpy as np
from .data import issue_for,available,stamp

TRAIN_END=stamp("2026-06-01")
VALID_START,VALID_END=stamp("2026-06-15"),stamp("2026-07-15")
CAL_START,CAL_END=stamp("2026-07-28"),stamp("2026-08-18")
TEST_START,TEST_END=stamp("2026-09-01"),stamp("2026-09-30")

def features(series,t):
    issue=issue_for(t)
    times=[t-timedelta(days=d) for d in (14,21,28)]
    latest=issue-timedelta(days=12,hours=1)
    times+= [latest-timedelta(hours=h) for h in range(24)]
    if any(not available(s,issue) for s in times):
        raise ValueError("Unavailable demand feature")
    if any(s not in series for s in times):
        return None
    lag=np.array([series[s] for s in times[:3]])
    recent=np.array([series[s] for s in times[3:]])
    hour=t.hour;weekday=t.weekday()
    x=np.r_[lag,recent.mean(),recent.std(),recent[-1],recent[0],
            math.sin(2*math.pi*hour/24),math.cos(2*math.pi*hour/24),
            math.sin(4*math.pi*hour/24),math.cos(4*math.pi*hour/24),
            [float(weekday==d) for d in range(6)],
            math.sin(2*math.pi*t.timetuple().tm_yday/365.25),
            math.cos(2*math.pi*t.timetuple().tm_yday/365.25)]
    return x,lag[0],float(lag.mean()),latest

class Ridge:
    def fit(self,x,y,alpha):
        x=np.asarray(x,float);y=np.asarray(y,float)
        self.mean=x.mean(axis=0);self.scale=x.std(axis=0);self.scale[self.scale<1e-9]=1
        z=np.column_stack([np.ones(len(x)),(x-self.mean)/self.scale])
        penalty=np.eye(z.shape[1])*alpha;penalty[0,0]=0
        self.beta=np.linalg.solve(z.T@z+penalty,z.T@y)
        return self
    def predict(self,x):
        x=np.asarray(x,float)
        return np.maximum(0,np.column_stack([np.ones(len(x)),(x-self.mean)/self.scale])@self.beta)
    def export(self):
        return {"mean":self.mean.tolist(),"scale":self.scale.tolist(),"coefficients":self.beta.tolist()}

def score(y,p):
    y,p=np.asarray(y,float),np.asarray(p,float)
    err=p-y
    return {"n":len(y),"mae_mwh":float(np.mean(np.abs(err))),
            "rmse_mwh":float(np.sqrt(np.mean(err**2))),"bias_mwh":float(err.mean()),
            "wape":float(np.sum(np.abs(err))/np.sum(y)) if np.sum(y) else None}

def order_quantile(values,q):
    if not 0<q<1:
        raise ValueError("q must lie strictly between 0 and 1")
    values=np.asarray(values,float)
    if not len(values) or not np.isfinite(values).all():
        raise ValueError("Need finite calibration residuals")
    return float(np.quantile(values,q,method="inverted_cdf"))

def interval_radius(residuals,coverage=0.9):
    if not np.isfinite(np.asarray(residuals,float)).all():
        raise ValueError("Calibration residuals must be finite")
    n=len(residuals)
    if not n or not 0<coverage<1:
        raise ValueError("Invalid calibration sample")
    k=math.ceil((n+1)*coverage)
    if k>n:
        raise ValueError("Too few calibration residuals")
    return float(np.sort(np.abs(residuals))[k-1])

def fit_area(series):
    rows=[]
    for t,y in sorted(series.items()):
        f=features(series,t)
        if f is not None:
            rows.append((t,y,*f))
    groups=[[r for r in rows if condition(r[0])] for condition in (
        lambda t:t<TRAIN_END,lambda t:VALID_START<=t<VALID_END,
        lambda t:CAL_START<=t<CAL_END,lambda t:TEST_START<=t<TEST_END)]
    train,valid,cal,test=groups
    if min(map(len,groups))<100:
        raise ValueError("Insufficient samples in evaluation windows")
    def arrays(rs):return np.array([r[2] for r in rs]),np.array([r[1] for r in rs])
    xt,yt=arrays(train);xv,yv=arrays(valid)
    models={f"ridge_{alpha}":Ridge().fit(xt,yt,alpha) for alpha in (1,10,100)}
    def predict(name,rs):
        if name=="two_week":return np.array([r[3] for r in rs])
        if name=="three_week_mean":return np.array([r[4] for r in rs])
        return models[name].predict(np.array([r[2] for r in rs]))
    names=["two_week","three_week_mean",*models]
    validation={n:score(yv,predict(n,valid)) for n in names}
    selected=min(names,key=lambda n:validation[n]["mae_mwh"])
    xc,yc=arrays(cal);residuals=yc-predict(selected,cal)
    offsets={str(q):order_quantile(residuals,q) for q in (0.5,0.8,0.9)}
    radius=interval_radius(residuals)
    x,y=arrays(test);p=predict(selected,test)
    test_scores={n:score(y,predict(n,test)) for n in names}
    summary={"selected":selected,"training_n":len(train),"validation":validation,
        "calibration_n":len(cal),"test":test_scores,"offsets_mwh":offsets,
        "interval90_radius_mwh":radius,"interval90_coverage":float(np.mean((y>=np.maximum(0,p-radius))&(y<=p+radius))),
        "models":{n:m.export() for n,m in models.items()}}
    forecasts=[]
    for row,pred in zip(test,p):
        t,actual,_,two_week,mean,latest=row
        forecasts.append({"delivery":t.isoformat(),"issue":issue_for(t).isoformat(),
            "latest_demand_input":latest.isoformat(),"actual":actual,"point":float(pred),
            "two_week":float(two_week),"three_week_mean":float(mean),
            "lower90":float(max(0,pred-radius)),"upper90":float(pred+radius),
            "q50":float(max(0,pred+offsets["0.5"])),"q80":float(max(0,pred+offsets["0.8"])),
            "q90":float(max(0,pred+offsets["0.9"]))})
    return forecasts,summary

