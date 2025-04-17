from flask import Flask, redirect, request, render_template, send_file
import qrcode
import io
import csv
import requests
import gspread
from datetime import datetime
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

# === Google Sheets Setup ===
def get_sheet(sheet_name):
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name('/etc/secrets/google-credentials.json', scope)
    client = gspread.authorize(creds)
    return client.open(sheet_name).sheet1

# === Routes ===

@app.route('/')
def home():
    return redirect('/dashboard')

@app.route('/track')
def track():
    short_id = request.args.get('id')
    if not short_id:
        return "Missing short ID", 400

    ip = request.remote_addr
    ua = request.headers.get('User-Agent', '')[:250]
    timestamp = datetime.utcnow().isoformat()

    try:
        geo = requests.get(f"http://ip-api.com/json/{ip}").json()
        city = geo.get("city", "")
        country = geo.get("country", "")
        lat = geo.get("lat", "")
        lon = geo.get("lon", "")
    except:
        city = country = lat = lon = ""

    row = [short_id, timestamp, ip, city, country, lat, lon, ua]
    print("[SCAN] →", row)

    try:
        sheet = get_sheet("QR Scan Archive")
        sheet.append_row(row)
        print("[SHEET] ✅ Row appended")
    except Exception as e:
        print("[SHEET ERROR]", e)

    try:
        redirects = get_sheet("QR Redirects").get_all_records()
        match = next((r for r in redirects if r["Short Code"] == short_id), None)
        if match:
            return redirect(match["Destination"])
    except Exception as e:
        print("[REDIRECT ERROR]", e)

    return "Invalid or missing redirect", 404

@app.route('/dashboard')
def dashboard():
    try:
        redirect_sheet = get_sheet("QR Redirects")
        log_sheet = get_sheet("QR Scan Archive")

        redirects = redirect_sheet.get_all_records()
        logs = log_sheet.get_all_records()

        stats = []
        for redirect in redirects:
            short_id = redirect["Short Code"]
            destination = redirect["Destination"]
            count = sum(1 for log in logs if log.get("Short Code") == short_id)
            stats.append((short_id, destination, count))

        heatmap_locations = []
        for log in logs:
            try:
                lat = float(log["Latitude"])
                lon = float(log["Longitude"])
                if lat and lon:
                    heatmap_locations.append([lat, lon, 0.9])
            except:
                continue

        print(f"[MAP] Loaded {len(heatmap_locations)} heatmap points")
        return render_template("dashboard.html", stats=stats, locations=heatmap_locations)

    except Exception as e:
        print("[DASHBOARD ERROR]", e)
        return "Dashboard failed to load.", 500

@app.route('/view-qr/<short_id>')
def view_qr(short_id):
    url = f"{request.host_url}track?id={short_id}"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf)
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

@app.route('/download-qr/<short_id>')
def download_qr(short_id):
    url = f"{request.host_url}track?id={short_id}"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf)
    buf.seek(0)
    return send_file(buf, mimetype='image/png', as_attachment=True, download_name=f"{short_id}-qr.png")

@app.route('/export-csv')
def export_csv():
    try:
        rows = get_sheet("QR Scan Archive").get_all_values()
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerows(rows)
        output.seek(0)
        return send_file(io.BytesIO(output.getvalue().encode()), mimetype='text/csv', as_attachment=True, download_name='qr-scan-logs.csv')
    except Exception as e:
        print("[EXPORT ERROR]", e)
        return "Export failed", 500

if __name__ == '__main__':
    app.run(debug=True)
