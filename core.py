"""CampusWaste core: DB, fill prediction, routing, classifier fallback."""
import io, math, os, random, sqlite3, statistics, datetime as dt

DEPOT = dict(name="Campus Store (depot, demo)", lat=29.8652, lng=77.8970)
SIM = dict(offset_h=0.0, surge=1.0, vehicles=3)
_RAW = [.2,.1,.1,.1,.1,.2,.4,.8,1.2,1.0,.9,1.3,2.2,2.0,1.0,.9,1.0,1.2,1.5,1.8,1.6,1.0,.6,.3]
HOURW = [x / (sum(_RAW) / 24) for x in _RAW]  # mean 1.0

def conn():
    c = sqlite3.connect(os.environ.get("DB_PATH", "campus.db")); c.row_factory = sqlite3.Row; return c

def now():
    return dt.datetime.now().replace(microsecond=0) + dt.timedelta(hours=SIM["offset_h"])

def init():
    with conn() as c:
        c.executescript("""create table if not exists bins(id integer primary key autoincrement,name text,location text,
        lat real,lng real,capacity_l real default 120,base_rate real default 4,demo int default 0);
        create table if not exists obs(id integer primary key autoincrement,bin_id int,ts text,fill real,source text);""")
        if not c.execute("select count(*) from bins").fetchone()[0]:
            seed(c)

# DEMO DATA: coordinates approximate; fill histories are synthetic (source='demo-seed').
SEED_BINS = [("RB Mess Gate","Rajendra Bhawan",29.8641,77.8958,5.5),("RB Block A","Rajendra Bhawan",29.8644,77.8964,4.5),
 ("RB Canteen","Rajendra Bhawan",29.8638,77.8962,6.0),("RB Parking","Rajendra Bhawan",29.8636,77.8955,3.0),
 ("MAC Entrance","MAC",29.8663,77.8985,4.0),("MAC Food Court","MAC",29.8666,77.8989,6.5),
 ("MAC Library Side","MAC",29.8660,77.8990,3.0),("MAC Lawn","MAC",29.8668,77.8982,2.5),
 ("LHC Main Gate","LHC",29.8655,77.8975,5.0),("LHC Corridor","LHC",29.8657,77.8979,4.5),
 ("LHC Vending Area","LHC",29.8653,77.8978,5.5),("LHC Back Lane","LHC",29.8651,77.8981,2.5)]

def seed(c):
    rnd = random.Random(42); end = now(); start = end - dt.timedelta(days=14)
    for n, loc, la, lo, r in SEED_BINS:
        bid = c.execute("insert into bins(name,location,lat,lng,base_rate,demo) values(?,?,?,?,?,1)", (n, loc, la, lo, r)).lastrowid
        b = dict(location=loc, base_rate=r); mult = rnd.uniform(.8, 1.3); t, fill, nxt = start, 0.0, start
        while t < end:
            fill += rate(b, t) * mult * rnd.uniform(.85, 1.15) * .25; t += dt.timedelta(minutes=15)
            if t >= nxt:
                src = "demo-seed"
                if fill >= 90: fill = 0.0
                c.execute("insert into obs(bin_id,ts,fill,source) values(?,?,?,?)", (bid, t.isoformat(), max(0, min(110, fill + rnd.gauss(0, 3))), src))
                nxt = t + dt.timedelta(hours=rnd.uniform(4, 8))

def rate(b, t, surge=1.0):  # % fill per hour
    day = (1.2 if "Bhawan" in b["location"] else .7) if t.weekday() >= 5 else 1.0
    return b["base_rate"] * HOURW[t.hour] * day * surge

def growth(b, t0, t1, surge=1.0):
    g, t = 0.0, t0
    while t < t1:
        g += rate(b, t + dt.timedelta(minutes=7), surge) * min(.25, (t1 - t).total_seconds() / 3600); t += dt.timedelta(minutes=15)
    return g

def learned_scale(b, obs):
    r = []
    for p, q in zip(obs, obs[1:]):
        if q["source"] == "sim" or p["source"] == "sim" or q["fill"] <= p["fill"]: continue
        m = growth(b, dt.datetime.fromisoformat(p["ts"]), dt.datetime.fromisoformat(q["ts"]))
        if m > 1: r.append((q["fill"] - p["fill"]) / m)
    if not r: return 1.0
    return max(.5, min(2.0, (3 + len(r) * statistics.median(r)) / (3 + len(r))))

