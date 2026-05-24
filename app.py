import io
import os
import re
import time
import requests
import pdfplumber
from urllib.parse import quote
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

# Street type keywords (FR + EN)
_STREET_TYPES = (
    r'rue|avenue|ave|boulevard|blvd|chemin|ch|route|rte|place|pl|'
    r'côte|court|cr|drive|dr|lane|ln|road|rd|street|st|boul|'
    r'impasse|rang|montée|allée|terrasse|croissant|way|wy'
)

# Phone number patterns to strip from the end of an address
_PHONE_RE = re.compile(
    r'\s*[\(\+]?[\d\s\-\.]{7,15}\d\s*$'
)

# Postal code (Canadian)
_POSTAL_RE = re.compile(r'[A-Z]\d[A-Z]\s*\d[A-Z]\d', re.IGNORECASE)

_nominatim_last_call = 0


def extract_text(stream):
    text = ""
    with pdfplumber.open(stream) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                text += t + "\n"
    return text


def clean_address(raw):
    """Remove phone numbers and trailing junk, keep only the address part."""
    # Strip phone number at end (e.g. 514-555-0101)
    addr = re.sub(r'\s+\d{3}[\s.\-]\d{3}[\s.\-]\d{4}\s*$', '', raw)
    # Strip any remaining trailing digits block (e.g. row numbers)
    addr = re.sub(r'\s+\d{1,5}\s*$', '', addr)
    # Collapse whitespace
    addr = re.sub(r'\s+', ' ', addr).strip().rstrip(',').strip()
    return addr


def find_addresses(text):
    """Extract street addresses from plain text."""
    addresses = []
    seen = set()

    pattern = re.compile(
        r'\b(\d{1,5}[,\s]+(?:' + _STREET_TYPES + r')\b'
        r'[^\n]{2,60}'
        r'(?:,\s*[^\n,]{2,50}){0,3})',
        re.IGNORECASE
    )

    for match in pattern.finditer(text):
        addr = clean_address(match.group(1))
        if len(addr) > 10 and addr.lower() not in seen:
            seen.add(addr.lower())
            addresses.append(addr)

    return addresses


def _simplify(address):
    """Return a simplified version of the address for fallback geocoding."""
    # Drop postal code and everything after
    addr = _POSTAL_RE.sub('', address).strip().rstrip(',').strip()
    return addr


def geocode_google(address, api_key):
    url = (
        f"https://maps.googleapis.com/maps/api/geocode/json"
        f"?address={quote(address)}&key={api_key}&region=ca&language=fr"
    )
    try:
        r = requests.get(url, timeout=8)
        data = r.json()
        status = data.get('status', 'UNKNOWN')
        if status == 'OK' and data.get('results'):
            loc = data['results'][0]['geometry']['location']
            return loc['lat'], loc['lng'], data['results'][0]['formatted_address'], None
        return None, None, None, status   # return status so caller can log it
    except Exception as e:
        return None, None, None, str(e)


def geocode_nominatim(address):
    global _nominatim_last_call
    elapsed = time.time() - _nominatim_last_call
    if elapsed < 1.1:
        time.sleep(1.1 - elapsed)

    headers = {'User-Agent': 'sylvia-livraison/1.0 (gabohanian@gmail.com)'}
    queries = [address, _simplify(address)]

    for q in queries:
        for country in ['ca', '']:
            params = {'q': q, 'format': 'json', 'limit': 1, 'addressdetails': 0}
            if country:
                params['countrycodes'] = country
            try:
                r = requests.get(
                    'https://nominatim.openstreetmap.org/search',
                    params=params, headers=headers, timeout=8
                )
                _nominatim_last_call = time.time()
                data = r.json()
                if data:
                    return float(data[0]['lat']), float(data[0]['lon']), data[0]['display_name'], None
            except Exception:
                pass
            time.sleep(0.3)

    return None, None, None, 'ZERO_RESULTS'


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
            'error': 'Aucune adresse trouvée. Vérifiez que le PDF contient des adresses comme "123 Rue des Érables".',
            'preview': text[:600],
        }), 200

    return jsonify({'addresses': addresses})


@app.route('/geocode')
def geocode():
    address = request.args.get('address', '').strip()
    if not address:
        return jsonify({'error': 'Adresse manquante'}), 400

    api_key = os.environ.get('GOOGLE_MAPS_API_KEY', '')

    if api_key:
        lat, lon, display, err = geocode_google(address, api_key)
    else:
        lat, lon, display, err = geocode_nominatim(address)

    if lat is None:
        # err tells the frontend (and us) what went wrong
        return jsonify({'found': False, 'reason': err or 'ZERO_RESULTS'})

    return jsonify({'found': True, 'lat': lat, 'lon': lon, 'display': display})


@app.route('/geocode/provider')
def geocode_provider():
    key = os.environ.get('GOOGLE_MAPS_API_KEY', '')
    return jsonify({'provider': 'google' if key else 'nominatim'})
