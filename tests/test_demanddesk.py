import json
import math
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
import numpy as np
from demanddesk.data import normalize,stamp,issue_for,available,load
from demanddesk.model import features,fit_area,interval_radius,order_quantile,score,Ridge
from demanddesk.book import Desk
from demanddesk.pipeline import simulate
from demanddesk.report import render

def series():
    start=stamp("2025-10-01")
    return {start+timedelta(hours=h):2000+250*math.sin(h/24)+100*math.sin(h/168) for h in range(8760)}

class DataTests(unittest.TestCase):
    def records(self):
        d={"records":[{"TimeUTC":"2026-01-01T00:00","PriceArea":"DK1","GrossCon":2000,"Version":"Preliminary"}]}
        p={"records":[{"TimeUTC":f"2026-01-01T00:{m:02d}","PriceArea":"DK1","DayAheadPriceEUR":v}
            for m,v in zip((0,15,30,45),(-40,0,40,80))]}
        return d,p
    def test_hourly_units_and_negative_prices(self):
        d,p=self.records();s,m,q=normalize(d,p)
        self.assertEqual(s["DK1"][stamp("2026-01-01")],2000)
        self.assertEqual(m["DK1"][stamp("2026-01-01")],20)
        self.assertEqual(q["versions"],{"Preliminary":1})
    def test_missing_quarter_not_filled(self):
        d,p=self.records();p["records"].pop()
        s,m,q=normalize(d,p)
        self.assertEqual(m["DK1"],{})
        self.assertEqual(q["counts"]["incomplete_price_hours"],1)
    def test_invalid_demand_excluded(self):
        d,p=self.records();d["records"][0]["GrossCon"]=-1
        s,m,q=normalize(d,p);self.assertEqual(s["DK1"],{})
    def test_duplicate_demand_rejected(self):
        d,p=self.records();d["records"]*=2
        with self.assertRaises(ValueError):normalize(d,p)
    def test_duplicate_price_rejected(self):
        d,p=self.records();p["records"].append(p["records"][0])
        with self.assertRaises(ValueError):normalize(d,p)
    def test_alignment_rejected(self):
        d,p=self.records();p["records"][0]["TimeUTC"]="2026-01-01T00:04"
        with self.assertRaises(ValueError):normalize(d,p)
    def test_timezone_offsets_keep_dst_hours_distinct(self):
        self.assertNotEqual(stamp("2026-10-25T02:00+01:00"),stamp("2026-10-25T02:00+02:00"))
    def test_cached_data_hash_checked(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/"demand.json").write_text("{}")
            (p/"manifest.json").write_text('{"demand":{"sha256":"wrong"}}')
            with self.assertRaises(ValueError):load(p)

class ForecastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.s=series()
    def test_horizons_share_previous_day_issue(self):
        first=stamp("2026-09-01");last=first+timedelta(hours=23)
        self.assertEqual(issue_for(first),issue_for(last))
        self.assertEqual(first-issue_for(first),timedelta(hours=15))
        self.assertEqual(last-issue_for(last),timedelta(hours=38))
    def test_exact_availability_boundary(self):
        t=stamp("2026-01-01")
        self.assertTrue(available(t,t+timedelta(days=12,hours=1)))
        self.assertFalse(available(t,t+timedelta(days=12)))
    def test_recent_unavailable_data_cannot_change_features(self):
        t=stamp("2026-09-01");x=features(self.s,t)[0];issue=issue_for(t)
        altered={s:(v if available(s,issue) else 1e9) for s,v in self.s.items()}
        np.testing.assert_array_equal(x,features(altered,t)[0])
    def test_missing_lag_excludes_sample(self):
        t=stamp("2026-09-01");s=dict(self.s);del s[t-timedelta(days=21)]
        self.assertIsNone(features(s,t))
    def test_split_label_maturity(self):
        self.assertTrue(available(stamp("2026-05-31T23:00"),issue_for(stamp("2026-06-15"))))
        self.assertTrue(available(stamp("2026-07-14T23:00"),issue_for(stamp("2026-07-28"))))
        self.assertTrue(available(stamp("2026-08-17T23:00"),issue_for(stamp("2026-09-01"))))
    def test_finite_sample_interval_rank(self):
        self.assertEqual(interval_radius(np.arange(1,20),0.9),18)
        with self.assertRaises(ValueError):interval_radius([1],0.99)
        with self.assertRaises(ValueError):interval_radius([float("nan")]*20)
    def test_signed_quantile_can_lower_plan(self):
        self.assertEqual(order_quantile([-5,-4,-3,-2,-1],0.5),-3)
        with self.assertRaises(ValueError):order_quantile([],0.8)
    def test_error_units(self):
        s=score([100,200],[110,180])
        self.assertEqual(s["mae_mwh"],15);self.assertEqual(s["bias_mwh"],-5)
        self.assertAlmostEqual(s["wape"],0.1)
    def test_ridge_scaling_is_frozen(self):
        x=np.arange(200).reshape(100,2);y=100+x[:,0]*2
        r=Ridge().fit(x,y,1);before=r.mean.copy();r.predict(x+1000)
        np.testing.assert_array_equal(before,r.mean)
        self.assertTrue((r.predict(x)>=0).all())
    def test_future_labels_cannot_change_selection_or_calibration(self):
        _,a=fit_area(self.s)
        changed={t:(v+5000 if t>=stamp("2026-09-01") else v) for t,v in self.s.items()}
        _,b=fit_area(changed)
        for key in ("selected","models","offsets_mwh","interval90_radius_mwh","validation"):
            self.assertEqual(a[key],b[key])
        self.assertNotEqual(a["test"],b["test"])

