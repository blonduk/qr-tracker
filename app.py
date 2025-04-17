from flask import Flask, request, redirect, render_template, send_file
from datetime import datetime
import qrcode
import io
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)

# === GOOGLE SHEETS SETUP ===
def get_sheet(sheet_name):
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name('/etc/secrets/google-credentials.json', scope)
    client = gspread.authorize(creds)
    return client.open(sheet_name).sheet1

def append_log_row(data):
    try:
        sheet = get_sheet("QR Scan Archive")
        sheet.append_row(data)
        print("[SHEET ✅] Log row added.")
    except Exception as e:
        print("[SHEET ❌] Error appending:", e)

def fetch_redirects():
    sheet = get_sheet("QR Redirects")
    return sheet.get_all_records()

def fetch_logs():
    sheet = get_sheet("QR Scan Archive")
    return sheet.get_all_records()

# === MAIN ROUTES ===
@app.route('/')
def home():
    return redirect('/dashboard')

@app.route('/track')
def track():
    short_id = request.args.get('id')
    if not short_id:
        return "Missing ID", 400

    ip = request.remote_addr or request.headers.get('X-Forwarded-For', '').split(',')[0]
    ua = request.headers.get('User-Agent', '')[:250]
    timestamp = datetime.utcnow().isoformat()

    try:
        geo = requests.get(f"http://ip-api.com/json/{ip}").json()
        city = geo.get("city", "")
        country = geo.get("country", "")
    except:
        city = ""
        country = ""

    row = [short_id, timestamp, ip, city, country, ua]
    append_log_row(row)

    for entry in fetch_redirects():
        if entry['short_id'] == short_id:
            return redirect(entry['destination'])

    return "Invalid short code", 404

@app.route('/dashboard')
def dashboard():
    redirects = fetch_redirects()
    logs = fetch_logs()

    stats = []
    heatmap_points = []

    for redir in redirects:
        sid = redir['short_id']
        dest = redir['destination']
        count = sum(1 for log in logs if log['Short Code'] == sid)
        stats.append((sid, dest, count))

    for log in logs:
        try:
            city = log['City']
            country = log['Country']
            if city or country:
                ip = log['IP']
                geo = requests.get(f"http://ip-api.com/json/{ip}").json()
                if geo['status'] == 'success':
                    lat, lon = geo['lat'], geo['lon']
                    heatmap_points.append((log['Short Code'], lat, lon))
        except Exception as e:
            print("[MAP GEO ERROR]", e)

    return render_template("dashboard.html", stats=stats, locations=heatmap_points)

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
    sheet = get_sheet("QR Redirects")
    sheet.append_row([short, dest])
    return redirect('/dashboard')

@app.route('/edit', methods=['POST'])
def edit():
    short = request.form['short_id']
    new_url = request.form['new_destination']
    sheet = get_sheet("QR Redirects")
    cell = sheet.find(short)
    sheet.update_cell(cell.row, 2, new_url)
    return redirect('/dashboard')

@app.route('/delete/<short_id>', methods=['POST'])
def delete(short_id):
    sheet = get_sheet("QR Redirects")
    cell = sheet.find(short_id)
    sheet.delete_rows(cell.row)
    return redirect('/dashboard')

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000)
