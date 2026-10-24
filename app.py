import os
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import core, datetime as dt

app = FastAPI(title="CampusWaste Intelligence")
@app.on_event("startup")
def _s(): core.init()

class Report(BaseModel):
    bin_id: int; fill_pct: float = Field(ge=0, le=100); source: str = Field("report", pattern="^(report|sim)$")
class NewBin(BaseModel):
    name: str = Field(min_length=1); location: str; lat: float = Field(ge=-90, le=90); lng: float = Field(ge=-180, le=180)
    capacity_l: float = Field(120, gt=0); base_rate: float = Field(4, gt=0)
class Sim(BaseModel):
    scenario: str = Field(pattern="^(lunch_surge|new_report|breakdown|reset)$")

def _need_bin(i):
    with core.conn() as c:
        if not c.execute("select 1 from bins where id=?", (i,)).fetchone(): raise HTTPException(404, "bin not found")

@app.get("/api/bins")
def bins(): return dict(now=core.now().isoformat(), sim=core.SIM, bins=core.statuses())
@app.post("/api/bins", status_code=201)
def add_bin(b: NewBin):
    with core.conn() as c:
        i = c.execute("insert into bins(name,location,lat,lng,capacity_l,base_rate) values(?,?,?,?,?,?)", (b.name, b.location, b.lat, b.lng, b.capacity_l, b.base_rate)).lastrowid
        c.execute("insert into obs(bin_id,ts,fill,source) values(?,?,?,'report')", (i, core.now().isoformat(), 0))
    return dict(id=i)
@app.post("/api/reports", status_code=201)
def report(r: Report):
    _need_bin(r.bin_id)
    with core.conn() as c: c.execute("insert into obs(bin_id,ts,fill,source) values(?,?,?,?)", (r.bin_id, core.now().isoformat(), r.fill_pct, r.source))
    return dict(ok=True)
@app.post("/api/collections", status_code=201)
def collect(r: dict):
    i = int(r.get("bin_id", -1)); _need_bin(i)
    with core.conn() as c: c.execute("insert into obs(bin_id,ts,fill,source) values(?,?,0,'collection')", (i, core.now().isoformat()))
    return dict(ok=True)
@app.get("/api/route")
def route(vehicles: int | None = None, capacity_l: float = 600): return core.route_plan(vehicles, capacity_l)
@app.get("/api/analytics")
def analytics(): return core.analytics()
@app.post("/api/classify")
async def classify(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > 8_000_000: raise HTTPException(413, "image too large")
    try: return core.classify(data)
    except Exception: raise HTTPException(400, "could not read image")
@app.post("/api/simulate")
def simulate(s: Sim):
    S = core.SIM
    if s.scenario == "reset": S.update(offset_h=0.0, surge=1.0, vehicles=3)
    elif s.scenario == "lunch_surge":
        t = dt.datetime.now().replace(hour=12, minute=30, second=0, microsecond=0)
        S.update(offset_h=(t - dt.datetime.now()).total_seconds() / 3600, surge=1.6)
    elif s.scenario == "breakdown": S["vehicles"] = max(1, S["vehicles"] - 1)
    else:
        st = max(core.statuses(), key=lambda b: -b["fill"] if b["fill"] > 85 else -999) if core.statuses() else None
        if st: report(Report(bin_id=st["id"], fill_pct=95, source="sim"))
    return dict(sim=S)

app.mount("/static", StaticFiles(directory="static"), name="static")
@app.get("/")
def index(): return FileResponse("static/index.html")
