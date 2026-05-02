import math  # L001
import time  # L002
from typing import Dict, List, Tuple, Any  # L003

import requests  # L004
import pandas as pd  # L005
import streamlit as st  # L006
import folium  # L007
from streamlit_folium import st_folium  # L008
from shapely.geometry import LineString, Point  # L009


# ------------------------------------------------------------  # L010
# App-Konfiguration  # L011
# ------------------------------------------------------------  # L012
st.set_page_config(page_title="Tankroute", page_icon="⛽", layout="wide")  # L013

TANKERKOENIG_API_KEY = st.secrets["TANKERKOENIG_API_KEY"]  # L014
ORS_API_KEY = st.secrets["ORS_API_KEY"]  # L015

TANKERKOENIG_LIST_URL = "https://creativecommons.tankerkoenig.de/json/list.php"  # L016
ORS_GEOCODE_URL = "https://api.openrouteservice.org/geocode/search"  # L017
ORS_DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/driving-car/geojson"  # L018


# ------------------------------------------------------------  # L019
# Hilfsfunktionen für Entfernungen und Koordinaten  # L020
# ------------------------------------------------------------  # L021
def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:  # L022
    """Berechnet die Luftlinienentfernung zwischen zwei Punkten in Kilometern."""  # L023
    earth_radius_km = 6371.0  # L024
    d_lat = math.radians(lat2 - lat1)  # L025
    d_lon = math.radians(lon2 - lon1)  # L026
    a = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2  # L027
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))  # L028
    return earth_radius_km * c  # L029


def latlon_to_lonlat(lat: float, lon: float) -> List[float]:  # L030
    """Wandelt Koordinaten von lat/lon nach lon/lat um, weil ORS lon/lat erwartet."""  # L031
    return [lon, lat]  # L032


def lonlat_to_latlon(coord: List[float]) -> Tuple[float, float]:  # L033
    """Wandelt Koordinaten von lon/lat nach lat/lon um, weil Karten meist lat/lon nutzen."""  # L034
    return coord[1], coord[0]  # L035


# ------------------------------------------------------------  # L036
# OpenRouteService: Adresse -> Koordinaten  # L037
# ------------------------------------------------------------  # L038
@st.cache_data(ttl=3600)  # L039
def geocode_address(address: str) -> Tuple[float, float, str]:  # L040
    """Sucht eine Adresse über OpenRouteService und gibt lat/lon zurück."""  # L041
    params = {"api_key": ORS_API_KEY, "text": address, "boundary.country": "DE", "size": 1}  # L042
    response = requests.get(ORS_GEOCODE_URL, params=params, timeout=20)  # L043
    response.raise_for_status()  # L044
    data = response.json()  # L045
    features = data.get("features", [])  # L046
    if not features:  # L047
        raise ValueError(f"Adresse nicht gefunden: {address}")  # L048
    feature = features[0]  # L049
    lon, lat = feature["geometry"]["coordinates"]  # L050
    label = feature["properties"].get("label", address)  # L051
    return lat, lon, label  # L052


# ------------------------------------------------------------  # L053
# OpenRouteService: Route berechnen  # L054
# ------------------------------------------------------------  # L055
@st.cache_data(ttl=900)  # L056
def get_route(start_lat: float, start_lon: float, end_lat: float, end_lon: float) -> Dict[str, Any]:  # L057
    """Berechnet die Route und gibt Geometrie, Fahrzeit und Distanz zurück."""  # L058
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}  # L059
    body = {"coordinates": [latlon_to_lonlat(start_lat, start_lon), latlon_to_lonlat(end_lat, end_lon)]}  # L060
    response = requests.post(ORS_DIRECTIONS_URL, headers=headers, json=body, timeout=30)  # L061
    response.raise_for_status()  # L062
    data = response.json()  # L063
    feature = data["features"][0]  # L064
    summary = feature["properties"]["summary"]  # L065
    coords_lonlat = feature["geometry"]["coordinates"]  # L066
    coords_latlon = [lonlat_to_latlon(coord) for coord in coords_lonlat]  # L067
    return {"coords_lonlat": coords_lonlat, "coords_latlon": coords_latlon, "duration_min": summary["duration"] / 60, "distance_km": summary["distance"] / 1000}  # L068


