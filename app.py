from flask import Flask, request, redirect, render_template, send_file
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import requests
import qrcode
import io
import csv

app = Flask(__name__)

# === Google Sheets Setup ===
def get_sheet(name):
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name('/etc/secrets/google-credentials.json', scope)
    client = gspread.authorize(creds)
    return client.open(name).sheet1

def load_redirects():
    sheet = get_sheet("QR Redirects")
    rows = sheet.get_all_records()
    return {row["Short Code"]: row["Destination"] for row in rows if "Short Code" in row and "Destination" in row}

def append_scan_log(data):
    sheet = get_sheet("QR Scan Archive")
    sheet.append_row(data)

def load_logs():
    sheet = get_sheet("QR Scan Archive")
    return sheet.get_all_records()

# === Routes ===
@app.route('/')
def home():
    return redirect('/dashboard')

@app.route('/track')
def track():
    short_id = request.args.get('id')
    if not short_id:
        return "Missing ID", 400

    redirects = load_redirects()
    dest = redirects.get(short_id)
    if not dest:
        return "Invalid short code", 404

    ip = request.headers.get('X-Forwarded-For', request.remote_addr)
    ua = request.headers.get('User-Agent', '')[:250]
    timestamp = datetime.utcnow().isoformat()

    # Geo IP
    try:
        geo = requests.get(f"http://ip-api.com/json/{ip}").json()
        city = geo.get("city", "")
        country = geo.get("country", "")
    except:
        city = ""
        country = ""

    append_scan_log([short_id, timestamp, ip, city, country, ua])
    return redirect(dest)

@app.route('/dashboard')
def dashboard():
    logs = load_logs()
    redirects = load_redirects()

    # Count scans per short_id
    scan_counts = {}
    for row in logs:
        sid = row.get("Short Code", "")
        if sid:
            scan_counts[sid] = scan_counts.get(sid, 0) + 1

    # Build stats
    stats = []
    for sid, dest in redirects.items():
        stats.append([sid, dest, scan_counts.get(sid, 0)])

    # Build map locations
    locations = []
    for row in logs:
        ip = row.get("IP", "")
        short_id = row.get("Short Code", "")
        try:
            geo = requests.get(f"http://ip-api.com/json/{ip}").json()
            if geo.get("status") == "success":
                lat = geo.get("lat")
                lon = geo.get("lon")
                if lat and lon:
                    locations.append([short_id, geo.get("city", ""), lat, lon])
        except:
            continue

    return render_template("dashboard.html", stats=stats, locations=locations)

@app.route('/add', methods=['POST'])
def add():
    short = request.form['short_id'].strip()
    dest = request.form['destination'].strip()
    sheet = get_sheet("QR Redirects")
    sheet.append_row([short, dest])
    return redirect('/dashboard')

@app.route('/edit', methods=['POST'])
def edit():
    short = request.form['short_id']
    new_url = request.form['new_destination']
    sheet = get_sheet("QR Redirects")
    records = sheet.get_all_records()
    for i, row in enumerate(records):
        if row["Short Code"] == short:
            sheet.update_cell(i + 2, 2, new_url)
            break
    return redirect('/dashboard')

@app.route('/delete/<short_id>', methods=['POST'])
def delete(short_id):
    sheet = get_sheet("QR Redirects")
    records = sheet.get_all_records()
    for i, row in enumerate(records):
        if row["Short Code"] == short_id:
            sheet.delete_rows(i + 2)
            break
    return redirect('/dashboard')

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
    writer = csv.writer(output)
    writer.writerow(['Short Code', 'Timestamp', 'IP', 'City', 'Country', 'User Agent'])
    for row in logs:
        writer.writerow([
            row.get('Short Code', ''),
            row.get('Timestamp', ''),
            row.get('IP', ''),
            row.get('City', ''),
            row.get('Country', ''),
            row.get('User Agent', '')
        ])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode()), mimetype='text/csv', as_attachment=True, download_name='qr-logs.csv')

# === Run ===
if __name__ == '__main__':
    app.run(debug=True, port=5000)
