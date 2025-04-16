from flask import Flask, redirect, request, render_template, send_file
from datetime import datetime
import sqlite3
import os
import qrcode
import io
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)
DB_FILE = 'redirects.db'

# === GOOGLE SHEETS SETUP ===
def get_sheet():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_path = '/etc/secrets/google-credentials.json'
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    client = gspread.authorize(creds)
    return client.open("QR Scan Archive").sheet1

def append_to_sheet(data):
    try:
        print(f"[SHEET] Attempting to write row: {data}")
        sheet = get_sheet()
        sheet.append_row(data)
        print("[SHEET] ✅ Row appended successfully")
    except Exception as e:
        print("[SHEET ERROR]", e)

# === DATABASE INIT ===
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
        conn.execute('''
            CREATE TABLE IF NOT EXISTS redirects (
                short_id TEXT PRIMARY KEY,
                destination TEXT
            )
        ''')
        conn.execute("INSERT OR IGNORE INTO redirects (short_id, destination) VALUES (?, ?)", ("blondart", "https://www.blondart.co.uk"))

# === MAIN ROUTES ===
@app.route('/')
def home():
    return redirect('/dashboard')

@app.route('/track')
def track():
    short_id = request.args.get('id')
    if not short_id:
        return "Missing tracking ID", 400

    user_agent = request.headers.get('User-Agent', '').replace('\n', ' ').replace('\r', ' ')[:250]
    ip = request.remote_addr
    timestamp = datetime.utcnow()

    try:
        geo = requests.get(f"http://ip-api.com/json/{ip}").json()
        print("[GEO DEBUG]", geo)
        city = geo.get('city', '')
        country = geo.get('country', '')
        lat = geo.get('lat', 0)
        lon = geo.get('lon', 0)
    except Exception as e:
        print("[GEO ERROR]", e)
        city, country = '', ''
        lat, lon = 0, 0

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO logs (short_id, timestamp, user_agent, ip, city, country, lat, lon)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (short_id, timestamp, user_agent, ip, city, country, lat, lon))
        conn.commit()
        dest = cursor.execute("SELECT destination FROM redirects WHERE short_id = ?", (short_id,)).fetchone()

    sheet_row = [short_id, str(timestamp), ip, city, country, user_agent]
    try:
        append_to_sheet(sheet_row)
    except Exception as sheet_error:
        print("[TRACK] ❌ Sheet write FAILED:", sheet_error)

    if dest:
        return redirect(dest[0])
    else:
        return "Invalid tracking code", 404

@app.route('/dashboard')
def dashboard():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT r.short_id, r.destination, COUNT(l.id)
            FROM redirects r
            LEFT JOIN logs l ON r.short_id = l.short_id
            GROUP BY r.short_id
        """)
        stats = cursor.fetchall()

        cursor.execute("SELECT short_id, timestamp, lat, lon, city, country FROM logs WHERE lat != 0 AND lon != 0")
        locations = cursor.fetchall()

    return render_template("dashboard.html", stats=stats, locations=locations)

@app.route('/add', methods=['POST'])
def add_redirect():
    short_id = request.form.get('short_id').strip()
    destination = request.form.get('destination').strip()

    if not short_id or not destination:
        return "Missing fields", 400

    with sqlite3.connect(DB_FILE) as conn:
        try:
            conn.execute("INSERT INTO redirects (short_id, destination) VALUES (?, ?)", (short_id, destination))
            conn.commit()
        except sqlite3.IntegrityError:
            return "Shortcode already exists", 400

    return redirect("/dashboard")

@app.route('/edit', methods=['POST'])
def edit_redirect():
    short_id = request.form.get('short_id')
    new_dest = request.form.get('new_destination')
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("UPDATE redirects SET destination = ? WHERE short_id = ?", (new_dest, short_id))
        conn.commit()
    return redirect("/dashboard")

@app.route('/delete/<short_id>', methods=['POST'])
def delete_redirect(short_id):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("DELETE FROM logs WHERE short_id = ?", (short_id,))
        conn.execute("DELETE FROM redirects WHERE short_id = ?", (short_id,))
        conn.commit()
    return redirect("/dashboard")

@app.route('/view-qr/<short_id>')
def view_qr(short_id):
    try:
        from PIL import Image
    except ImportError:
        return "[QR VIEW ERROR] Missing Pillow", 500

    url = f"{request.host_url.rstrip('/')}/track?id={short_id}"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

@app.route('/export-csv')
def export_csv():
    import csv
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

# === STARTUP ===
if __name__ == '__main__':
    if not os.path.exists(DB_FILE):
        init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
