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
    """Return dict: {short_code: destination} from QR Redirects sheet."""
    sheet = get_sheet("QR Redirects")
    rows = sheet.get_all_records()
    return {r["Short Code"]: r["Destination"] for r in rows if r["Short Code"]}

def add_redirect_sheet(short, dest):
    sheet = get_sheet("QR Redirects")
    sheet.append_row([short, dest])

def edit_redirect_sheet(short, new_dest):
    sheet = get_sheet("QR Redirects")
    cell = sheet.find(short)
    if cell:
        sheet.update_cell(cell.row, 2, new_dest)

def delete_redirect_sheet(short):
    sheet = get_sheet("QR Redirects")
    cell = sheet.find(short)
    if cell:
        sheet.delete_row(cell.row)

def append_to_archive(row):
    sheet = get_sheet("QR Scan Archive")
    sheet.append_row(row)

def restore_logs_from_sheet():
    """On startup, pull everything from the Scan Archive sheet into SQLite logs."""
    sheet = get_sheet("QR Scan Archive")
    records = sheet.get_all_records()
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS logs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              short_id TEXT, timestamp TEXT,
              ip TEXT, city TEXT, country TEXT,
              user_agent TEXT
            )
        """)
        for r in records:
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
# App startup & DB init
# ————————————————
@app.before_first_request
def initialize():
    # ensure logs table exists & restore from sheet
    restore_logs_from_sheet()

# ————————————————
# Tracking endpoint
# ————————————————
@app.route('/track')
def track():
    short = request.args.get('id')
    if not short:
        return "Missing ID", 400

    redirects = get_redirects()
    dest = redirects.get(short)
    ua = request.headers.get('User-Agent','')[:200]
    ip = request.remote_addr
    ts = datetime.utcnow().isoformat(sep=' ', timespec='seconds')

    # Geo lookup
    try:
        g = requests.get(f"http://ip-api.com/json/{ip}").json()
        city, country = g.get("city",""), g.get("country","")
    except:
        city, country = "",""

    # Log locally
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
            INSERT INTO logs
            (short_id,timestamp,ip,city,country,user_agent)
            VALUES (?,?,?,?,?,?)
        """,(short,ts,ip,city,country,ua))
        conn.commit()

    # Log to Sheets
    append_to_archive([short, ts, ip, city, country, ua])

    if dest:
        return redirect(dest)
    return "Invalid code", 404

# ————————————————
# Dashboard
# ————————————————
@app.route('/dashboard')
def dashboard():
    redirects = get_redirects()
    stats = []
    with sqlite3.connect(DB_FILE) as conn:
        cur = conn.cursor()
        for s,d in redirects.items():
            count = cur.execute(
                "SELECT COUNT(*) FROM logs WHERE short_id=?", (s,)
            ).fetchone()[0]
            stats.append((s,d,count))
        locs = cur.execute(
            "SELECT lat,lon FROM logs WHERE city != '' AND lat IS NOT NULL AND lon IS NOT NULL"
        ).fetchall()
    return render_template("dashboard.html", stats=stats, locations=locs)

# ————————————————
# Redirect management
# ————————————————
@app.route('/add', methods=['POST'])
def add():
    short = request.form['short_id'].strip()
    dest  = request.form['destination'].strip()
    add_redirect_sheet(short,dest)
    return redirect('/dashboard')

@app.route('/edit', methods=['POST'])
def edit():
    short = request.form['short_id']
    newd  = request.form['new_destination']
    edit_redirect_sheet(short,newd)
    return redirect('/dashboard')

@app.route('/delete/<short_id>', methods=['POST'])
def delete(short_id):
    delete_redirect_sheet(short_id)
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("DELETE FROM logs WHERE short_id=?", (short_id,))
        conn.commit()
    return redirect('/dashboard')

# ————————————————
# QR generators & CSV export
# ————————————————
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
            "SELECT short_id,timestamp,ip,city,country,user_agent FROM logs ORDER BY timestamp DESC"
        ).fetchall()
    mem = io.StringIO()
    w = csv.writer(mem)
    w.writerow(['Short Code','Timestamp','IP','City','Country','User Agent'])
    w.writerows(rows)
    mem.seek(0)
    return send_file(io.BytesIO(mem.getvalue().encode()),
                     mimetype='text/csv',
                     as_attachment=True,
                     download_name='qr-logs.csv')

# ————————————————
# Launch
# ————————————————
if __name__ == '__main__':
    # make sure archive & redirects sheets exist or error early
    get_sheet("QR Redirects")
    get_sheet("QR Scan Archive")
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT",5000)))
