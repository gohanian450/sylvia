import io
import os
import re
import time
import requests
import pdfplumber
from urllib.parse import quote
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

ADDRESS_PATTERNS = [
    r'\b(\d{1,5}[\s,]+(?:rue|avenue|ave|boulevard|blvd|chemin|ch|route|rte|place|pl|côte|court|cr|drive|dr|lane|ln|way|wy|road|rd|street|st|boul|impasse|rang|montée|allée|terrasse|croissant)[^\n,]{2,60}(?:,\s*[^\n,]{2,60}){0,3}(?:\s+[A-Z]\d[A-Z]\s?\d[A-Z]\d)?)',
    r'\b(\d{1,5},\s*(?:rue|avenue|ave|boulevard|blvd|chemin|ch|boul|route|rte|place|pl|côte|court|cr|drive|dr|road|rd|street|st|impasse|rang|montée|allée)[^\n]{5,80})',
]

_nominatim_last_call = 0

def extract_text(stream):
    text = ""
    with pdfplumber.open(stream) as pdf:
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
            addr = re.sub(r'\s+', ' ', match.group(1).strip().rstrip(','))
            if addr.lower() not in seen and len(addr) > 10:
                seen.add(addr.lower())
                addresses.append(addr)
    return addresses

def geocode_google(address, api_key):
    url = f"https://maps.googleapis.com/maps/api/geocode/json?address={quote(address)}&key={api_key}&region=ca&language=fr"
    r = requests.get(url, timeout=8)
    data = r.json()
    if data.get('status') == 'OK' and data.get('results'):
        loc = data['results'][0]['geometry']['location']
        return loc['lat'], loc['lng'], data['results'][0]['formatted_address']
    return None, None, None

def geocode_nominatim(address):
    global _nominatim_last_call
    # respect 1 req/sec
    elapsed = time.time() - _nominatim_last_call
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)

    headers = {'User-Agent': 'sylvia-livraison/1.0 (gabohanian@gmail.com)'}

    # Try with full address first, then simplified
    queries = [address, re.sub(r',?\s*[A-Z]\d[A-Z]\s*\d[A-Z]\d', '', address).strip()]

    for q in queries:
        for country in ['ca', '']:
            params = {'q': q, 'format': 'json', 'limit': 1, 'addressdetails': 0}
            if country:
                params['countrycodes'] = country
            try:
                r = requests.get('https://nominatim.openstreetmap.org/search',
                                 params=params, headers=headers, timeout=8)
                _nominatim_last_call = time.time()
                data = r.json()
                if data:
                    return float(data[0]['lat']), float(data[0]['lon']), data[0]['display_name']
            except Exception:
                pass
            time.sleep(0.3)

    return None, None, None

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/extract', methods=['POST'])
def extract():
    if 'pdf' not in request.files:
        return jsonify({'error': 'Aucun fichier PDF reçu'}), 400

    f = request.files['pdf']
    if not f.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Le fichier doit être un PDF'}), 400

    try:
        stream = io.BytesIO(f.read())
        text = extract_text(stream)
    except Exception as e:
        return jsonify({'error': f'Impossible de lire le PDF : {e}'}), 500

    if not text.strip():
        return jsonify({'error': 'Le PDF ne contient pas de texte lisible (PDF scanné ?)'}), 400

    addresses = find_addresses(text)

    if not addresses:
        return jsonify({
            'error': 'Aucune adresse trouvée. Vérifiez que le PDF contient des adresses de type "123 Rue des Érables".',
            'preview': text[:600],
        }), 200

    return jsonify({'addresses': addresses})

@app.route('/geocode')
def geocode():
    """Proxy endpoint — keeps API keys server-side."""
    address = request.args.get('address', '').strip()
    if not address:
        return jsonify({'error': 'Adresse manquante'}), 400

    api_key = os.environ.get('GOOGLE_MAPS_API_KEY', '')

    if api_key:
        lat, lon, display = geocode_google(address, api_key)
    else:
        lat, lon, display = geocode_nominatim(address)

    if lat is None:
        return jsonify({'found': False})
    return jsonify({'found': True, 'lat': lat, 'lon': lon, 'display': display})

@app.route('/geocode/provider')
def geocode_provider():
    """Let the frontend know which geocoder is active."""
    key = os.environ.get('GOOGLE_MAPS_API_KEY', '')
    return jsonify({'provider': 'google' if key else 'nominatim'})
