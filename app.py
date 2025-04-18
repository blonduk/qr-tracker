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

# --- USERS ---
USERS = {
    "Laurence2k": "qrtracker69",
    "Jack": "artoneggs"
}

# --- GOOGLE SHEETS ---
def get_sheet(name):
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_path = '/etc/secrets/google-credentials.json'
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    client = gspread.authorize(creds)
    return client.open(name).sheet1

def load_redirects():
    return get_sheet("QR Redirects").get_all_records()

def load_logs():
    return get_sheet("QR Scan Archive").get_all_records()

# --- ROUTES ---
@app.route('/')
def home():
    if 'user' not in session:
        return redirect('/login')
    return redirect('/dashboard')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u = request.form['username']
        p = request.form['password']
        if u in USERS and USERS[u] == p:
            session['user'] = u
            return redirect('/dashboard')
        return render_template('login.html', error='Invalid credentials')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

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

    return render_template('dashboard.html', stats=stats, user=user)

@app.route('/add', methods=['POST'])
def add():
    if 'user' not in session:
        return redirect('/login')
    sheet = get_sheet("QR Redirects")
    sheet.append_row([
        request.form['short_id'].strip(),
        request.form['destination'].strip(),
        session['user']
    ])
    return redirect('/dashboard')

@app.route('/edit', methods=['POST'])
def edit():
    if 'user' not in session:
        return redirect('/login')
    short = request.form['short_id']
    new_url = request.form['new_destination']
    sheet = get_sheet("QR Redirects")
    rows = sheet.get_all_records()
    for i, row in enumerate(rows, start=2):
        if row['Short Code'] == short:
            sheet.update_cell(i, 2, new_url)
            break
    return redirect('/dashboard')

@app.route('/delete/<short_id>', methods=['POST'])
def delete(short_id):
    if 'user' not in session:
        return redirect('/login')
    sheet = get_sheet("QR Redirects")
    rows = sheet.get_all_records()
    for i, row in enumerate(rows, start=2):
        if row['Short Code'] == short_id:
            sheet.delete_rows(i)
            break
    return redirect('/dashboard')

@app.route('/track')
def track():
    sid = request.args.get('id')
    if not sid:
        return "Missing ID", 400
    logs = get_sheet("QR Scan Archive")
    redirects = get_sheet("QR Redirects").get_all_records()

    match = next((r for r in redirects if r['Short Code'] == sid), None)
    if not match:
        return "Invalid code", 404

    ip = request.remote_addr
    ua = request.headers.get('User-Agent', '')[:250]
    ts = datetime.utcnow().isoformat()
    logs.append_row([sid, ts, ip, '', '', ua])

    return redirect(match['Destination'])

@app.route('/view-qr/<short_id>')
def view_qr(short_id):
    url = f"{request.host_url}track?id={short_id}"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

@app.route('/download-qr/<short_id>.png')
def download_png(short_id):
    url = f"{request.host_url}track?id={short_id}"
    qr = qrcode.QRCode(box_size=10, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").resize((1000, 1000))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    filename = f"glitchlink-{short_id}-qr.png"
    return send_file(buf, mimetype="image/png", as_attachment=True, download_name=filename)

@app.route('/download-qr/<short_id>.svg')
def download_svg(short_id):
    url = f"{request.host_url}track?id={short_id}"
    qr = qrcode.make(url)
    size = 1000
    dwg = svgwrite.Drawing(size=(size, size))
    module_count = len(qr.get_matrix())
    box_size = size // module_count

    for y, row in enumerate(qr.get_matrix()):
        for x, val in enumerate(row):
            if val:
                dwg.add(dwg.rect(insert=(x * box_size, y * box_size), size=(box_size, box_size), fill='black'))

    buf = io.BytesIO()
    dwg.write(buf)
    buf.seek(0)
    filename = f"glitchlink-{short_id}-qr.svg"
    return send_file(buf, mimetype="image/svg+xml", as_attachment=True, download_name=filename)

@app.route('/export-csv')
def export_csv():
    if 'user' not in session:
        return redirect('/login')
    user = session['user']
    logs = load_logs()
    codes = [r['Short Code'] for r in load_redirects() if r['User'] == user]
    user_logs = [l for l in logs if l['Short Code'] in codes]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Short Code', 'Timestamp', 'IP', 'City', 'Country', 'User Agent'])
    for l in user_logs:
        writer.writerow([l['Short Code'], l['Timestamp'], l['IP'], l.get('City', ''), l.get('Country', ''), l['User Agent']])
    output.seek(0)
    return send_file(io.BytesIO(output.getvalue().encode()), mimetype='text/csv',
                     as_attachment=True, download_name=f'glitchlink-{user}-logs.csv')

@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html"), 404

if __name__ == '__main__':
    app.run(debug=True)
