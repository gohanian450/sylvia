import os
import re
import math
import pdfplumber
from flask import Flask, request, jsonify, render_template
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut
import time

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max

geocoder = Nominatim(user_agent="livraison-app", timeout=10)

# ── PDF parsing ──────────────────────────────────────────────────────────────

# Patterns for Canadian / Quebec addresses
ADDRESS_PATTERNS = [
    # "123 Rue Saint-Jean, Montréal, QC H2X 1Z3"
    r'\b(\d{1,5}[\s,]+(?:rue|avenue|ave|boulevard|blvd|chemin|ch|route|rte|place|pl|côte|court|cr|drive|dr|lane|ln|way|wy|road|rd|street|st|boul|impasse|rang|montée|allée|terrasse|croissant)[^\n,]{2,60}(?:,\s*[^\n,]{2,60}){0,3}(?:\s+[A-Z]\d[A-Z]\s?\d[A-Z]\d)?)',
    # "1234, boul. des Sources, Dollard-des-Ormeaux"
    r'\b(\d{1,5},\s*(?:rue|avenue|ave|boulevard|blvd|chemin|ch|boul|route|rte|place|pl|côte|court|cr|drive|dr|road|rd|street|st|impasse|rang|montée|allée)[^\n]{5,80})',
]

def extract_text_from_pdf(filepath):
    text = ""
    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text += t + "\n"
    return text

def find_addresses(text):
    addresses = []
    seen = set()
    for pattern in ADDRESS_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            addr = match.group(1).strip().rstrip(',')
            addr_clean = re.sub(r'\s+', ' ', addr)
            if addr_clean.lower() not in seen and len(addr_clean) > 10:
                seen.add(addr_clean.lower())
                addresses.append(addr_clean)
    return addresses

# ── Geocoding ─────────────────────────────────────────────────────────────────

def geocode_address(address, retries=3):
    for attempt in range(retries):
        try:
            location = geocoder.geocode(address, country_codes=['ca'])
            if location:
                return location.latitude, location.longitude, location.address
            # try without country restriction
            location = geocoder.geocode(address)
            if location:
                return location.latitude, location.longitude, location.address
        except GeocoderTimedOut:
            time.sleep(1)
        except Exception:
            time.sleep(1)
    return None, None, address

# ── Route optimization (nearest-neighbor greedy) ──────────────────────────────

def haversine(lat1, lon1, lat2, lon2):
    R = 6371  # km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))

def nearest_neighbor_route(points, start_lat=None, start_lon=None):
    """Sort points by nearest-neighbor starting from the centroid (or given start)."""
    if not points:
        return []

    valid = [p for p in points if p['lat'] is not None]
    invalid = [p for p in points if p['lat'] is None]

    if not valid:
        return invalid

    if start_lat is None:
        start_lat = sum(p['lat'] for p in valid) / len(valid)
        start_lon = sum(p['lon'] for p in valid) / len(valid)

    unvisited = list(valid)
    route = []
    current_lat, current_lon = start_lat, start_lon

    while unvisited:
        nearest = min(unvisited, key=lambda p: haversine(current_lat, current_lon, p['lat'], p['lon']))
        unvisited.remove(nearest)
        nearest['distance_from_prev'] = round(haversine(current_lat, current_lon, nearest['lat'], nearest['lon']), 2)
        current_lat, current_lon = nearest['lat'], nearest['lon']
        route.append(nearest)

    return route + invalid

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload():
    if 'pdf' not in request.files:
        return jsonify({'error': 'Aucun fichier PDF reçu'}), 400

    f = request.files['pdf']
    if not f.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Le fichier doit être un PDF'}), 400

    path = os.path.join(app.config['UPLOAD_FOLDER'], 'upload.pdf')
    f.save(path)

    # 1. Extract text
    try:
        text = extract_text_from_pdf(path)
    except Exception as e:
        return jsonify({'error': f'Impossible de lire le PDF : {e}'}), 500

    if not text.strip():
        return jsonify({'error': 'Le PDF ne contient pas de texte lisible (image scanné?)'}), 400

    # 2. Find addresses
    raw_addresses = find_addresses(text)

    if not raw_addresses:
        return jsonify({
            'error': 'Aucune adresse trouvée dans le PDF.',
            'raw_text_sample': text[:500]
        }), 200

    # 3. Geocode
    start_lat = request.form.get('start_lat', type=float)
    start_lon = request.form.get('start_lon', type=float)

    points = []
    for i, addr in enumerate(raw_addresses):
        lat, lon, full_addr = geocode_address(addr)
        points.append({
            'index': i + 1,
            'raw': addr,
            'full_address': full_addr,
            'lat': lat,
            'lon': lon,
            'distance_from_prev': 0,
        })
        time.sleep(1.1)  # respect Nominatim rate limit (1 req/sec)

    # 4. Optimize route
    route = nearest_neighbor_route(points, start_lat, start_lon)

    total_distance = sum(p.get('distance_from_prev', 0) for p in route)

    return jsonify({
        'count': len(route),
        'total_distance_km': round(total_distance, 2),
        'route': route,
    })


if __name__ == '__main__':
    os.makedirs('uploads', exist_ok=True)
    app.run(debug=True, port=5000)
