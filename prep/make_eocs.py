import csv
import osmnx as ox

eocs = [
    ("Fulton", "Atlanta-Fulton County EMA", "130 Peachtree St SW, Atlanta, GA 30303"),
    ("DeKalb", "DeKalb Emergency Management Agency", "1950 West Exchange Place, Tucker, GA 30084"),
    ("Clayton", "Clayton County Office of Emergency Management", "1590 Adamson Parkway, Morrow, GA 30260"),
    ("Cobb", "Cobb County EMA", "140 North Marietta Parkway, Marietta, GA 30060"),
    ("Gwinnett", "Gwinnett County Office of Emergency Management", "800 Hi-Hope Rd, Lawrenceville, GA 30043"),
]

with open("data/eocs.csv", "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["county", "name", "address", "lat", "lon"])
    for county, name, addr in eocs:
        try:
            lat, lon = ox.geocode(addr)
        except Exception:
            lat, lon = "", ""
            print(f"Couldn't find {addr}. Fill its lat/lon in by hand.")
        w.writerow([county, name, addr, lat, lon])
        print(county, lat, lon)
