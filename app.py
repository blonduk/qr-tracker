from flask import Flask, redirect, request, render_template, send_file
from datetime import datetime
import sqlite3, os, io, csv, requests, qrcode
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
    return {r.get("Short Code", "").strip(): r.get("Destination", "").strip()
            for r in rows if r.get("Short Code")}

def add_redirect(s, d):     get_sheet("QR Redirects").append_row([s, d])
def edit_redirect(s, d):
    sh = get_sheet("QR Redirects"); c = sh.find(s)
    if c: sh.update_cell(c.row, 2, d)
def delete_redirect(s):
    sh = get_sheet("QR Redirects"); c = sh.find(s)
    if c: sh.delete_row(c.row)

def append_to_archive(row):
    get_sheet("QR Scan Archive").append_row(row)

def restore_logs_from_sheet():
    sh = get_sheet("QR Scan Archive")
    raw = sh.get_all_records()
    # normalize keys to lowercase/stripped
    norm_rows = []
    for r in raw:
        norm = {k.strip().lower(): v for k, v in r.items()}
        if "short code" in norm and "timestamp" in norm:
            norm_rows.append(norm)
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
          CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY,
            short_id TEXT, timestamp TEXT,
            ip TEXT, city TEXT, country TEXT,
            user_agent TEXT
          )
        """)
        for r in norm_rows:
            conn.execute("""
              INSERT OR IGNORE INTO logs
              (short_id,timestamp,ip,city,country,user_agent)
              VALUES (?,?,?,?,?,?)
            """,(
              r.get("short code",""),
              r.get("timestamp",""),
              r.get("ip",""),
              r.get("city",""),
              r.get("country",""),
              r.get("user agent","")
            ))
        conn.commit()

# ————————————————
# Initialize on import
# ————————————————
restore_logs_from_sheet()

# ————————————————
# Tracking
# ————————————————
@app.route('/track')
def track():
    sid = request.args.get('id')
    if not sid: return "Missing ID", 400
    dests = get_redirects(); dest = dests.get(sid)

    ua = request.headers.get('User-Agent','')[:200]
    ip = request.remote_addr
    ts = datetime.utcnow().isoformat(sep=' ', timespec='seconds')
    # Geo
    try:
        g = requests.get(f"http://ip-api.com/json/{ip}").json()
        city, country = g.get("city",""), g.get("country","")
    except:
        city, country = "",""

    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("INSERT INTO logs (short_id,timestamp,ip,city,country,user_agent) VALUES (?,?,?,?,?,?)",
                     (sid,ts,ip,city,country,ua))
        conn.commit()
    append_to_archive([sid, ts, ip, city, country, ua])

    return redirect(dest) if dest else ("Invalid code", 404)

# ————————————————
# Dashboard
# ————————————————
@app.route('/dashboard')
def dashboard():
    dests = get_redirects()
    stats = []; locs = []
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        for s,d in dests.items():
            cnt = c.execute("SELECT COUNT(*) FROM logs WHERE short_id=?", (s,)).fetchone()[0]
            stats.append((s,d,cnt))
        locs = c.execute("SELECT ip,city,country FROM logs WHERE city!=''").fetchall()
    return render_template("dashboard.html", stats=stats, locations=locs)

# ————————————————
# Redirect management
# ————————————————
@app.route('/add', methods=['POST'])
def add(): add_redirect(request.form['short_id'].strip(), request.form['destination'].strip()); return redirect('/dashboard')
@app.route('/edit', methods=['POST'])
def edit(): edit_redirect(request.form['short_id'], request.form['new_destination']); return redirect('/dashboard')
@app.route('/delete/<short_id>', methods=['POST'])
def delete(short_id): delete_redirect(short_id); \
    conn=sqlite3.connect(DB_FILE); conn.execute("DELETE FROM logs WHERE short_id=?", (short_id,)); conn.commit(); return redirect('/dashboard')

# ————————————————
# QR & CSV
# ————————————————
@app.route('/view-qr/<short_id>')
def view_qr(short_id):
    img=qrcode.make(f"{request.host_url}track?id={short_id}")
    buf=io.BytesIO(); img.save(buf); buf.seek(0)
    return send_file(buf, mimetype='image/png')

@app.route('/download-qr/<short_id>')
def download_qr(short_id):
    img=qrcode.make(f"{request.host_url}track?id={short_id}")
    buf=io.BytesIO(); img.save(buf); buf.seek(0)
    return send_file(buf, mimetype='image/png', as_attachment=True, download_name=f"{short_id}.png")

@app.route('/export-csv')
def export_csv():
    with sqlite3.connect(DB_FILE) as conn:
        rows=conn.execute("SELECT short_id,timestamp,ip,city,country,user_agent FROM logs ORDER BY timestamp DESC").fetchall()
    mem=io.StringIO(); w=csv.writer(mem)
    w.writerow(['Short Code','Timestamp','IP','City','Country','User Agent']); w.writerows(rows)
    mem.seek(0)
    return send_file(io.BytesIO(mem.getvalue().encode()), mimetype='text/csv', as_attachment=True, download_name='qr-logs.csv')

# ————————————————
# Launch
# ————————————————
if __name__=='__main__':
    # verify sheets exist
    get_sheet("QR Redirects"); get_sheet("QR Scan Archive")
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT",5000)))
