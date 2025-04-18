from flask import Flask, request, redirect, render_template, send_file, session, url_for
import qrcode
import io
import csv
import os
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'qr_secret_key_2025'

# === Google Sheets Setup ===
def get_sheet(sheet_name):
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_path = 'etc/secrets/google-credentials.json'
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    client = gspread.authorize(creds)
    return client.open(sheet_name).sheet1

# === Hardcoded users ===
USERS = {
    "Laurence2k": "qrtracker69",
    "Jack": "artoneggs"
}

# === Routes ===

@app.route('/')
def home():
    return redirect('/login')

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        user = request.form['username']
        pwd = request.form['password']
        if user in USERS and USERS[user] == pwd:
            session['user'] = user
            return redirect('/dashboard')
        else:
            error = 'Invalid credentials'
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect('/login')

@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        return redirect('/login')

    user = session['user']
    try:
        redirect_sheet = get_sheet("QR Redirects")
        scan_sheet = get_sheet("QR Scan Archive")

        redirects = [row for row in redirect_sheet.get_all_records() if row['User'] == user]
        logs = [row for row in scan_sheet.get_all_records() if row['Short Code'] in [r['Short Code'] for r in redirects]]

        scan_counts = {}
        for row in logs:
            sid = row['Short Code']
            scan_counts[sid] = scan_counts.get(sid, 0) + 1

        stats = []
        for r in redirects:
            code = r['Short Code']
            stats.append([code, r['Destination'], scan_counts.get(code, 0)])

    except Exception as e:
        print("[DASHBOARD ERROR]", e)
        stats = []

    return render_template("dashboard.html", stats=stats)

@app.route('/track')
def track():
    short_id = request.args.get('id')
    if not short_id:
        return "Missing ID", 400

    try:
        redirect_sheet = get_sheet("QR Redirects")
        row = next((r for r in redirect_sheet.get_all_records() if r['Short Code'] == short_id), None)
        if not row:
            return "Invalid short code", 404

        ip = request.remote_addr
        ua = request.headers.get('User-Agent', '')[:250]
        timestamp = datetime.utcnow()

        try:
            geo = requests.get(f"http://ip-api.com/json/{ip}").json()
            city = geo.get("city", "")
            country = geo.get("country", "")
        except:
            city = ""
            country = ""

        scan_sheet = get_sheet("QR Scan Archive")
        scan_sheet.append_row([short_id, str(timestamp), ip, city, country, ua])

        return redirect(row['Destination'])

    except Exception as e:
        print("[TRACK ERROR]", e)
        return "Server error", 500

@app.route('/add', methods=['POST'])
def add():
    if 'user' not in session:
        return redirect('/login')

    short = request.form['short_id'].strip()
    dest = request.form['destination'].strip()
    user = session['user']
    try:
        sheet = get_sheet("QR Redirects")
        sheet.append_row([short, dest, user])
    except Exception as e:
        print("[ADD ERROR]", e)
    return redirect('/dashboard')

@app.route('/edit', methods=['POST'])
def edit():
    if 'user' not in session:
        return redirect('/login')

    short_id = request.form['short_id']
    new_dest = request.form['new_destination']
    try:
        sheet = get_sheet("QR Redirects")
        records = sheet.get_all_records()
        for i, row in enumerate(records, start=2):
            if row['Short Code'] == short_id:
                sheet.update_cell(i, 2, new_dest)
                break
    except Exception as e:
        print("[EDIT ERROR]", e)
    return redirect('/dashboard')

@app.route('/delete/<short_id>', methods=['POST'])
def delete(short_id):
    if 'user' not in session:
        return redirect('/login')

    try:
        sheet = get_sheet("QR Redirects")
        records = sheet.get_all_records()
        for i, row in enumerate(records, start=2):
            if row['Short Code'] == short_id:
                sheet.delete_rows(i)
                break
    except Exception as e:
        print("[DELETE ERROR]", e)
    return redirect('/dashboard')

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
    if 'user' not in session:
        return redirect('/login')

    user = session['user']
    try:
        scan_sheet = get_sheet("QR Scan Archive")
        logs = [row for row in scan_sheet.get_all_records() if row['Short Code'] in [r['Short Code'] for r in get_sheet("QR Redirects").get_all_records() if r['User'] == user]]

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

    except Exception as e:
        print("[EXPORT ERROR]", e)
        return "Export failed", 500

# === 404 Error Page ===
@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404

# === Run the app ===
if __name__ == '__main__':
    app.run(debug=True)
