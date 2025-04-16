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

# === Google Sheets Setup ===
def get_gsheet(sheet_name):
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_path = '/etc/secrets/google-credentials.json'
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    client = gspread.authorize(creds)
    sheet = client.open(sheet_name).sheet1
    return sheet

def get_redirects_from_sheet():
    try:
        sheet = get_gsheet("QR Redirects")
        rows = sheet.get_all_records()
        redirects = {row["Short Code"].strip(): row["Destination"].strip() for row in rows if row["Short Code"] and row["Destination"]}
        print(f"[SHEETS] Loaded {len(redirects)} redirects from QR Redirects")
        return redirects
    except Exception as e:
        print("[SHEETS ERROR] Failed to load redirects:", e)
        return {}

def append_to_scan_sheet(data):
    try:
        print(f"[SHEETS] Logging scan to archive: {data}")
        sheet = get_gsheet("QR Scan Archive")
        sheet.append_row(data)
        print("[SHEETS] ✅ Row appended to QR Scan Archive")
    except Exception as e:
        print("[SHEETS ERROR] Scan archive write failed:", e)

# === DB Setup ===
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                short_id TEXT,
                timestamp DATETIME,
                user_agent TEXT,
                ip TEXT,
                city TEXT,
                country TEXT,
                lat REAL,
                lon REAL
            )
        ''')

@app.route('/')
def home():
    return redirect('/dashboard')

@app.route('/track')
def track():
    short_id = request.args.get('id')
    if not short_id:
        return "Missing ID", 400

    redirects = get_redirects_from_sheet()
    if short_id not in redirects:
        return "Invalid tracking code", 404

    user_agent = request.headers.get('User-Agent', '').replace('\n', ' ').replace('\r', ' ')[:250]
    ip = request.remote_addr
    timestamp = datetime.utcnow()

    try:
        geo = requests.get(f"http://ip-api.com/json/{ip}").json()
        city = geo.get('city', '')
        country = geo.get('country', '')
        lat = geo.get('lat', 0)
        lon = geo.get('lon', 0)
    except Exception as geo_err:
        print("[GEO ERROR]", geo_err)
        city, country = '', ''
        lat, lon = 0, 0

    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            INSERT INTO logs (short_id, timestamp, user_agent, ip, city, country, lat, lon)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (short_id, timestamp, user_agent, ip, city, country, lat, lon))
        conn.commit()

    append_to_scan_sheet([short_id, str(timestamp), ip, city, country, user_agent])

    return redirect(redirects[short_id])

@app.route('/dashboard')
def dashboard():
    new_code = request.args.get('new')
    redirects = get_redirects_from_sheet()

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        stats = []
        for code, url in redirects.items():
            count = cursor.execute("SELECT COUNT(*) FROM logs WHERE short_id = ?", (code,)).fetchone()[0]
            stats.append((code, url, count))

        cursor.execute("SELECT short_id, timestamp, lat, lon, city, country FROM logs")
        raw_locations = cursor.fetchall()

    # Filter bad coordinates
    locations = [row for row in raw_locations if row[2] and row[3] and row[2] != 0 and row[3] != 0]

    return render_template("dashboard.html", stats=stats, new_code=new_code, locations=locations)

@app.route('/add', methods=['POST'])
def add_redirect():
    return "⛔ This version uses Google Sheets for redirects. Add new codes in the 'QR Redirects' sheet manually."

@app.route('/view-qr/<short_id>')
def view_qr(short_id):
    try:
        track_url = f"{request.host_url.rstrip('/')}/track?id={short_id}"
        img = qrcode.make(track_url)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        return send_file(buf, mimetype='image/png', as_attachment=False)
    except Exception as e:
        print("[QR VIEW ERROR]", e)
        return "QR preview failed", 500

@app.route('/download-qr/<short_id>')
def download_qr(short_id):
    try:
        track_url = f"{request.host_url.rstrip('/')}/track?id={short_id}"
        img = qrcode.make(track_url)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        return send_file(buf, mimetype='image/png', as_attachment=True, download_name=f"qr-{short_id}.png")
    except Exception as e:
        print("[QR DOWNLOAD ERROR]", e)
        return "Download failed", 500

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

# === INIT ===
if not os.path.exists(DB_FILE):
    init_db()
