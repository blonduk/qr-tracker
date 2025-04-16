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

# ————————————————
# Google Sheets helpers
# ————————————————
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
    rows = sheet.get_all_records()
    return {r["Short Code"]: r["Destination"] for r in rows if r["Short Code"]}

def add_redirect_sheet(s, d):
    get_sheet("QR Redirects").append_row([s, d])

def edit_redirect_sheet(s, d):
    sh = get_sheet("QR Redirects")
    cell = sh.find(s)
    if cell: sh.update_cell(cell.row, 2, d)

def delete_redirect_sheet(s):
    sh = get_sheet("QR Redirects")
    cell = sh.find(s)
    if cell: sh.delete_row(cell.row)

def append_to_archive(row):
    get_sheet("QR Scan Archive").append_row(row)

def restore_logs_from_sheet():
    sh = get_sheet("QR Scan Archive")
    recs = sh.get_all_records()
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS logs (
              id INTEGER PRIMARY KEY,
              short_id TEXT, timestamp TEXT,
              ip TEXT, city TEXT, country TEXT,
              user_agent TEXT
            )
        """)
        for r in recs:
            conn.execute("""
                INSERT OR IGNORE INTO logs
                (short_id,timestamp,ip,city,country,user_agent)
                VALUES (?,?,?,?,?,?)
            """,(
                r["Short Code"], r["Timestamp"],
                r["IP"], r["City"], r["Country"],
                r["User Agent"]
            ))
        conn.commit()

# ————————————————
# DB init & restore on import
# ————————————————
restore_logs_from_sheet()

# ————————————————
# Tracking endpoint
# ————————————————
@app.route('/track')
def track():
    short = request.args.get('id')
    if not short:
        return "Missing ID", 400

    dests = get_redirects()
    dest = dests.get(short)
    ua = request.headers.get('User-Agent','')[:200]
    ip = request.remote_addr
    ts = datetime.utcnow().isoformat(sep=' ', timespec='seconds')

    # Geo lookup
    try:
        geo = requests.get(f"http://ip-api.com/json/{ip}").json()
        city, country = geo.get("city",""), geo.get("country","")
    except:
        city, country = "",""

    # Log locally
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS logs (
             id INTEGER PRIMARY KEY,
             short_id TEXT, timestamp TEXT,
             ip TEXT, city TEXT, country TEXT,
             user_agent TEXT
            )
        """)
        conn.execute("""
            INSERT INTO logs
            (short_id,timestamp,ip,city,country,user_agent)
            VALUES (?,?,?,?,?,?)
        """,(short,ts,ip,city,country,ua))
        conn.commit()

    # Log to Sheet
    append_to_archive([short, ts, ip, city, country, ua])

    if dest:
        return redirect(dest)
    return "Invalid code", 404

# ————————————————
# Dashboard
# ————————————————
@app.route('/dashboard')
def dashboard():
    dests = get_redirects()
    stats = []
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        for s,d in dests.items():
            cnt = cur.execute("SELECT COUNT(*) FROM logs WHERE short_id=?", (s,)).fetchone()[0]
            stats.append((s,d,cnt))
        locs = cur.execute(
            "SELECT ip,city,country FROM logs WHERE city!=''"
        ).fetchall()
    return render_template("dashboard.html", stats=stats, locations=locs)

# ————————————————
# Redirect management
# ————————————————
@app.route('/add', methods=['POST'])
def add():
    s = request.form['short_id'].strip()
    d = request.form['destination'].strip()
    add_redirect_sheet(s,d)
    return redirect('/dashboard')

@app.route('/edit', methods=['POST'])
def edit():
    s = request.form['short_id']
    d = request.form['new_destination']
    edit_redirect_sheet(s,d)
    return redirect('/dashboard')

@app.route('/delete/<short_id>', methods=['POST'])
def delete(short_id):
    delete_redirect_sheet(short_id)
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("DELETE FROM logs WHERE short_id=?", (short_id,))
        conn.commit()
    return redirect('/dashboard')

# ————————————————
# QR & CSV endpoints
# ————————————————
@app.route('/view-qr/<short_id>')
def view_qr(short_id):
    url = f"{request.host_url}track?id={short_id}"
    img = qrcode.make(url); buf=io.BytesIO(); img.save(buf); buf.seek(0)
    return send_file(buf, mimetype='image/png')

@app.route('/download-qr/<short_id>')
def download_qr(short_id):
    url = f"{request.host_url}track?id={short_id}"
    img = qrcode.make(url); buf=io.BytesIO(); img.save(buf); buf.seek(0)
    return send_file(buf, mimetype='image/png', as_attachment=True,
                     download_name=f"{short_id}-qr.png")

@app.route('/export-csv')
def export_csv():
    with sqlite3.connect(DB_FILE) as conn:
        rows = conn.execute(
            "SELECT short_id,timestamp,ip,city,country,user_agent FROM logs ORDER BY timestamp DESC"
        ).fetchall()
    mem=io.StringIO(); w=csv.writer(mem)
    w.writerow(['Short Code','Timestamp','IP','City','Country','User Agent'])
    w.writerows(rows); mem.seek(0)
    return send_file(io.BytesIO(mem.getvalue().encode()), mimetype='text/csv',
                     as_attachment=True, download_name='qr-logs.csv')

# ————————————————
# Launch
# ————————————————
if __name__ == '__main__':
    # verify sheets exist early
    get_sheet("QR Redirects"); get_sheet("QR Scan Archive")
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT",5000)))
