import io
import re
import pdfplumber
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

ADDRESS_PATTERNS = [
    r'\b(\d{1,5}[\s,]+(?:rue|avenue|ave|boulevard|blvd|chemin|ch|route|rte|place|pl|côte|court|cr|drive|dr|lane|ln|way|wy|road|rd|street|st|boul|impasse|rang|montée|allée|terrasse|croissant)[^\n,]{2,60}(?:,\s*[^\n,]{2,60}){0,3}(?:\s+[A-Z]\d[A-Z]\s?\d[A-Z]\d)?)',
    r'\b(\d{1,5},\s*(?:rue|avenue|ave|boulevard|blvd|chemin|ch|boul|route|rte|place|pl|côte|court|cr|drive|dr|road|rd|street|st|impasse|rang|montée|allée)[^\n]{5,80})',
]

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
