from flask import Flask, request, redirect, render_template, send_file
from datetime import datetime
import io
import csv
import qrcode
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)
GOOGLE_CREDS_PATH = '/etc/secrets/google-credentials.json'
REDIRECT_SHEET_NAME = 'QR Redirects'
SCAN_ARCHIVE_SHEET_NAME = 'QR Scan Archive'

# === GOOGLE SHEETS HELPERS ===
def get_sheet(sheet_name):
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_CREDS_PATH, scope)
    client = gspread.authorize(creds)
    return client.open(sheet_name).sheet1

def get_redirects():
    sheet = get_sheet(REDIRECT_SHEET_NAME)
    return sheet.get_all_records()

def add_redirect(short_id, destination):
    sheet = get_sheet(REDIRECT_SHEET_NAME)
    sheet.append_row([short_id, destination])

def edit_redirect(short_id, new_url):
    sheet = get_sheet(REDIRECT_SHEET_NAME)
    records = sheet.get_all_records()
    for i, row in enumerate(records, start=2):
        if row['Short Code'] == short_id:
            sheet.update_cell(i, 2, new_url)
            break

def delete_redirect(short_id):
    sheet = get_sheet(REDIRECT_SHEET_NAME)
    records = sheet.get_all_records()
    for i, row in enumerate(records, start=2):
        if row['Short Code'] == short_id:
            sheet.delete_rows(i)
            break

def log_scan(short_id, ip, city, country, user_agent):
    sheet = get_sheet(SCAN_ARCHIVE_SHEET_NAME)
    timestamp = datetime.utcnow().isoformat()
    sheet.append_row([short_id, timestamp, ip, city, country, user_agent[:250]])

def get_logs():
    sheet = get_sheet(SCAN_ARCHIVE_SHEET_NAME)
    return sheet.get_all_records()

# === ROUTES ===
@app.route('/')
def home():
    return redirect('/dashboard')

@app.route('/dashboard')
def dashboard():
    stats = []
    locations = []

    try:
        redirect_sheet = get_sheet("QR Redirects")
        rows = redirect_sheet.get_all_records()
        for row in rows:
            short_id = row.get("Short Code")
            dest = row.get("Destination")
            scan_count = 0

            # Count matching rows in the archive
            archive = get_sheet("QR Scan Archive").get_all_records()
            scan_count = sum(1 for log in archive if log.get("Short Code") == short_id)

            stats.append((short_id, dest, scan_count))

        # Get heatmap locations
        for log in archive:
            if log.get("City") and log.get("Country"):
                lat = GEO_LOOKUP.get(log["City"], {}).get("lat")
                lon = GEO_LOOKUP.get(log["City"], {}).get("lon")
                if lat and lon:
                    locations.append([log["Short Code"], log["City"], log["Country"], lat, lon])

    except Exception as e:
        print("[DASHBOARD ERROR]", e)

    return render_template("dashboard.html", stats=stats, locations=locations)


@app.route('/track')
def track():
    short_id = request.args.get('id')
    if not short_id:
        return "Missing ID", 400

    redirects = get_redirects()
    redirect_row = next((r for r in redirects if r['Short Code'] == short_id), None)
    if not redirect_row:
        return "Invalid code", 404

    ip = request.remote_addr
    ua = request.headers.get('User-Agent', '')

    try:
        geo = requests.get(f"http://ip-api.com/json/{ip}").json()
        city = geo.get('city', '')
        country = geo.get('country', '')
    except:
        city = ''
        country = ''

    log_scan(short_id, ip, city, country, ua)
    return redirect(redirect_row['Destination'])

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

@app.route('/add', methods=['POST'])
def add():
    short = request.form['short_id'].strip()
    dest = request.form['destination'].strip()
    add_redirect(short, dest)
    return redirect('/dashboard')

@app.route('/edit', methods=['POST'])
def edit():
    short = request.form['short_id']
    new_url = request.form['new_destination']
    edit_redirect(short, new_url)
    return redirect('/dashboard')

@app.route('/delete/<short_id>', methods=['POST'])
def delete(short_id):
    delete_redirect(short_id)
    return redirect('/dashboard')

@app.route('/export-csv')
def export_csv():
    logs = get_logs()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Short Code', 'Timestamp', 'IP', 'City', 'Country', 'User Agent'])
    for log in logs:
        writer.writerow([log.get('Short Code'), log.get('Timestamp'), log.get('IP'),
                         log.get('City'), log.get('Country'), log.get('User Agent')])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode()), mimetype='text/csv', as_attachment=True, download_name='qr-logs.csv')

# === RUN ===
if __name__ == '__main__':
    app.run(debug=True)