# ------------------------------------------------------------  # L069
# Tankerkönig: Tankstellen im Umkreis abrufen  # L070
# ------------------------------------------------------------  # L071
@st.cache_data(ttl=300)  # L072
def get_stations_near(lat: float, lon: float, radius_km: float, fuel_type: str) -> List[Dict[str, Any]]:  # L073
    """Ruft Tankstellen im Radius von Tankerkönig ab."""  # L074
    params = {"lat": lat, "lng": lon, "rad": radius_km, "sort": "dist", "type": fuel_type, "apikey": TANKERKOENIG_API_KEY}  # L075
    response = requests.get(TANKERKOENIG_LIST_URL, params=params, timeout=20)  # L076
    response.raise_for_status()  # L077
    data = response.json()  # L078
    if not data.get("ok"):  # L079
        raise ValueError(data.get("message", "Tankerkönig-Abfrage fehlgeschlagen"))  # L080
    return data.get("stations", [])  # L081


# ------------------------------------------------------------  # L082
# Route in Suchpunkte zerlegen  # L083
# ------------------------------------------------------------  # L084
def pick_route_search_points(route_coords_latlon: List[Tuple[float, float]], max_points: int = 3) -> List[Tuple[float, float]]:  # L085
    """Wählt wenige Punkte entlang der Route für Tankerkönig-Umkreissuchen aus."""  # L086
    if len(route_coords_latlon) <= max_points:  # L087
        return route_coords_latlon  # L088
    indexes = [round(i * (len(route_coords_latlon) - 1) / (max_points + 1)) for i in range(1, max_points + 1)]  # L089
    return [route_coords_latlon[index] for index in indexes]  # L090


