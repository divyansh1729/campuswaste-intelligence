# CampusWaste Intelligence (IIT Roorkee)
Sensor-free waste management: classifier, fill prediction, OR-Tools routing, live simulation, analytics.
Stack: FastAPI + SQLite + OR-Tools + Leaflet (single static page, no build step).

## Run
    pip install -r requirements.txt
    cp .env.example .env   # optional
    uvicorn app:app --reload      # http://localhost:8000
    pytest -q
## Deploy
    docker build -t campuswaste . && docker run -p 8000:8000 -v $PWD/data:/app/data -e DB_PATH=/app/data/campus.db campuswaste
Or push to Render/Railway (start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`).

## Add real locations
`POST /api/bins {"name","location","lat","lng","capacity_l","base_rate"}`, or edit `SEED_BINS` in `core.py`.

## Honesty notes
- Seed bins/histories are **synthetic demo data** (`source=demo-seed`); coordinates are approximate.
- Classifier: with no model configured, a colour heuristic runs, labelled UNVALIDATED, confidence fixed at 0.4, always "uncertain".
  For real classification: `pip install transformers torch` and set `CLASSIFIER_MODEL` to a waste image-classification model. Validate it on campus photos before claiming accuracy.
- Prediction: hourly/weekday demand profile x per-bin rate, scaled by learned corrections from real reports; confidence decays with observation age.
  `/api/analytics` compares against a persistence baseline, split into `measured` (user reports) and `simulated_demo_data`.
- Routing: haversine x1.3 distance, 15 km/h, 3 min/stop, 0.6 kg CO2/km (assumptions, editable). Baseline = visit every bin. All route savings are **simulated**, not measured.
- No auth, single-process in-memory simulation state. Not production hardened.