def predict(b, obs, at, surge=1.0):
    obs = [o for o in obs if o["ts"] <= at.isoformat()]
    if not obs: return None
    last = obs[-1]; t0 = dt.datetime.fromisoformat(last["ts"]); s = learned_scale(b, obs)
    fill = min(130.0, last["fill"] + growth(b, t0, at, surge) * s)
    age = max(0.0, (at - t0).total_seconds() / 3600)
    conf = round(max(.1, math.exp(-age / 10)) * min(1, .5 + .1 * len(obs)), 2)
    r = max(rate(b, at, surge) * s, .1)
    return dict(fill=round(fill, 1), kind="observed" if age < .25 else "predicted", last_fill=round(last["fill"], 1),
                last_ts=last["ts"], last_source=last["source"], age_h=round(age, 1), confidence=conf,
                hours_to_overflow=None if fill >= 100 else round((100 - fill) / r, 1), scale=round(s, 2))

def statuses():
    out = []; at = now()
    with conn() as c:
        for b in c.execute("select * from bins").fetchall():
            b = dict(b); o = [dict(x) for x in c.execute("select * from obs where bin_id=? order by ts", (b["id"],))]
            p = predict(b, o, at, SIM["surge"] if SIM["surge"] != 1 else 1.0)
            if p: b.update(p); out.append(b)
    return out

def hav(a, b):
    p = math.pi / 180; x = math.sin((b["lat"] - a["lat"]) * p / 2) ** 2 + math.cos(a["lat"] * p) * math.cos(b["lat"] * p) * math.sin((b["lng"] - a["lng"]) * p / 2) ** 2
    return 12742 * math.asin(math.sqrt(x))

def needs_service(b, thr=60, hrs=4):
    return b["fill"] >= thr or (b["hours_to_overflow"] is not None and b["hours_to_overflow"] <= hrs)

def plan(bins, vehicles, cap_l):
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2 as E
    if not bins or vehicles < 1: return dict(routes=[], km=0, minutes=0, dropped=[b["id"] for b in bins])
    nodes = [DEPOT] + bins
    M = [[int(hav(a, b) * 1300) for b in nodes] for a in nodes]  # metres, x1.3 road factor (assumption)
    m = pywrapcp.RoutingIndexManager(len(nodes), vehicles, 0); r = pywrapcp.RoutingModel(m)
    r.SetArcCostEvaluatorOfAllVehicles(r.RegisterTransitCallback(lambda i, j: M[m.IndexToNode(i)][m.IndexToNode(j)]))
    load = lambda n: 0 if n == 0 else max(1, int(min(100, nodes[n]["fill"]) / 100 * nodes[n]["capacity_l"]))
    r.AddDimensionWithVehicleCapacity(r.RegisterUnaryTransitCallback(lambda i: load(m.IndexToNode(i))), 0, [int(cap_l)] * vehicles, True, "cap")
    for k, b in enumerate(bins, 1): r.AddDisjunction([m.NodeToIndex(k)], int(20000 + 1000 * min(130, b["fill"])))
    sp = pywrapcp.DefaultRoutingSearchParameters()
    sp.first_solution_strategy = E.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    sp.local_search_metaheuristic = E.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH; sp.time_limit.seconds = 1
    sol = r.SolveWithParameters(sp); routes, km, mins, served = [], 0.0, 0.0, set()
    if not sol: return dict(routes=[], km=0, minutes=0, dropped=[b["id"] for b in bins])
    for v in range(vehicles):
        i, path, d = r.Start(v), [0], 0
        while not r.IsEnd(i):
            j = sol.Value(r.NextVar(i)); d += M[m.IndexToNode(i)][m.IndexToNode(j)]; i = j; path.append(m.IndexToNode(i))
        if len(path) > 2:
            stops = [nodes[n] for n in path[1:-1]]; served |= {s["id"] for s in stops}
            routes.append(dict(vehicle=v + 1, km=round(d / 1000, 2), stops=[dict(id=s["id"], name=s["name"], fill=s["fill"]) for s in stops],
                               path=[[nodes[n]["lat"], nodes[n]["lng"]] for n in path], load_l=sum(load(n) for n in path)))
            km += d / 1000; mins += d / 1000 / 15 * 60 + 3 * len(stops)
    return dict(routes=routes, km=round(km, 2), minutes=round(mins), dropped=[b["id"] for b in bins if b["id"] not in served])

