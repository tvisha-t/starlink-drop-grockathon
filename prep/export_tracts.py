"""Run once on your Mac: converts the CDC SVI geodatabase into two light files.

  data/tracts.csv      one row per Census tract: geoid, county, centroid, population, vulnerability
  data/tracts.geojson  simplified tract outlines for the map

Usage (from the starlink-drop folder, with the venv on):  python3 prep/export_tracts.py
"""
import geopandas as gpd

W, S, E, N = -84.65, 33.55, -84.05, 33.95          # same box as the tower data

gdf = gpd.read_file("data/SVI2022_GEORGIA_tract.gdb")
gdf = gdf.to_crs(4326)
gdf = gdf.cx[W:E, S:N]                              # tracts touching the metro box

# centroids computed in a metric projection (UTM 16N), then back to lat/lon
cent = gdf.to_crs(32616).geometry.centroid.to_crs(4326)
svi = gdf["RPL_THEMES"].where(gdf["RPL_THEMES"] >= 0)   # -999 means "no data"

out = gpd.GeoDataFrame({
    "geoid": gdf["FIPS"].astype(str),
    "county": gdf["COUNTY"].str.replace(" County", "", regex=False),
    "lat": cent.y.round(6),
    "lon": cent.x.round(6),
    "pop": gdf["E_TOTPOP"].clip(lower=0).astype(int),
    "svi": svi.round(4),
}, geometry=gdf.geometry)
out = out[(out.lon.between(W, E)) & (out.lat.between(S, N))]

out.drop(columns="geometry").to_csv("data/tracts.csv", index=False)
shapes = out[["geoid", "geometry"]].copy()
shapes["geometry"] = shapes.geometry.simplify(0.0005, preserve_topology=True)
shapes.to_file("data/tracts.geojson", driver="GeoJSON")

print(f"{len(out)} tracts, {out['pop'].sum():,} people, "
      f"{out['svi'].isna().sum()} tracts without a vulnerability score")
print("wrote data/tracts.csv and data/tracts.geojson")
