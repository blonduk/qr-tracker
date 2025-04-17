from flask import Flask, redirect, request, render_template, send_file
import qrcode
import io
from datetime import datetime
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

# === Google Sheets Setup ===
def get_sheet(sheet_name):
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name('/etc/secrets/google-credentials.json', scope)
    client = gspread.authorize(creds)
    return client.open(sheet_name).sheet1

def get_redirects():
    sheet = get_sheet("QR Redirects")
    return sheet.get_all_records()

def get_logs():
    sheet = get_sheet("QR Scan Archive")
    return sheet.get_all_records()

def add_redirect(short, dest):
    sheet = get_sheet("QR Redirects")
    sheet.append_row([short, dest])

def update_redirect(short, new_url):
    sheet = get_sheet("QR Redirects")
    cell = sheet.find(short)
    sheet.update_cell(cell.row, 2, new_url)

def delete_redirect(short):
    sheet = get_sheet("QR Redirects")
    cell = sheet.find(short)
    sheet.delete_rows(cell.row)

def append_log(data):
    sheet = get_sheet("QR Scan Archive")
    sheet.append_row(data)

# === ROUTES ===
@app.route('/')
def home():
    return redirect('/dashboard')

@app.route('/track')
def track():
    short_id = request.args.get('id')
    if not short_id:
        return "Missing ID", 400

    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    ua = request.headers.get('User-Agent', '')[:250]
    timestamp = datetime.utcnow().isoformat()

    try:
        geo = requests.get(f"http://ip-api.com/json/{ip}").json()
        city = geo.get("city", "")
        country = geo.get("country", "")
    except:
        city = ""
        country = ""

    append_log([short_id, timestamp, ip, city, country, ua])

    redirects = get_redirects()
    for row in redirects:
        if row['short_id'] == short_id:
            return redirect(row['destination'])

    return "Invalid short code", 404

@app.route('/dashboard')
def dashboard():
    redirects = get_redirects()
    logs = get_logs()

    scan_counts = {}
    for log in logs:
        sid = log.get('Short Code')
        if sid:
            scan_counts[sid] = scan_counts.get(sid, 0) + 1

    stats = []
    for row in redirects:
        sid = row['short_id']
        dest = row['destination']
        count = scan_counts.get(sid, 0)
        stats.append((sid, dest, count))

    heatmap = []
    for log in logs:
        try:
            lat = float(log.get('lat', 0))
            lon = float(log.get('lon', 0))
            if lat and lon:
                heatmap.append([lat, lon, 0.8])
        except:
            continue

    return render_template("dashboard.html", stats=stats, locations=heatmap)

@app.route('/view-qr/<short_id>')
def view_qr(short_id):
    url = f"{request.host_url}track?id={short_id}"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf)
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

@app.route('/add', methods=['POST'])
def add():
    short = request.form['short_id'].strip()
    dest = request.form['destination'].strip()
    add_redirect(short, dest)
    return redirect('/dashboard')

@app.route('/edit', methods=['POST'])
def edit():
    short = request.form['short_id'].strip()
    new_url = request.form['new_destination'].strip()
    update_redirect(short, new_url)
    return redirect('/dashboard')

@app.route('/delete/<short_id>', methods=['POST'])
def delete(short_id):
    delete_redirect(short_id)
    return redirect('/dashboard')
