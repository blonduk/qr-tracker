from flask import Flask, redirect, request, render_template, send_file
from datetime import datetime
import sqlite3
import os
import qrcode
import io
import csv
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)
DB_FILE = 'redirects.db'

# === GOOGLE SHEETS SETUP ===
def get_sheet(name):
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_path = '/etc/secrets/google-credentials.json'
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    client = gspread.authorize(creds)
    return client.open(name).sheet1

def get_redirects_from_sheet():
    try:
        sheet = get_sheet("QR Redirects")
        data = sheet.get_all_values()[1:]  # skip header
        return {row[0]: row[1] for row in data if row[0] and row[1]}
    except Exception as e:
        print("[SHEET REDIRECT ERROR]", e)
        return {}

def append_to_scan_sheet(data):
    try:
        print(f"[SHEET] Attempting to write row: {data}")
        sheet = get_sheet("QR Scan Archive")
        sheet.append_row(data)
        print("[SHEET] ✅ Row appended successfully")
    except Exception as e:
        print("[SHEET ERROR]", e)

@app.route('/')
def home():
    return redirect('/dashboard')

@app.route('/track')
def track():
    short_id = request.args.get('id')
    if not short_id:
        return "Missing ID", 400

    redirects = get_redirects_from_sheet()
    destination = redirects.get(short_id)

    user_agent = request.headers.get('User-Agent', '').replace('\n', ' ').replace('\r', ' ')[:250]
    ip = request.remote_addr
    timestamp = datetime.utcnow()

    # Geo IP lookup
    try:
        geo = requests.get(f"http://ip-api.com/json/{ip}").json()
        city = geo.get("city", "")
        country = geo.get("country", "")
        lat = geo.get("lat", 0)
        lon = geo.get("lon", 0)
    except Exception as e:
        print("[GEO ERROR]", e)
        city, country, lat, lon = '', '', 0, 0

    # Log scan to local DB
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                short_id TEXT,
                timestamp TEXT,
                ip TEXT,
                user_agent TEXT,
                city TEXT,
                country TEXT,
                lat REAL,
                lon REAL
            )
        """)
        conn.execute("""
            INSERT INTO logs (short_id, timestamp, ip, user_agent, city, country, lat, lon)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (short_id, timestamp, ip, user_agent, city, country, lat, lon))
        conn.commit()

    # Log to Google Sheets
    append_to_scan_sheet([short_id, str(timestamp), ip, city, country, user_agent])

    if destination:
        return redirect(destination)
    return "Invalid tracking code", 404

@app.route('/dashboard')
def dashboard():
    redirects = get_redirects_from_sheet()
    stats = []
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        for short_id, destination in redirects.items():
            count = cursor.execute("SELECT COUNT(*) FROM logs WHERE short_id = ?", (short_id,)).fetchone()[0]
            stats.append((short_id, destination, count))

        cursor.execute("SELECT short_id, timestamp, lat, lon, city, country FROM logs WHERE lat != 0 AND lon != 0")
        locations = cursor.fetchall()

    return render_template("dashboard.html", stats=stats, locations=locations)

@app.route('/view-qr/<short_id>')
def view_qr(short_id):
    try:
        track_url = f"{request.host_url.rstrip('/')}/track?id={short_id}"
        img = qrcode.make(track_url)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        return send_file(buf, mimetype='image/png')
    except Exception as e:
        print("[QR VIEW ERROR]", e)
        return "QR code error", 500

@app.route('/download-qr/<short_id>')
def download_qr(short_id):
    track_url = f"{request.host_url.rstrip('/')}/track?id={short_id}"
    img = qrcode.make(track_url)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png', as_attachment=True, download_name=f"qr-{short_id}.png")

@app.route('/export-csv')
def export_csv():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Short Code', 'Timestamp', 'IP', 'City', 'Country', 'User Agent'])
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT short_id, timestamp, ip, city, country, user_agent FROM logs ORDER BY timestamp DESC")
        for row in cursor.fetchall():
            writer.writerow(row)
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode()), mimetype='text/csv', as_attachment=True, download_name='qr-scan-logs.csv')

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
