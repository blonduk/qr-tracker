from flask import Flask, redirect, request, render_template, send_file
from datetime import datetime
import io
import csv
import qrcode
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

# === GOOGLE SHEETS ===
def get_sheet(sheet_name):
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name('/etc/secrets/google-credentials.json', scope)
    client = gspread.authorize(creds)
    return client.open(sheet_name).sheet1

def load_redirects():
    sheet = get_sheet("QR Redirects")
    data = sheet.get_all_records()
    return {row['Short Code']: row['Destination'] for row in data if row['Short Code'] and row['Destination']}

def log_scan(short_id, ip, city, country, ua):
    sheet = get_sheet("QR Scan Archive")
    timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    row = [short_id, timestamp, ip, city, country, ua]
    try:
        sheet.append_row(row)
        print("[SCAN LOGGED ✅]", row)
    except Exception as e:
        print("[❌ LOG ERROR]", e)

# === ROUTES ===
@app.route('/')
def home():
    return redirect('/dashboard')

@app.route('/track')
def track():
    short_id = request.args.get('id')
    if not short_id:
        return "Missing QR ID", 400

    redirects = load_redirects()
    destination = redirects.get(short_id)
    if not destination:
        return "Invalid QR code", 404

    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    ua = request.headers.get('User-Agent', '')[:250]

    try:
        geo = requests.get(f"http://ip-api.com/json/{ip}").json()
        city = geo.get("city", "")
        country = geo.get("country", "")
    except:
        city = ""
        country = ""

    log_scan(short_id, ip, city, country, ua)
    return redirect(destination)

@app.route('/dashboard')
def dashboard():
    redirects = load_redirects()
    logs = get_sheet("QR Scan Archive").get_all_records()

    stats = {}
    for short_id in redirects:
        stats[short_id] = {
            'destination': redirects[short_id],
            'scans': 0
        }

    locations = []
    for log in logs:
        sid = log.get('Short Code')
        if sid in stats:
            stats[sid]['scans'] += 1
        city = log.get('City', '')
        country = log.get('Country', '')
        if city and country:
            try:
                geo = requests.get(f"https://nominatim.openstreetmap.org/search", params={
                    'city': city,
                    'country': country,
                    'format': 'json',
                    'limit': 1
                }, headers={'User-Agent': 'qr-tracker'}).json()
                if geo:
                    lat = float(geo[0]['lat'])
                    lon = float(geo[0]['lon'])
                    locations.append([sid, city, country, lat, lon])
            except:
                continue

    rows = [(sid, stats[sid]['destination'], stats[sid]['scans']) for sid in stats]

    return render_template("dashboard.html", stats=rows, locations=locations)

@app.route('/view-qr/<short_id>')
def view_qr(short_id):
    url = f"{request.host_url}track?id={short_id}"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf)
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

@app.route('/export-csv')
def export_csv():
    sheet = get_sheet("QR Scan Archive")
    logs = sheet.get_all_values()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerows(logs)
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode()), mimetype='text/csv', as_attachment=True, download_name='qr-logs.csv')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
