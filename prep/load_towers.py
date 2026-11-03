import pandas as pd

cols = ["radio", "mcc", "net", "area", "cell", "unit", "lon", "lat",
        "range", "samples", "changeable", "created", "updated", "avg_signal"]
W, S, E, N = -84.65, 33.55, -84.05, 33.95   # same box as the road network

parts = []
for f in ["data/towers_raw/310.csv.gz", "data/towers_raw/311.csv.gz"]:
    for chunk in pd.read_csv(f, header=None, names=cols,
                             usecols=["radio", "lon", "lat", "range", "samples"],
                             chunksize=500_000):
        inside = chunk.lon.between(W, E) & chunk.lat.between(S, N)
        parts.append(chunk[inside])

cells = pd.concat(parts, ignore_index=True)
print(f"{len(cells):,} cells inside metro Atlanta")

# Several cells (antennas) share one tower, so merge cells within ~100 m
cells["gx"] = (cells.lon / 0.0011).round().astype(int)
cells["gy"] = (cells.lat / 0.0009).round().astype(int)
towers = (cells.groupby(["gx", "gy"])
          .agg(lon=("lon", "mean"), lat=("lat", "mean"),
               n_cells=("lon", "size"),
               radios=("radio", lambda r: ",".join(sorted(set(r)))),
               max_range_m=("range", "max"))
          .reset_index(drop=True))
towers.insert(0, "tower_id", [f"t_{i}" for i in range(len(towers))])
towers.to_csv("data/towers.csv", index=False)
print(f"{len(towers):,} towers saved to data/towers.csv")
