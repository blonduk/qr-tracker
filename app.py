from flask import Flask, redirect, request, render_template, send_file
from datetime import datetime
import sqlite3
import os
import qrcode
import io
import csv
import requests

app = Flask(__name__)

DB_FILE = 'redirects.db'

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

@app.route('/track')
def track():
    short_id = request.args.get('id')
    user_agent = request.headers.get('User-Agent')
    ip = request.remote_addr
    timestamp = datetime.utcnow()

    # Geolocation lookup
    try:
        geo = requests.get(f"http://ip-api.com/json/{ip}").json()
        city = geo.get('city', '')
        country = geo.get('country', '')
        lat = geo.get('lat', 0)
        lon = geo.get('lon', 0)
    except:
        city = ''
        country = ''
        lat = 0
        lon = 0

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO logs (short_id, timestamp, user_agent, ip, city, country, lat, lon)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (short_id, timestamp, user_agent, ip, city, country, lat, lon))
        conn.commit()
        dest = cursor.execute("SELECT destination FROM redirects WHERE short_id = ?", (short_id,)).fetchone()

    if dest:
        return redirect(dest[0])
    else:
        return "Invalid tracking code", 404

# Everything else stays the same — shortened for clarity:
@app.route('/dashboard')
def dashboard():
    new_code = request.args.get('new')
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT r.short_id, r.destination, COUNT(l.id) as scan_count
            FROM redirects r
            LEFT JOIN logs l ON r.short_id = l.short_id
            GROUP BY r.short_id
        """)
        stats = cursor.fetchall()
        return render_template('dashboard.html', stats=stats, new_code=new_code)

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

    return redirect(f"/dashboard?new={short_id}")

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

@app.route('/download-qr/<short_id>')
def download_qr(short_id):
    track_url = f"{request.host_url.rstrip('/')}/track?id={short_id}"
    img = qrcode.make(track_url)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)

    return send_file(
        buf,
        mimetype='image/png',
        as_attachment=True,
        download_name=f'qr-{short_id}.png'
    )

@app.route('/view-qr/<short_id>')
def view_qr(short_id):
    track_url = f"{request.host_url.rstrip('/')}/track?id={short_id}"
    img = qrcode.make(track_url)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)

    return send_file(
        buf,
        mimetype='image/png',
        as_attachment=False
    )

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
    return send_file(
        io.BytesIO(output.getvalue().encode()),
        mimetype='text/csv',
        as_attachment=True,
        download_name='qr-scan-logs.csv'
    )

if __name__ == '__main__':
    if not os.path.exists(DB_FILE):
        init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