# ------------------------------------------------------------  # L091
# Duplikate entfernen  # L092
# ------------------------------------------------------------  # L093
def deduplicate_stations(stations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:  # L094
    """Entfernt doppelte Tankstellen anhand der Tankerkönig-ID."""  # L095
    seen_ids = set()  # L096
    unique_stations = []  # L097
    for station in stations:  # L098
        station_id = station.get("id")  # L099
        if station_id and station_id not in seen_ids:  # L100
            seen_ids.add(station_id)  # L101
            unique_stations.append(station)  # L102
    return unique_stations  # L103


# ------------------------------------------------------------  # L104
# Lokale Korridorprüfung: Liegt Tankstelle nah genug an der Route?  # L105
# ------------------------------------------------------------  # L106
def filter_stations_by_corridor(stations: List[Dict[str, Any]], route_coords_lonlat: List[List[float]], corridor_km: float) -> List[Dict[str, Any]]:  # L107
    """Filtert Tankstellen, die nahe genug an der Route liegen."""  # L108
    route_line = LineString(route_coords_lonlat)  # L109
    filtered = []  # L110
    for station in stations:  # L111
        station_point = Point(station["lng"], station["lat"])  # L112
        distance_degrees = route_line.distance(station_point)  # L113
        distance_km_rough = distance_degrees * 111  # L114
        if distance_km_rough <= corridor_km:  # L115
            station["corridor_distance_km"] = distance_km_rough  # L116
            filtered.append(station)  # L117
    return filtered  # L118


# ------------------------------------------------------------  # L119
# Umweg-Fahrzeit pro Tankstelle berechnen  # L120
# ------------------------------------------------------------  # L121
@st.cache_data(ttl=900)  # L122
def get_route_via_station(start_lat: float, start_lon: float, station_lat: float, station_lon: float, end_lat: float, end_lon: float) -> Dict[str, float]:  # L123
    """Berechnet die Fahrtzeit von Start über Tankstelle zum Ziel."""  # L124
    headers = {"Authorization": ORS_API_KEY, "Content-Type": "application/json"}  # L125
    body = {"coordinates": [latlon_to_lonlat(start_lat, start_lon), latlon_to_lonlat(station_lat, station_lon), latlon_to_lonlat(end_lat, end_lon)]}  # L126
    response = requests.post(ORS_DIRECTIONS_URL, headers=headers, json=body, timeout=30)  # L127
    response.raise_for_status()  # L128
    data = response.json()  # L129
    summary = data["features"][0]["properties"]["summary"]  # L130
    return {"duration_min": summary["duration"] / 60, "distance_km": summary["distance"] / 1000}  # L131


# ------------------------------------------------------------  # L132
# Score berechnen: Geldersparnis gegen Zeitverlust abwägen  # L133
# ------------------------------------------------------------  # L134
def score_station(station: Dict[str, Any], reference_price: float, tank_liters: float, direct_duration_min: float, via_duration_min: float, money_weight: float) -> Dict[str, Any]:  # L135
    """Berechnet Ersparnis, Zusatzzeit und Gesamtscore für eine Tankstelle."""  # L136
    station_price = float(station["price"])  # L137
    saving_eur = max(0, reference_price - station_price) * tank_liters  # L138
    extra_time_min = max(0, via_duration_min - direct_duration_min)  # L139
    time_weight = 1 - money_weight  # L140
    score = money_weight * saving_eur - time_weight * extra_time_min  # L141
    station["saving_eur"] = saving_eur  # L142
    station["extra_time_min"] = extra_time_min  # L143
    station["total_duration_min"] = via_duration_min  # L144
    station["score"] = score  # L145
    return station  # L146


# ------------------------------------------------------------  # L147
# Karte erstellen  # L148
# ------------------------------------------------------------  # L149
def build_map(route_coords_latlon: List[Tuple[float, float]], top_stations: List[Dict[str, Any]]) -> folium.Map:  # L150
    """Erstellt eine Karte mit Route und Top-Tankstellen."""  # L151
    center_lat = sum(point[0] for point in route_coords_latlon) / len(route_coords_latlon)  # L152
    center_lon = sum(point[1] for point in route_coords_latlon) / len(route_coords_latlon)  # L153
    route_map = folium.Map(location=[center_lat, center_lon], zoom_start=11)  # L154
    folium.PolyLine(route_coords_latlon, tooltip="Direktroute").add_to(route_map)  # L155
    for index, station in enumerate(top_stations, start=1):  # L156
        popup = f"{index}. {station.get('name', 'Tankstelle')}<br>{station.get('brand', '')}<br>{station['price']:.3f} €/l<br>+{station['extra_time_min']:.1f} min"  # L157
        folium.Marker(location=[station["lat"], station["lng"]], popup=popup, tooltip=f"{index}. {station.get('brand', station.get('name', 'Tankstelle'))}").add_to(route_map)  # L158
    return route_map  # L159


# ------------------------------------------------------------  # L160
# Streamlit-Oberfläche  # L161
# ------------------------------------------------------------  # L162
st.title("⛽ Tankroute")  # L163
st.write("Findet günstige Tankstellen auf deinem Pendelweg und bewertet Preisersparnis gegen Zusatzzeit.")  # L164

if "last_results" not in st.session_state:  # L164a
    st.session_state["last_results"] = None  # L164b

with st.sidebar:  # L165
    st.header("Eingaben")  # L166
    start_address = st.text_input("Startadresse", "Karlsruhe Gutenbergplatz")  # L167
    end_address = st.text_input("Zieladresse", "Gasometer Pforzheim")  # L168
    direction = st.radio("Richtung", ["Hinweg", "Rückweg"])  # L169
    fuel_type = st.selectbox("Kraftstoff", ["diesel", "e5", "e10"], index=0)  # L170
    tank_liters = st.number_input("Tankmenge in Litern", min_value=5.0, max_value=120.0, value=60.0, step=5.0)  # L171
    start_end_radius_km = st.slider("Radius um Start/Ziel in km", min_value=1.0, max_value=10.0, value=4.0, step=0.5)  # L172
    corridor_km = st.slider("Routenkorridor in km", min_value=1.0, max_value=5.0, value=3.0, step=0.5)  # L173
    max_extra_time_min = st.slider("Maximaler Umweg in Minuten", min_value=1, max_value=30, value=10, step=1)  # L174
    money_weight_percent = st.slider("Gewichtung Geldersparnis", min_value=0, max_value=100, value=70, step=5)  # L175
    search_clicked = st.button("Tankoptionen suchen")  # L176


# ------------------------------------------------------------  # L177
# Hauptlogik ausführen, wenn Button geklickt wurde  # L178
# ------------------------------------------------------------  # L179
if search_clicked:  # L180
    try:  # L181
        with st.spinner("Berechne Route und suche Tankstellen..."):  # L182
            if direction == "Rückweg":  # L183
                start_address, end_address = end_address, start_address  # L184

            start_lat, start_lon, start_label = geocode_address(start_address)  # L185
            end_lat, end_lon, end_label = geocode_address(end_address)  # L186
            route = get_route(start_lat, start_lon, end_lat, end_lon)  # L187

            start_stations = get_stations_near(start_lat, start_lon, start_end_radius_km, fuel_type)  # L188
            end_stations = get_stations_near(end_lat, end_lon, start_end_radius_km, fuel_type)  # L189

            route_search_points = pick_route_search_points(route["coords_latlon"], max_points=3)  # L190
            route_stations = []  # L191
            for point_lat, point_lon in route_search_points:  # L192
                route_stations.extend(get_stations_near(point_lat, point_lon, 25, fuel_type))  # L193

            all_stations = deduplicate_stations(start_stations + end_stations + route_stations)  # L194
            open_stations = [station for station in all_stations if station.get("isOpen") and station.get("price")]  # L195
            corridor_stations = filter_stations_by_corridor(open_stations, route["coords_lonlat"], corridor_km)  # L196

            reference_candidates = [station for station in start_stations + end_stations if station.get("isOpen") and station.get("price")]  # L197
            if not reference_candidates:  # L198
                raise ValueError("Keine offene Referenz-Tankstelle nahe Start oder Ziel gefunden.")  # L199
            reference_price = min(float(station["price"]) for station in reference_candidates)  # L200

            candidate_stations = sorted(corridor_stations, key=lambda station: float(station["price"]))[:30]  # L201
            scored_stations = []  # L202

            for station in candidate_stations:  # L203
                via_route = get_route_via_station(start_lat, start_lon, station["lat"], station["lng"], end_lat, end_lon)  # L204
                scored_station = score_station(station, reference_price, tank_liters, route["duration_min"], via_route["duration_min"], money_weight_percent / 100)  # L205
                if scored_station["extra_time_min"] <= max_extra_time_min:  # L206
                    scored_stations.append(scored_station)  # L207

            top_stations = sorted(scored_stations, key=lambda station: station["score"], reverse=True)[:3]  # L208

            st.session_state["last_results"] = {  # L208a
                "route": route,  # L208b
                "top_stations": top_stations,  # L208c
                "reference_price": reference_price,  # L208d
                "start_label": start_label,  # L208e
                "end_label": end_label,  # L208f
            }  # L208g
        
        results = st.session_state["last_results"]  # L209
        route = results["route"]  # L210
        top_stations = results["top_stations"]  # L211
        reference_price = results["reference_price"]  # L212
        start_label = results["start_label"]  # L213
        end_label = results["end_label"]  # L214

        st.subheader("Route")  # L215
        st.write(f"**Start:** {start_label}")  # L216
        st.write(f"**Ziel:** {end_label}")  # L217
        st.write(f"**Direkte Fahrzeit:** {route['duration_min']:.1f} Minuten")  # L218
        st.write(f"**Direkte Distanz:** {route['distance_km']:.1f} km")  # L219
        st.write(f"**Referenzpreis nahe Start/Ziel:** {reference_price:.3f} €/l")  # L220

        if not top_stations:  # L221
            st.warning("Keine passende Tankstelle innerhalb des maximalen Umwegs gefunden.")  # L222
        else:  # L223
            st.subheader("Beste 3 Tankoptionen")  # L224
            table_rows = []  # L225
            for index, station in enumerate(top_stations, start=1):  # L226
                table_rows.append({"Rang": index, "Name": station.get("name"), "Marke": station.get("brand"), "Ort": station.get("place"), "Preis €/l": station["price"], "Ersparnis €": round(station["saving_eur"], 2), "Zusatzzeit min": round(station["extra_time_min"], 1), "Gesamtfahrtzeit min": round(station["total_duration_min"], 1), "Score": round(station["score"], 2)})  # L227
            st.dataframe(pd.DataFrame(table_rows), use_container_width=True)  # L228

            route_map = build_map(route["coords_latlon"], top_stations)  # L229
            st_folium(route_map, width=1000, height=600)  # L230
