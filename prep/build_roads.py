import osmnx as ox

G = ox.graph_from_xml("data/atl_drive.osm", simplify=True, retain_all=False)
G = ox.add_edge_speeds(G)
G = ox.add_edge_travel_times(G)
ox.save_graphml(G, "data/atlanta_drive.graphml")
print("done:", len(G.nodes), "intersections,", len(G.edges), "road segments")
