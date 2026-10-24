import io, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ["DB_PATH"] = tempfile.mktemp(suffix=".db")
from fastapi.testclient import TestClient
from PIL import Image
import core, app as A
c = TestClient(A.app)

def test_bins_seeded_and_predicted():
    with c: d = c.get("/api/bins").json()
    assert len(d["bins"]) == 12 and all(0 <= b["confidence"] <= 1 for b in d["bins"])
    assert all(b["kind"] in ("observed", "predicted") for b in d["bins"])

def test_report_validation_and_observation():
    with c:
        assert c.post("/api/reports", json={"bin_id": 1, "fill_pct": 150}).status_code == 422
        assert c.post("/api/reports", json={"bin_id": 999, "fill_pct": 50}).status_code == 404
        assert c.post("/api/reports", json={"bin_id": 1, "fill_pct": 42}).status_code == 201
        b = [x for x in c.get("/api/bins").json()["bins"] if x["id"] == 1][0]
    assert b["kind"] == "observed" and abs(b["fill"] - 42) < 2

def test_route_capacity_and_baseline():
    with c: r = c.get("/api/route?vehicles=3&capacity_l=600").json()
    assert all(x["load_l"] <= 600 for x in r["smart"]["routes"])
    assert r["smart"]["km"] <= r["baseline_all_bins"]["km"] + 1e-6 and "SIMULATED" in r["label"]

def test_breakdown_and_reset():
    with c:
        assert c.post("/api/simulate", json={"scenario": "breakdown"}).json()["sim"]["vehicles"] == 2
        c.post("/api/simulate", json={"scenario": "reset"})
    assert core.SIM["vehicles"] == 3

def test_classifier_fallback_is_honest():
    buf = io.BytesIO(); Image.new("RGB", (64, 64), (30, 160, 40)).save(buf, "PNG")
    with c: r = c.post("/api/classify", files={"file": ("a.png", buf.getvalue(), "image/png")}).json()
    assert r["uncertain"] is True and "UNVALIDATED" in r["engine"]

def test_analytics_separates_sources():
    with c: a = c.get("/api/analytics").json()
    assert "simulated_demo_data" in a and "measured" in a
