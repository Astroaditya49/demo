import ee, requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

ee.Initialize(project="shining-domain-513816-d7")

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


def landsat(start, end, region):
    """Landsat 8 + 9, badal/shadow hata ke, NDVI aur temperature ke liye ready."""
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
        region = ee.Geometry.Point([lon, lat]).buffer(radius_km * 1000).bounds()
        scenes = None

        if layer in ("ndvi", "lst"):
            col = landsat(start, end, region)
            scenes = col.size().getInfo()
            if scenes == 0:
                raise HTTPException(404, "Is date range me saaf Landsat image nahi mili. Date range badhao.")
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
            col = (ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY")
                   .filterDate(start, end).select("precipitation"))
            img = col.sum().rename("v")
            vis = {"min": 0, "max": 200, "palette": ["ffffff", "74add1", "08306b"]}
            scale = 5566
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