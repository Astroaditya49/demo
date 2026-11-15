import ee, requests
from datetime import date
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

ee.Initialize(project="shining-domain-513816-d7")

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

SMAP_ID = "NASA/SMAP/SPL4SMGP/008"

# layer name -> (SMAP band, min, max)
SMAP = {
    "sm_surface":  ("sm_surface", 0.05, 0.50),
    "sm_rootzone": ("sm_rootzone", 0.05, 0.50),
    "sm_profile":  ("sm_profile", 0.05, 0.50),
    "sm_rootzone_pctl": ("sm_rootzone_pctl", 0, 100),
}
SM_PALETTE = ["a50026", "fdae61", "ffffbf", "abd9e9", "2c7bb6"]   # dry -> wet


def landsat(start, end, region):
    """Landsat 8 + 9, cloud/shadow masked, ready for NDVI and temperature."""
    def prep(img):
        qa = img.select("QA_PIXEL")
        clear = (qa.bitwiseAnd(1 << 1).eq(0)
                 .And(qa.bitwiseAnd(1 << 2).eq(0))
                 .And(qa.bitwiseAnd(1 << 3).eq(0))
                 .And(qa.bitwiseAnd(1 << 4).eq(0)))
        sr = img.select(["SR_B4", "SR_B5"]).multiply(0.0000275).add(-0.2)
        st = img.select("ST_B10").multiply(0.00341802).add(149.0).subtract(273.15)
        return sr.addBands(st).updateMask(clear)

    c8 = ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
    c9 = ee.ImageCollection("LANDSAT/LC09/C02/T1_L2")
    return c8.merge(c9).filterBounds(region).filterDate(start, end).map(prep)


@app.get("/api/gee/map")
def gee_map(layer: str, start: str, end: str, lat: float, lon: float, radius_km: float = 10):
    try:
        scenes = None

        if layer in ("ndvi", "lst"):
            region = ee.Geometry.Point([lon, lat]).buffer(radius_km * 1000).bounds()
            col = landsat(start, end, region)
            scenes = col.size().getInfo()
            if scenes == 0:
                raise HTTPException(404, "No clear Landsat images found for this date range. Try a longer range.")
            comp = col.median()
            scale = 30
            if layer == "ndvi":
                img = comp.normalizedDifference(["SR_B5", "SR_B4"]).rename("v")
                vis = {"min": 0, "max": 0.8,
                       "palette": ["d73027", "fee08b", "a6d96a", "1a9850"]}
            else:
                img = comp.select("ST_B10").rename("v")
                vis = {"min": 15, "max": 45,
                       "palette": ["313695", "ffffbf", "a50026"]}

        elif layer == "rain":
            region = ee.Geometry.Point([lon, lat]).buffer(radius_km * 1000).bounds()
            col = (ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
                   .filterDate(start, end).select("precipitation"))
            img = col.sum().rename("v")
            vis = {"min": 0, "max": 200, "palette": ["ffffff", "74add1", "08306b"]}
            scale = 5566

        elif layer in SMAP:
            band, vmin, vmax = SMAP[layer]
            region = ee.Geometry.Point([lon, lat]).buffer(60 * 1000).bounds()   # coarse data, bigger area
            col = ee.ImageCollection(SMAP_ID).filterDate(start, end).select(band)
            scenes = col.size().getInfo()
            if scenes == 0:
                raise HTTPException(404, "No SMAP soil moisture data for this date range. "
                                         "SMAP L4 can lag by a few days, so try an earlier end date.")
            img = col.mean().rename("v")
            vis = {"min": vmin, "max": vmax, "palette": SM_PALETTE}
            scale = 9000
        else:
            raise HTTPException(404, "Unknown layer")

        img = img.clip(region)
        m = img.getMapId(vis)
        stats = img.reduceRegion(ee.Reducer.mean(), region, scale,
                                 bestEffort=True, maxPixels=1e9).getInfo()
        return {"tile_url": m["tile_fetcher"].url_format, "mean": stats.get("v"),
                "layer": layer, "scenes": scenes, "resolution_m": scale}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/smap/timeseries")
def smap_timeseries(lat: float, lon: float, start: str, end: str):
    """Daily mean surface + root-zone soil moisture at a point (SMAP L4)."""
    try:
        d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
        n_days = (d1 - d0).days
        if n_days < 1:
            raise HTTPException(400, "End date must be after start date.")
        if n_days > 180:
            raise HTTPException(400, "Soil moisture chart supports up to 180 days. Shorten the range.")

        pt = ee.Geometry.Point([lon, lat])
        col = (ee.ImageCollection(SMAP_ID).filterDate(start, end)
               .select(["sm_surface", "sm_rootzone"]))

        def daily(i):
            day0 = ee.Date(start).advance(ee.Number(i), "day")
            sub = col.filterDate(day0, day0.advance(1, "day"))
            vals = ee.Dictionary(ee.Algorithms.If(
                sub.size().gt(0),
                sub.mean().reduceRegion(ee.Reducer.first(), pt, 9000),
                ee.Dictionary({"sm_surface": None, "sm_rootzone": None})))
            return ee.Feature(None, vals).set("date", day0.format("YYYY-MM-dd"))

        fc = ee.FeatureCollection(ee.List.sequence(0, n_days - 1).map(daily))
        rows = fc.getInfo()["features"]
        return {
            "dates": [r["properties"]["date"] for r in rows],
            "sm_surface": [r["properties"].get("sm_surface") for r in rows],
            "sm_rootzone": [r["properties"].get("sm_rootzone") for r in rows],
        }
    except HTTPException:
        raise
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