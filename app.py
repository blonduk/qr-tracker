from flask import Flask, redirect, request, render_template, send_file
from datetime import datetime
import sqlite3, os, io, csv, requests, qrcode
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)
DB_FILE = 'redirects.db'

# —————————————————————————————
# Google Sheets helpers
# —————————————————————————————
def gs_client():
    scope = ['https://spreadsheets.google.com/feeds',
             'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name(
        '/etc/secrets/google-credentials.json', scope
    )
    return gspread.authorize(creds)

def get_sheet(name):
    return gs_client().open(name).sheet1

def get_redirects():
    sheet = get_sheet("QR Redirects")
    rows = sheet.get_all_records()
    return {r["Short Code"].strip(): r["Destination"].strip()
            for r in rows if r.get("Short Code")}

def add_redirect(s,d):   get_sheet("QR Redirects").append_row([s,d])
def edit_redirect(s,d):
    sh = get_sheet("QR Redirects"); cell=sh.find(s)
    if cell: sh.update_cell(cell.row,2,d)
def delete_redirect(s):
    sh = get_sheet("QR Redirects"); cell=sh.find(s)
    if cell: sh.delete_rows(cell.row)

def append_to_archive(row):
    # row = [short, timestamp, ip, city, country, ua, lat, lon]
    get_sheet("QR Scan Archive").append_row(row)

# —————————————————————————————
# Initialize DB + restore logs
# —————————————————————————————
def init_db_and_restore():
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
    # Now restore all rows from sheet
    sh = get_sheet("QR Scan Archive")
    records = sh.get_all_records()
    with sqlite3.connect(DB_FILE) as conn:
        for r in records:
            # normalize key names and default missing
            short = r.get("Short Code","").strip()
            ts    = r.get("Timestamp","").strip()
            ip    = r.get("IP","").strip()
            city  = r.get("City","").strip()
            country = r.get("Country","").strip()
            ua    = r.get("User Agent","").strip()
            # some sheets may have 'Lat','Lon' or 'lat','lon'
            lat   = r.get("Lat", r.get("lat", 0)) or 0
            lon   = r.get("Lon", r.get("lon", 0)) or 0

            conn.execute("""
                INSERT OR IGNORE INTO logs
                (short_id,timestamp,ip,city,country,user_agent,lat,lon)
                VALUES (?,?,?,?,?,?,?,?)
            """, (short, ts, ip, city, country, ua, lat, lon))
        conn.commit()

# run init+restore on import
init_db_and_restore()

# —————————————————————————————
# Tracking endpoint
# —————————————————————————————
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
        geo = requests.get(f"http://ip-api.com/json/{ip}").json()
        city = geo.get("city","")
        country = geo.get("country","")
        lat = geo.get("lat", 0)
        lon = geo.get("lon", 0)
    except:
        city=country=""; lat=lon=0

    # store in SQLite
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            INSERT INTO logs
            (short_id,timestamp,ip,city,country,user_agent,lat,lon)
            VALUES (?,?,?,?,?,?,?,?)
        """, (sid, ts, ip, city, country, ua, lat, lon))
        conn.commit()

    # store in Google Sheet archive
    append_to_archive([sid, ts, ip, city, country, ua, lat, lon])

    if dest:
        return redirect(dest)
    return "Invalid code", 404

# —————————————————————————————
# Dashboard endpoint
# —————————————————————————————
@app.route('/dashboard')
def dashboard():
    redirects = get_redirects()
    stats = []
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        for code, url in redirects.items():
            count = cur.execute(
                "SELECT COUNT(*) FROM logs WHERE short_id=?", (code,)
            ).fetchone()[0]
            stats.append((code, url, count))
        cur.execute("SELECT lat,lon FROM logs WHERE lat<>0 AND lon<>0")
        locations = cur.fetchall()

    return render_template("dashboard.html",
                           stats=stats,
                           locations=locations)

# —————————————————————————————
# Redirect management
# —————————————————————————————
@app.route('/add', methods=['POST'])
def add():
    s=request.form['short_id'].strip()
    d=request.form['destination'].strip()
    add_redirect(s,d)
    return redirect('/dashboard')

@app.route('/edit', methods=['POST'])
def edit():
    s=request.form['short_id']
    d=request.form['new_destination'].strip()
    edit_redirect(s,d)
    return redirect('/dashboard')

@app.route('/delete/<short_id>', methods=['POST'])
def delete(short_id):
    delete_redirect(short_id)
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("DELETE FROM logs WHERE short_id=?", (short_id,))
        conn.commit()
    return redirect('/dashboard')

# —————————————————————————————
# QR & CSV endpoints
# —————————————————————————————
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
    return send_file(buf, mimetype='image/png',
                     as_attachment=True,
                     download_name=f"{short_id}-qr.png")

@app.route('/export-csv')
def export_csv():
    with sqlite3.connect(DB_FILE) as conn:
        rows = conn.execute(
            "SELECT short_id,timestamp,ip,city,country,user_agent,lat,lon "
            "FROM logs ORDER BY timestamp DESC"
        ).fetchall()
    mem = io.StringIO(); w = csv.writer(mem)
    w.writerow(['Short Code','Timestamp','IP','City','Country','User Agent','Lat','Lon'])
    w.writerows(rows); mem.seek(0)
    return send_file(io.BytesIO(mem.getvalue().encode()),
                     mimetype='text/csv',
                     as_attachment=True,
                     download_name='qr-logs.csv')

# —————————————————————————————
# Launch
# —————————————————————————————
if __name__=='__main__':
    # verify your sheets exist before start
    get_sheet("QR Redirects")
    get_sheet("QR Scan Archive")
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT",5000)))