class DeskTests(unittest.TestCase):
    def setUp(self):
        self.d=Desk();self.addCleanup(self.d.close)
    def submit(self,oid="one",area="DK1",issue="2026-08-31T09:00",delivery="2026-09-01",volume=3):
        return self.d.submit(oid,area,issue,delivery,volume)
    def test_full_lifecycle_and_cost_identity(self):
        self.submit();penalty,cost=self.d.reconcile("one","2026-09-13T01:00",3.5,100)
        self.assertEqual(penalty,100);self.assertEqual(cost,450)
        self.assertEqual([e["status"] for e in self.d.events()],["PROPOSED","APPROVED","OPEN","SETTLED"])
    def test_hourly_cap(self):
        self.assertEqual(self.submit(volume=5),4)
        self.assertEqual(self.d.orders()[0]["reason"],"volume cap")
    def test_daily_cap_shared_across_zones(self):
        self.d.daily_limit=4
        self.submit()
        self.assertEqual(self.submit("two","DK2"),1)
    def test_daily_capacity_resets_for_new_delivery_day(self):
        self.d.daily_limit=3;self.submit()
        self.assertEqual(self.submit("two",delivery="2026-09-02"),3)
    def test_rejected_load_still_incurs_cost(self):
        self.d.kill_switch=True;self.assertEqual(self.submit(),0)
        p,c=self.d.reconcile("one","2026-09-13T01:00",3,100)
        self.assertEqual((p,c),(600,900))
        self.assertEqual(self.d.orders()[0]["status"],"REJECTED_RECONCILED")
    def test_negative_spot_cost_and_surplus(self):
        self.submit()
        p,c=self.d.reconcile("one","2026-09-13T01:00",2,-50)
        self.assertEqual((p,c),(50,-50))
    def test_early_and_repeated_reconciliation(self):
        self.submit()
        with self.assertRaises(ValueError):self.d.reconcile("one","2026-09-12T01:00",3,100)
        self.d.reconcile("one","2026-09-13T01:00",3,100)
        with self.assertRaises(ValueError):self.d.reconcile("one","2026-09-14",3,100)
    def test_duplicate_exposure_and_backdating(self):
        self.submit()
        with self.assertRaises(ValueError):self.submit("two")
        with self.assertRaises(ValueError):self.submit("three","DK2",issue="2026-08-30")
    def test_invalid_inputs_leave_no_order(self):
        with self.assertRaises(ValueError):self.submit(volume=float("nan"))
        self.assertEqual(self.d.orders(),[])
    def test_persisted_book(self):
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/"book.sqlite");d=Desk(path)
            d.submit("one","DK1","2026-08-31T09:00","2026-09-01",3);d.close()
            d=Desk(path)
            try:self.assertEqual(d.orders()[0]["status"],"OPEN")
            finally:d.close()

class SimulationTests(unittest.TestCase):
    def forecasts(self):
        start=stamp("2026-09-01")
        return [{"area":a,"delivery":(start+timedelta(hours=h)).isoformat(),
                 "issue":issue_for(start).isoformat(),"actual":2500,"spot":100,
                 "q50":3000,"q80":3000,"q90":3000} for h in range(24) for a in ("DK1","DK2")]
    def test_whole_day_caps_and_rejected_accounting(self):
        r=simulate(self.forecasts())
        self.assertEqual(r["accepted_mwh"],120)
        self.assertEqual(r["status_counts"],{"SETTLED":40,"REJECTED_RECONCILED":8})
        self.assertAlmostEqual(r["scenarios"][1]["penalty_eur"],5000)
        self.assertEqual(r["scenarios"][1]["daily"][0]["spot_cost"],12000)
        self.assertEqual(len(r["orders"]),48)
        times=[e["time"] for e in r["events"]];self.assertEqual(times,sorted(times))
    def test_penalty_sum_matches_order_ledger(self):
        r=simulate(self.forecasts())
        self.assertAlmostEqual(sum(o["penalty"] for o in r["orders"]),r["scenarios"][1]["penalty_eur"])
    def test_render_escapes_script_terminator(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"report.html";render(p,{"value":"</script>"})
            self.assertIn(r"\u003c/script>",p.read_text(encoding="utf-8"))

if __name__=="__main__":unittest.main()

