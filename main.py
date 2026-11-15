import ee, requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

ee.Initialize(project="shining-domain-513816-d7")

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

LAYERS = {
    "ndvi": {"id": "MODIS/061/MOD13A2", "band": "NDVI", "scale": 0.0001, "offset": 0,
             "vis": {"min": 0, "max": 0.9, "palette": ["d73027", "fee08b", "1a9850"]}},
    "lst":  {"id": "MODIS/061/MOD11A2", "band": "LST_Day_1km", "scale": 0.02, "offset": -273.15,
             "vis": {"min": 15, "max": 45, "palette": ["313695", "ffffbf", "a50026"]}},
    "rain": {"id": "UCSB-CHG/CHIRPS/DAILY", "band": "precipitation", "sum": True,
             "vis": {"min": 0, "max": 200, "palette": ["ffffff", "74add1", "08306b"]}},
}

@app.get("/api/gee/map")
def gee_map(layer: str, start: str, end: str, lat: float, lon: float, radius_km: float = 25):
    cfg = LAYERS.get(layer)
    if not cfg:
        raise HTTPException(404, "Unknown layer")
    try:
        region = ee.Geometry.Point([lon, lat]).buffer(radius_km * 1000).bounds()
        col = ee.ImageCollection(cfg["id"]).filterDate(start, end).select(cfg["band"])
        img = col.sum() if cfg.get("sum") else col.mean()
        img = img.multiply(cfg.get("scale", 1)).add(cfg.get("offset", 0)).clip(region)
        m = img.getMapId(cfg["vis"])
        stats = img.reduceRegion(ee.Reducer.mean(), region, 1000, bestEffort=True).getInfo()
        return {"tile_url": m["tile_fetcher"].url_format,
                "mean": stats.get(cfg["band"]), "layer": layer}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/api/power")
def power(lat: float, lon: float, start: str, end: str, params: str = "T2M,PRECTOTCORR"):
    r = requests.get(
        "https://power.larc.nasa.gov/api/temporal/daily/point",
        params={"parameters": params, "community": "AG", "longitude": lon,
                "latitude": lat, "start": start.replace("-", ""),
                "end": end.replace("-", ""), "format": "JSON"},
        timeout=60)
    if r.status_code != 200:
        raise HTTPException(r.status_code, r.text[:300])
    data = r.json()["properties"]["parameter"]
    return {k: {d: (None if v == -999 else v) for d, v in vals.items()}
            for k, vals in data.items()}