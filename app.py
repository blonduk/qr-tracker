from flask import Flask, redirect, request, render_template, send_file
import sqlite3, os, io, csv, qrcode, requests
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)
DB_FILE = 'redirects.db'

# === Google Sheets Setup ===
def get_sheet(name):
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name('/etc/secrets/google-credentials.json', scope)
    client = gspread.authorize(creds)
    return client.open(name).sheet1

def append_to_archive(data):
    try:
        sheet = get_sheet("QR Scan Archive")
        sheet.append_row(data)
        print("[SHEET] ✅ Row added")
    except Exception as e:
        print("[SHEET ERROR]", e)

def restore_logs():
    try:
        sheet = get_sheet("QR Scan Archive")
        rows = sheet.get_all_records()
        with sqlite3.connect(DB_FILE) as conn:
            for row in rows:
                conn.execute("""
                    INSERT INTO logs (short_id, timestamp, ip, city, country, user_agent)
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
        print("[RESTORE] ✅ Logs restored")
    except Exception as e:
        print("[RESTORE ERROR]", e)

# === Init DB ===
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            short_id TEXT, timestamp TEXT, ip TEXT, city TEXT, country TEXT, user_agent TEXT
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS redirects (
            short_id TEXT PRIMARY KEY,
            destination TEXT
        )''')
    restore_logs()

# === Routes ===
@app.route('/')
def home():
    return redirect('/dashboard')

@app.route('/track')
def track():
    short_id = request.args.get("id")
    if not short_id:
        return "Missing ID", 400

    ip = request.remote_addr
    ua = request.headers.get("User-Agent", "")[:250]
    timestamp = datetime.utcnow()
    city, country = "", ""

    try:
        geo = requests.get(f"http://ip-api.com/json/{ip}").json()
        city = geo.get("city", "")
        country = geo.get("country", "")
    except: pass

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO logs (short_id, timestamp, ip, city, country, user_agent)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (short_id, timestamp, ip, city, country, ua))
        dest = cursor.execute("SELECT destination FROM redirects WHERE short_id = ?", (short_id,)).fetchone()
        conn.commit()

    append_to_archive([short_id, str(timestamp), ip, city, country, ua])
    return redirect(dest[0]) if dest else "Invalid code", 404

@app.route('/dashboard')
def dashboard():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT r.short_id, r.destination, COUNT(l.id)
            FROM redirects r LEFT JOIN logs l ON r.short_id = l.short_id
            GROUP BY r.short_id
        """)
        stats = cursor.fetchall()

        cursor.execute("SELECT short_id, city, country, ip FROM logs WHERE city != '' OR country != ''")
        logs = cursor.fetchall()

    locations = []
    for row in logs:
        ip = row[3]
        try:
            geo = requests.get(f"http://ip-api.com/json/{ip}").json()
            if geo.get("lat") and geo.get("lon"):
                locations.append([row[0], geo["lat"], geo["lon"]])
        except: pass

    if not locations:
        locations = [["test1", 51.5, -0.12], ["test2", 48.8, 2.35], ["test3", 40.7, -74.0]]

    return render_template("dashboard.html", stats=stats, locations=locations)

@app.route('/view-qr/<short_id>')
def view_qr(short_id):
    url = f"{request.host_url}track?id={short_id}"
    img = qrcode.make(url)
    buf = io.BytesIO(); img.save(buf); buf.seek(0)
    return send_file(buf, mimetype='image/png')

@app.route('/download-qr/<short_id>')
def download_qr(short_id):
    url = f"{request.host_url}track?id={short_id}"
    img = qrcode.make(url)
    buf = io.BytesIO(); img.save(buf); buf.seek(0)
    return send_file(buf, mimetype='image/png', as_attachment=True, download_name=f"{short_id}-qr.png")

@app.route('/add', methods=['POST'])
def add():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("INSERT INTO redirects (short_id, destination) VALUES (?, ?)",
                     (request.form['short_id'], request.form['destination']))
        conn.commit()
    return redirect('/dashboard')

@app.route('/edit', methods=['POST'])
def edit():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("UPDATE redirects SET destination = ? WHERE short_id = ?",
                     (request.form['new_destination'], request.form['short_id']))
        conn.commit()
    return redirect('/dashboard')

@app.route('/delete/<short_id>', methods=['POST'])
def delete(short_id):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("DELETE FROM redirects WHERE short_id = ?", (short_id,))
        conn.execute("DELETE FROM logs WHERE short_id = ?", (short_id,))
        conn.commit()
    return redirect('/dashboard')

@app.route('/export-csv')
def export_csv():
    with sqlite3.connect(DB_FILE) as conn:
        rows = conn.execute("SELECT short_id, timestamp, ip, city, country, user_agent FROM logs").fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Short Code', 'Timestamp', 'IP', 'City', 'Country', 'User Agent'])
    writer.writerows(rows)
    return send_file(io.BytesIO(output.getvalue().encode()), mimetype='text/csv', as_attachment=True, download_name='scan-logs.csv')

if __name__ == '__main__':
    if not os.path.exists(DB_FILE): init_db()
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
