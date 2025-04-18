from flask import Flask, redirect, request, render_template, send_file
import qrcode
import io
import csv
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

app = Flask(__name__)

# === GOOGLE SHEETS SETUP ===
def get_sheet(sheet_name):
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_path = '/etc/secrets/google-credentials.json'
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    client = gspread.authorize(creds)
    return client.open(sheet_name).sheet1

def load_redirects():
    sheet = get_sheet("QR Redirects")
    data = sheet.get_all_records()
    return {row['Short Code']: row['Destination'] for row in data if row.get('Short Code') and row.get('Destination')}

def save_redirect(short_code, destination):
    sheet = get_sheet("QR Redirects")
    sheet.append_row([short_code, destination])

def update_redirect(short_code, new_dest):
    sheet = get_sheet("QR Redirects")
    data = sheet.get_all_records()
    for idx, row in enumerate(data, start=2):  # header is row 1
        if row['Short Code'] == short_code:
            sheet.update_cell(idx, 2, new_dest)

def delete_redirect(short_code):
    sheet = get_sheet("QR Redirects")
    data = sheet.get_all_records()
    for idx, row in enumerate(data, start=2):
        if row['Short Code'] == short_code:
            sheet.delete_rows(idx)
            break

def append_scan(short_code, ip, city, country, ua):
    sheet = get_sheet("QR Scan Archive")
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    sheet.append_row([short_code, timestamp, ip, city, country, ua])

def count_scans():
    sheet = get_sheet("QR Scan Archive")
    rows = sheet.get_all_records()
    stats = {}
    for row in rows:
        sid = row.get("Short Code")
        if sid:
            stats[sid] = stats.get(sid, 0) + 1
    return stats

# === ROUTES ===
@app.route('/')
def home():
    return redirect('/dashboard')

@app.route('/track')
def track():
    short_id = request.args.get('id')
    if not short_id:
        return "Missing ID", 400

    ip = request.remote_addr
    ua = request.headers.get('User-Agent', '')[:250]
    geo = {}
    try:
        geo = requests.get(f"http://ip-api.com/json/{ip}").json()
    except:
        pass

    city = geo.get("city", "")
    country = geo.get("country", "")

    redirects = load_redirects()
    dest = redirects.get(short_id)
    append_scan(short_id, ip, city, country, ua)

    if dest:
        return redirect(dest)
    else:
        return "Invalid code", 404

@app.route('/dashboard')
def dashboard():
    redirects = load_redirects()
    scans = count_scans()
    stats = []
    for code, url in redirects.items():
        stats.append((code, url, scans.get(code, 0)))
    return render_template("dashboard.html", stats=stats)

@app.route('/add', methods=['POST'])
def add():
    short = request.form['short_id'].strip()
    dest = request.form['destination'].strip()
    save_redirect(short, dest)
    return redirect('/dashboard')

@app.route('/edit', methods=['POST'])
def edit():
    short = request.form['short_id']
    new_url = request.form['new_destination']
    update_redirect(short, new_url)
    return redirect('/dashboard')

@app.route('/delete/<short_id>', methods=['POST'])
def delete(short_id):
    delete_redirect(short_id)
    return redirect('/dashboard')

@app.route('/export-csv')
def export_csv():
    sheet = get_sheet("QR Scan Archive")
    rows = sheet.get_all_values()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerows(rows)
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode()), mimetype='text/csv', as_attachment=True, download_name='qr-logs.csv')

@app.route('/view-qr/<short_id>')
def view_qr(short_id):
    url = f"{request.host_url}track?id={short_id}"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf)
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

# === RUN ===
if __name__ == '__main__':
    app.run(debug=True)
