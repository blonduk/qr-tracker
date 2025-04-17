from flask import Flask, redirect, request, render_template, send_file
import io
import qrcode
import requests
from datetime import datetime, timedelta
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

# === Google Sheets Setup ===
SCOPE = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
CREDS_PATH = '/etc/secrets/google-credentials.json'
ARCHIVE_SHEET_NAME = 'QR Scan Archive'
REDIRECTS_SHEET_NAME = 'QR Redirects'

def get_gspread_client():
    creds = ServiceAccountCredentials.from_json_keyfile_name(CREDS_PATH, SCOPE)
    return gspread.authorize(creds)

# === Caching Layer ===
cache = {
    'redirects': {'data': [], 'timestamp': datetime.min},
    'logs': {'data': [], 'timestamp': datetime.min}
}
CACHE_DURATION = timedelta(seconds=30)

def load_redirects():
    if datetime.utcnow() - cache['redirects']['timestamp'] < CACHE_DURATION:
        return cache['redirects']['data']
    try:
        client = get_gspread_client()
        sheet = client.open(REDIRECTS_SHEET_NAME).sheet1
        records = sheet.get_all_records()
        cache['redirects'] = {'data': records, 'timestamp': datetime.utcnow()}
        return records
    except Exception as e:
        print("[REDIRECTS CACHE ERROR]", e)
        return cache['redirects']['data']

def load_logs():
    if datetime.utcnow() - cache['logs']['timestamp'] < CACHE_DURATION:
        return cache['logs']['data']
    try:
        client = get_gspread_client()
        sheet = client.open(ARCHIVE_SHEET_NAME).sheet1
        records = sheet.get_all_records()
        cache['logs'] = {'data': records, 'timestamp': datetime.utcnow()}
        return records
    except Exception as e:
        print("[LOGS CACHE ERROR]", e)
        return cache['logs']['data']

# === Routes ===
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
        if geo.get('status') == 'success':
            city = geo.get("city", "")
            country = geo.get("country", "")
        else:
            city = ""
            country = ""
    except:
        city = ""
        country = ""

    # Save to Google Sheets
    try:
        client = get_gspread_client()
        sheet = client.open(ARCHIVE_SHEET_NAME).sheet1
        sheet.append_row([short_id, timestamp, ip, city, country, ua])
        cache['logs']['timestamp'] = datetime.min  # force refresh on next dashboard view
    except Exception as e:
        print("[APPEND ERROR]", e)

    # Redirect
    redirects = load_redirects()
    match = next((r for r in redirects if r.get('short_id') == short_id), None)
    if match:
        return redirect(match.get('destination', '/'))
    return "Invalid short code", 404

@app.route('/dashboard')
def dashboard():
    redirects = load_redirects()
    logs = load_logs()

    stats = []
    for r in redirects:
        sid = r.get('short_id', '')
        dest = r.get('destination', '')
        scan_count = sum(1 for l in logs if l.get('Short Code') == sid)
        stats.append((sid, dest, scan_count))

    # Prepare heatmap points
    locations = []
    for log in logs:
        try:
            if log['City'] and log['Country']:
                city = log['City']
                country = log['Country']
                geo = requests.get(f"http://ip-api.com/json/{log['IP']}").json()
                if geo.get("status") == "success":
                    lat = geo.get("lat")
                    lon = geo.get("lon")
                    if lat and lon:
                        locations.append([log['Short Code'], city, lat, lon])
        except Exception as e:
            print("[GEO FAIL]", e)

    return render_template("dashboard.html", stats=stats, locations=locations)

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
    logs = load_logs()
    output = io.StringIO()
    output.write("Short Code,Timestamp,IP,City,Country,User Agent\n")
    for row in logs:
        output.write(','.join([
            row.get("Short Code", ""),
            row.get("Timestamp", ""),
            row.get("IP", ""),
            row.get("City", ""),
            row.get("Country", ""),
            row.get("User Agent", "").replace(",", " ")
        ]) + "\n")
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode()), mimetype='text/csv', as_attachment=True, download_name="qr-logs.csv")

if __name__ == '__main__':
    app.run(debug=True)
