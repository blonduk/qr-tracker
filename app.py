
from flask import Flask, request, redirect, render_template, session, send_file, abort
import qrcode
import io
import csv
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import os
import svgwrite

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

USERS = {
    "Laurence2k": "qrtracker69",
    "Jack": "artoneggs"
}

def get_sheet(name):
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_path = '/etc/secrets/google-credentials.json'
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    client = gspread.authorize(creds)
    return client.open(name).sheet1

def load_redirects():
    sheet = get_sheet("QR Redirects")
    return sheet.get_all_records()

def load_logs():
    sheet = get_sheet("QR Scan Archive")
    return sheet.get_all_records()

@app.route('/')
def home():
    if 'user' not in session:
        return redirect('/login')
    return redirect('/dashboard')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = request.form['username']
        pw = request.form['password']
        if user in USERS and USERS[user] == pw:
            session['user'] = user
            return redirect('/dashboard')
        return render_template('login.html', error='Invalid credentials')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return render_template('logout.html')

@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        return redirect('/login')
    user = session['user']
    redirects = [r for r in load_redirects() if r['User'] == user]
    logs = load_logs()
    stats = []
    for r in redirects:
        sid = r['Short Code']
        dest = r['Destination']
        count = sum(1 for log in logs if log['Short Code'] == sid)
        stats.append((sid, dest, count))
    return render_template('dashboard.html', stats=stats, user=user, now=datetime.utcnow())

@app.route('/add', methods=['POST'])
def add():
    if 'user' not in session:
        return redirect('/login')
    short = request.form['short_id'].strip()
    dest = request.form['destination'].strip()
    user = session['user']
    sheet = get_sheet("QR Redirects")
    sheet.append_row([short, dest, user])
    return redirect('/dashboard')

@app.route('/edit', methods=['POST'])
def edit():
    if 'user' not in session:
        return redirect('/login')
    short = request.form['short_id'].strip()
    new_url = request.form['new_destination'].strip()
    sheet = get_sheet("QR Redirects")
    records = sheet.get_all_records()
    for i, row in enumerate(records, start=2):
        if row['Short Code'] == short:
            sheet.update_cell(i, 2, new_url)
            break
    return redirect('/dashboard')

@app.route('/delete/<short_id>', methods=['POST'])
def delete(short_id):
    if 'user' not in session:
        return redirect('/login')
    sheet = get_sheet("QR Redirects")
    records = sheet.get_all_records()
    for i, row in enumerate(records, start=2):
        if row['Short Code'] == short_id:
            sheet.delete_rows(i)
            break
    return redirect('/dashboard')

@app.route('/track')
def track():
    short_id = request.args.get('id')
    if not short_id:
        return "Missing ID", 400
    ip = request.remote_addr
    ua = request.headers.get('User-Agent', '')[:250]
    timestamp = datetime.utcnow().isoformat()
    redirect_sheet = get_sheet("QR Redirects")
    match = None
    for row in redirect_sheet.get_all_records():
        if row['Short Code'] == short_id:
            match = row
            break
    if not match:
        return "Invalid code", 404
    scan_sheet = get_sheet("QR Scan Archive")
    scan_sheet.append_row([short_id, timestamp, ip, '', '', ua])
    return redirect(match['Destination'])

@app.route('/view-qr/<short_id>')
def view_qr(short_id):
    url = f"{request.host_url}track?id={short_id}"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf)
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

@app.route('/download-png/<short_id>')
def download_png(short_id):
    url = f"{request.host_url}track?id={short_id}"
    qr = qrcode.QRCode(box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").resize((1000, 1000))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png', as_attachment=True, download_name=f"{short_id}_glitchlink.png")

@app.route('/download-svg/<short_id>')
def download_svg(short_id):
    url = f"{request.host_url}track?id={short_id}"
    qr = qrcode.QRCode(box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    size = len(matrix)
    dwg = svgwrite.Drawing(size=(size * 10, size * 10))
    for y, row in enumerate(matrix):
        for x, cell in enumerate(row):
            if cell:
                dwg.add(dwg.Rect(insert=(x * 10, y * 10), size=(10, 10), fill='black'))
    buf = io.BytesIO(dwg.tostring().encode())
    return send_file(buf, mimetype='image/svg+xml', as_attachment=True, download_name=f"{short_id}_glitchlink.svg")

@app.route('/export-csv')
def export_csv():
    if 'user' not in session:
        return redirect('/login')
    user = session['user']
    logs = load_logs()
    user_codes = [r['Short Code'] for r in load_redirects() if r['User'] == user]
    filtered = [log for log in logs if log['Short Code'] in user_codes]
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Short Code', 'Timestamp', 'IP', 'City', 'Country', 'User Agent'])
    for row in filtered:
        writer.writerow([
            row['Short Code'], row['Timestamp'], row['IP'], row.get('City', ''),
            row.get('Country', ''), row['User Agent']
        ])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode()), mimetype='text/csv',
                     as_attachment=True, download_name=f"{user}_glitchlink_logs.csv")

@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404

if __name__ == '__main__':
    app.run(debug=True)