def route_plan(vehicles=None, cap_l=600):
    st = statuses(); vehicles = SIM["vehicles"] if vehicles is None else vehicles
    need = [b for b in st if needs_service(b)]; smart = plan(need, vehicles, cap_l); base = plan(st, vehicles, cap_l)
    co2 = float(os.environ.get("CO2_KG_PER_KM", .6))
    for p in (smart, base): p["co2_kg"] = round(p["km"] * co2, 2); p["bins"] = len(p.get("routes") and [s for r in p["routes"] for s in r["stops"]] or [])
    return dict(smart=smart, baseline_all_bins=base, vehicles=vehicles, needed=[b["id"] for b in need],
                km_saved=round(base["km"] - smart["km"], 2), co2_saved_kg=round(base["co2_kg"] - smart["co2_kg"], 2),
                label="SIMULATED: haversine x1.3 road factor, 15 km/h, 3 min/stop, demo fill data. Not measured.")

def analytics():
    res = {}
    with conn() as c:
        for b in c.execute("select * from bins").fetchall():
            b = dict(b); o = [dict(x) for x in c.execute("select * from obs where bin_id=? order by ts", (b["id"],))]
            for i in range(1, len(o)):
                if o[i]["fill"] < o[i - 1]["fill"] - 5 or o[i]["source"] not in ("report", "demo-seed"): continue
                p = predict(b, o[:i], dt.datetime.fromisoformat(o[i]["ts"]))
                k = "measured" if o[i]["source"] == "report" else "simulated_demo_data"
                res.setdefault(k, []).append((abs(p["fill"] - o[i]["fill"]), abs(o[i - 1]["fill"] - o[i]["fill"])))
    f = lambda v: dict(n=len(v), mae_model=round(sum(a for a, _ in v) / len(v), 1), mae_persistence_baseline=round(sum(b for _, b in v) / len(v), 1))
    out = {k: f(v) for k, v in res.items()}
    if "measured" not in out: out["measured"] = dict(n=0, note="No user-submitted reports yet. Submit reports to get measured accuracy.")
    return out

BINMAP = {"plastic": "Blue bin (dry recyclables)", "paper": "Blue bin (dry recyclables)", "metal": "Blue bin (dry recyclables)",
          "glass": "Blue bin (dry recyclables)", "organic": "Green bin (wet / compost)", "e-waste": "E-waste collection point (not a bin)",
          "other": "Black bin (general)"}
KEYS = {"plastic": ["plastic", "bottle"], "paper": ["paper", "cardboard", "carton"], "metal": ["metal", "can", "tin"], "glass": ["glass"],
        "organic": ["organic", "food", "biological", "compost"], "e-waste": ["battery", "electronic", "e-waste", "phone"]}

def classify(data: bytes):
    thr = float(os.environ.get("CONF_THRESHOLD", .6)); model = os.environ.get("CLASSIFIER_MODEL"); from PIL import Image
    img = Image.open(io.BytesIO(data)).convert("RGB")
    if model:
        try:
            from transformers import pipeline
            top = pipeline("image-classification", model=model)(img)[0]
            lab = next((k for k, w in KEYS.items() if any(x in top["label"].lower() for x in w)), "other"); cf = round(float(top["score"]), 2)
            return dict(label=lab, raw_label=top["label"], confidence=cf, bin=BINMAP[lab], uncertain=cf < thr, engine=model)
        except Exception as e:
            err = f"pretrained model failed ({type(e).__name__}); used heuristic"
    else: err = "no pretrained model configured; used colour heuristic"
    import colorsys
    px = list(img.resize((32, 32)).getdata()); n = len(px); r, g, b = (sum(p[i] for p in px) / n for i in range(3))
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    lab = "organic" if (.15 < h < .45 and s > .25) or v < .3 else "paper" if s < .2 and v > .7 else "metal" if s < .2 else "plastic"
    return dict(label=lab, confidence=.4, bin=BINMAP[lab], uncertain=True, engine="color-heuristic (UNVALIDATED, demo fallback)",
                note=err + ". Always verify manually; see README to enable a pretrained model.")
