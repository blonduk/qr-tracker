from flask import Flask, redirect, request, render_template, send_file
from datetime import datetime
import sqlite3, os, io, csv, requests, qrcode
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)
DB_FILE = 'redirects.db'

def gs_client():
    scope = ['https://spreadsheets.google.com/feeds','https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name(
        '/etc/secrets/google-credentials.json', scope
    )
    return gspread.authorize(creds)

def get_sheet(name):
    return gs_client().open(name).sheet1

def get_redirects():
    sheet = get_sheet("QR Redirects")
    return {r["Short Code"]: r["Destination"] for r in sheet.get_all_records()}

def append_to_archive(row):
    get_sheet("QR Scan Archive").append_row(row)

# — init/restore logs table with lat/lon columns
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS logs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              short_id TEXT,
              timestamp TEXT,
              ip TEXT,
              city TEXT,
              country TEXT,
              user_agent TEXT,
              lat REAL,
              lon REAL
            )
        ''')
    # no need to re‑restore here; we’ll just start fresh data for heatmap

init_db()

@app.route('/track')
def track():
    sid = request.args.get('id')
    if not sid:
        return "Missing ID", 400

    redirects = get_redirects()
    dest = redirects.get(sid)

    ua = request.headers.get('User-Agent','')[:200]
    ip = request.remote_addr
    ts = datetime.utcnow().isoformat(sep=' ', timespec='seconds')

    # Geo lookup
    try:
        g = requests.get(f"http://ip-api.com/json/{ip}").json()
        city, country = g.get("city",""), g.get("country","")
        lat, lon = g.get("lat",0), g.get("lon",0)
    except:
        city = country = ""
        lat = lon = 0

    # log to SQLite
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
          INSERT INTO logs
          (short_id,timestamp,ip,city,country,user_agent,lat,lon)
          VALUES (?,?,?,?,?,?,?,?)
        """, (sid, ts, ip, city, country, ua, lat, lon))
        conn.commit()

    # log to sheet archive
    append_to_archive([sid, ts, ip, city, country, ua, lat, lon])

    if dest:
        return redirect(dest)
    return "Invalid code", 404

@app.route('/dashboard')
def dashboard():
    redirects = get_redirects()
    stats = []
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        for s, d in redirects.items():
            cnt = cur.execute(
                "SELECT COUNT(*) FROM logs WHERE short_id=?", (s,)
            ).fetchone()[0]
            stats.append((s, d, cnt))
        # pull only nonzero coords
        cur.execute("SELECT lat, lon FROM logs WHERE lat != 0 AND lon != 0")
        locations = cur.fetchall()

    return render_template("dashboard.html", stats=stats, locations=locations)

# QR generators & CSV export unchanged…

@app.route('/view-qr/<short_id>')
def view_qr(short_id):
    img = qrcode.make(f"{request.host_url}track?id={short_id}")
    buf = io.BytesIO(); img.save(buf); buf.seek(0)
    return send_file(buf, mimetype='image/png')

@app.route('/download-qr/<short_id>')
def download_qr(short_id):
    img = qrcode.make(f"{request.host_url}track?id={short_id}")
    buf = io.BytesIO(); img.save(buf); buf.seek(0)
    return send_file(buf, mimetype='image/png', as_attachment=True,
                     download_name=f"{short_id}-qr.png")

@app.route('/export-csv')
def export_csv():
    with sqlite3.connect(DB_FILE) as conn:
        rows = conn.execute(
            "SELECT short_id,timestamp,ip,city,country,user_agent,lat,lon FROM logs ORDER BY timestamp DESC"
        ).fetchall()
    mem = io.StringIO(); w = csv.writer(mem)
    w.writerow(['Short Code','Timestamp','IP','City','Country','User Agent','Lat','Lon'])
    w.writerows(rows); mem.seek(0)
    return send_file(io.BytesIO(mem.getvalue().encode()),
                     mimetype='text/csv', as_attachment=True,
                     download_name='qr-logs.csv')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
