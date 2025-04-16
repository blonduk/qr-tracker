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

def restore_logs_from_sheet():
    try:
        sheet = get_sheet("QR Scan Archive")
        rows = sheet.get_all_records()
        with sqlite3.connect(DB_FILE) as conn:
            for row in rows:
                conn.execute("""
                    INSERT OR IGNORE INTO logs (short_id, timestamp, ip, city, country, user_agent)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    row.get("Short Code"),
                    row.get("Timestamp"),
                    row.get("IP"),
                    row.get("City"),
                    row.get("Country"),
                    row.get("User Agent")
                ))
            conn.commit()
        print("[RESTORE] ✅ Logs restored from Google Sheets")
    except Exception as e:
        print("[RESTORE ERROR]", e)

# === DATABASE SETUP ===
def init_db():
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
        conn.execute('''
            CREATE TABLE IF NOT EXISTS redirects (
                short_id TEXT PRIMARY KEY,
                destination TEXT
            )
        ''')
        conn.execute("INSERT OR IGNORE INTO redirects (short_id, destination) VALUES (?, ?)", ("blondart", "https://www.blondart.co.uk"))
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
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO logs (short_id, timestamp, ip, city, country, user_agent)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (short_id, timestamp, ip, city, country, ua))
        conn.commit()
        dest = cursor.execute("SELECT destination FROM redirects WHERE short_id = ?", (short_id,)).fetchone()

    append_to_archive([short_id, str(timestamp), ip, city, country, ua])

    if dest:
        return redirect(dest[0])
    else:
        return "Invalid code", 404

# === DASHBOARD ===
@app.route('/dashboard')
def dashboard():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT r.short_id, r.destination, COUNT(l.id) as scans
            FROM redirects r
            LEFT JOIN logs l ON r.short_id = l.short_id
            GROUP BY r.short_id
        """)
        stats = cursor.fetchall()

        cursor.execute("SELECT short_id, city, country FROM logs WHERE city IS NOT NULL AND city != ''")
        locations = cursor.fetchall()

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

# === ADD / DELETE / EDIT REDIRECTS ===
@app.route('/add', methods=['POST'])
def add():
    short = request.form['short_id'].strip()
    dest = request.form['destination'].strip()
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("INSERT OR IGNORE INTO redirects (short_id, destination) VALUES (?, ?)", (short, dest))
        conn.commit()
    return redirect("/dashboard")

@app.route('/edit', methods=['POST'])
def edit():
    short = request.form['short_id']
    new_url = request.form['new_destination']
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("UPDATE redirects SET destination = ? WHERE short_id = ?", (new_url, short))
        conn.commit()
    return redirect("/dashboard")

@app.route('/delete/<short_id>', methods=['POST'])
def delete(short_id):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("DELETE FROM redirects WHERE short_id = ?", (short_id,))
        conn.execute("DELETE FROM logs WHERE short_id = ?", (short_id,))
        conn.commit()
    return redirect("/dashboard")

# === CSV EXPORT ===
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

# === RUN ===
if __name__ == '__main__':
    init_db()  # Always ensure tables exist and logs are restored
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
