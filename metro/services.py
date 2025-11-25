import networkx as nx
from decimal import Decimal
from .models import Station, Connection, MetroLine


import networkx as nx
from decimal import Decimal
from .models import Station, Connection, MetroLine

def build_graph(only_enabled: bool = False):
    G = nx.Graph()
    
    for station in Station.objects.all():
        G.add_node(station.id)

    qs = Connection.objects.select_related('line', 'from_station', 'to_station')
    if only_enabled:
        qs = qs.filter(line__is_enabled=True)

    for edge in qs:
        G.add_edge(
            edge.from_station.id,
            edge.to_station.id,
            line=edge.line.code
        )

    return G


def shortest_path_between_stations(source_station, dest_station, only_enabled=False):
    G = build_graph(only_enabled=only_enabled)
    try:
        path_ids = nx.shortest_path(G, source_station.id, dest_station.id)
        return path_ids
    except nx.NetworkXNoPath:
        return None



def calculate_price_from_path(path_ids, rate_per_edge=Decimal('5.00')):
    if not path_ids or len(path_ids) < 2:
        return Decimal('0.00')
    num_edges = len(path_ids) - 1
    return rate_per_edge * num_edges
