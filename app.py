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

def append_to_archive(data):
    try:
        sheet = get_sheet("QR Scan Archive")
        sheet.append_row(data)
        print("[SHEET] ✅ Row added to archive")
    except Exception as e:
        print("[SHEET ERROR]", e)

def fetch_redirects():
    sheet = get_sheet("QR Redirects")
    records = sheet.get_all_records(expected_headers=["Short Code", "Destination"])
    return [(r["Short Code"], r["Destination"]) for r in records]

def restore_logs_from_sheet():
    try:
        sheet = get_sheet("QR Scan Archive")
        rows = sheet.get_all_records(expected_headers=["Short Code", "Timestamp", "IP", "City", "Country", "User Agent"])
        with sqlite3.connect(DB_FILE) as conn:
            for row in rows:
                conn.execute("""
                    INSERT OR IGNORE INTO logs (short_id, timestamp, ip, city, country, user_agent)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    row["Short Code"],
                    row["Timestamp"],
                    row["IP"],
                    row["City"],
                    row["Country"],
                    row["User Agent"]
                ))
            conn.commit()
        print("[RESTORE] ✅ Logs restored from Google Sheets")
    except Exception as e:
        print("[RESTORE ERROR]", e)

# === DATABASE SETUP ===
def init_db_and_restore():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                short_id TEXT,
                timestamp DATETIME,
                ip TEXT,
                city TEXT,
                country TEXT,
                user_agent TEXT
            )
        ''')
    restore_logs_from_sheet()

@app.route('/')
def home():
    return redirect('/dashboard')

# === MAIN TRACKING ROUTE ===
@app.route('/track')
def track():
    short_id = request.args.get('id')
    if not short_id:
        return "Missing ID", 400

    ip = request.remote_addr
    ua = request.headers.get('User-Agent', '')[:250]
    timestamp = datetime.utcnow()

    try:
        geo = requests.get(f"http://ip-api.com/json/{ip}").json()
        city = geo.get("city", "")
        country = geo.get("country", "")
    except:
        city = ""
        country = ""

    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            INSERT INTO logs (short_id, timestamp, ip, city, country, user_agent)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (short_id, timestamp, ip, city, country, ua))
        conn.commit()

    append_to_archive([short_id, str(timestamp), ip, city, country, ua])

    # Lookup redirect URL from Google Sheet
    redirects = fetch_redirects()
    for sid, url in redirects:
        if sid == short_id:
            return redirect(url)

    return "Invalid tracking code", 404

# === DASHBOARD ===
@app.route('/dashboard')
def dashboard():
    redirects = fetch_redirects()
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        stats = []
        for short_id, dest in redirects:
            count = cursor.execute("SELECT COUNT(*) FROM logs WHERE short_id = ?", (short_id,)).fetchone()[0]
            stats.append((short_id, dest, count))

        locations = cursor.execute("SELECT short_id, city, country FROM logs WHERE city != ''").fetchall()

    return render_template("dashboard.html", stats=stats, locations=locations)

# === QR CODE HANDLERS ===
@app.route('/view-qr/<short_id>')
def view_qr(short_id):
    url = f"{request.host_url}track?id={short_id}"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf)
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

@app.route('/download-qr/<short_id>')
def download_qr(short_id):
    url = f"{request.host_url}track?id={short_id}"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf)
    buf.seek(0)
    return send_file(buf, mimetype='image/png', as_attachment=True, download_name=f"{short_id}-qr.png")

# === EDIT & DELETE ===
@app.route('/edit', methods=['POST'])
def edit():
    short = request.form['short_id']
    new_url = request.form['new_destination']
    sheet = get_sheet("QR Redirects")
    cell = sheet.find(short)
    if cell:
        sheet.update_cell(cell.row, 2, new_url)
    return redirect("/dashboard")

@app.route('/delete/<short_id>', methods=['POST'])
def delete_redirect(short_id):
    sheet = get_sheet("QR Redirects")
    cell = sheet.find(short_id)
    if cell:
        sheet.delete_rows(cell.row)
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("DELETE FROM logs WHERE short_id = ?", (short_id,))
        conn.commit()
    return redirect("/dashboard")

# === EXPORT CSV ===
@app.route('/export-csv')
def export_csv():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT short_id, timestamp, ip, city, country, user_agent FROM logs ORDER BY timestamp DESC")
        rows = cursor.fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Short Code', 'Timestamp', 'IP', 'City', 'Country', 'User Agent'])
    writer.writerows(rows)
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode()), mimetype='text/csv', as_attachment=True, download_name='qr-logs.csv')

# === INIT & RUN ===
if __name__ == '__main__':
    if not os.path.exists(DB_FILE):
        init_db_and_restore()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
