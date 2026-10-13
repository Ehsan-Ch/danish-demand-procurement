"""Paper quantity orders, with deterministic caps and delayed reconciliation."""
from datetime import timedelta
import math
import sqlite3
from .data import stamp

class Desk:
    def __init__(self,path=":memory:",hourly_limit=4.0,daily_limit=120.0):
        if any(not math.isfinite(v) or v<=0 for v in (hourly_limit,daily_limit)):
            raise ValueError("Positive finite limits required")
        self.db=sqlite3.connect(path);self.db.row_factory=sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS orders (
        id TEXT PRIMARY KEY, area TEXT, issue TEXT, delivery TEXT,
        requested REAL, accepted REAL, status TEXT, reason TEXT,
        actual REAL, spot REAL, penalty REAL, procurement_cost REAL);
        CREATE TABLE IF NOT EXISTS events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT,time TEXT,status TEXT,note TEXT);
        """)
        self.hourly_limit=hourly_limit;self.daily_limit=daily_limit;self.kill_switch=False
    def clock(self,t):
        row=self.db.execute("SELECT time FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        if row and stamp(row[0])>t:raise ValueError("Backdated event")
    def event(self,oid,t,state,note):
        self.db.execute("INSERT INTO events(id,time,status,note) VALUES(?,?,?,?)",(oid,t.isoformat(),state,note))
    def submit(self,oid,area,issue,delivery,volume):
        issue,delivery=stamp(issue),stamp(delivery);self.clock(issue)
        if area not in ("DK1","DK2") or not math.isfinite(volume) or volume<0 or issue>=delivery:
            raise ValueError("Invalid order")
        if self.db.execute("SELECT 1 FROM orders WHERE area=? AND delivery=?",(area,delivery.isoformat())).fetchone():
            raise ValueError("Duplicate delivery exposure")
        used=self.db.execute("SELECT COALESCE(SUM(accepted),0) FROM orders WHERE substr(delivery,1,10)=?",(delivery.isoformat()[:10],)).fetchone()[0]
        accepted=0 if self.kill_switch else min(volume,self.hourly_limit,max(0,self.daily_limit-used))
        if accepted<1e-8:accepted=0.0
        reason="kill switch" if self.kill_switch else "volume cap" if accepted+1e-8<volume else "zero quantity" if volume==0 else ""
        with self.db:
            self.db.execute("INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,NULL,NULL,NULL,NULL)",
                (oid,area,issue.isoformat(),delivery.isoformat(),volume,accepted,"PROPOSED",reason))
            self.event(oid,issue,"PROPOSED","Demand-linked quantity request")
            if accepted==0:
                self.db.execute("UPDATE orders SET status='REJECTED' WHERE id=?",(oid,))
                self.event(oid,issue,"REJECTED",reason or "zero quantity")
            else:
                self.event(oid,issue,"APPROVED",reason or "Within quantity limits")
                self.db.execute("UPDATE orders SET status='OPEN' WHERE id=?",(oid,))
                self.event(oid,issue,"OPEN","Assumed price-taking paper nomination")
        return accepted
    def reconcile(self,oid,when,actual,spot,shortage=200,surplus=50):
        when=stamp(when);self.clock(when)
        o=self.db.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone()
        if o is None or o["status"] not in ("OPEN","REJECTED"):
            raise ValueError("Order already reconciled or missing")
        if when<stamp(o["delivery"])+timedelta(days=12,hours=1):
            raise ValueError("Demand not yet assumed available")
        if any(not math.isfinite(x) for x in (actual,spot,shortage,surplus)) or min(actual,shortage,surplus)<0:
            raise ValueError("Invalid reconciliation inputs")
        penalty=max(actual-o["accepted"],0)*shortage+max(o["accepted"]-actual,0)*surplus
        cost=actual*spot+penalty
        with self.db:
            # A rejected nomination stays rejected; its uncovered load still incurs cost.
            state="SETTLED" if o["status"]=="OPEN" else "REJECTED_RECONCILED"
            self.db.execute("UPDATE orders SET status=?,actual=?,spot=?,penalty=?,procurement_cost=? WHERE id=?",
                            (state,actual,spot,penalty,cost,oid))
            self.event(oid,when,state,"Load reconciled using assumed shortage/surplus charges")
        return penalty,cost
    def orders(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM orders ORDER BY delivery,area")]
    def events(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM events ORDER BY seq")]
    def close(self):self.db.close()

