from flask import Flask, request, redirect, render_template, session, send_file, url_for
import qrcode
import io
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

app = Flask(__name__)
app.secret_key = "your_secret_key_here"  # Replace with something strong

# Users
USERS = {
    "Laurence2k": "qrtracker69",
    "Jack": "artoneggs"
}

# === GOOGLE SHEETS SETUP ===
def get_sheet(sheet_name):
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_path = '/etc/secrets/google-credentials.json'
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    client = gspread.authorize(creds)
    return client.open(sheet_name).sheet1

# === LOGIN ===
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = request.form['username']
        pw = request.form['password']
        if USERS.get(user) == pw:
            session['user'] = user
            return redirect('/dashboard')
        else:
            return "Invalid credentials", 401
    return render_template("login.html")

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# === TRACK ===
@app.route('/track')
def track():
    short_id = request.args.get('id')
    user = request.args.get('user')
    if not short_id or not user:
        return "Missing ID or user", 400

    ua = request.headers.get('User-Agent', '')[:250]
    ip = request.remote_addr
    timestamp = datetime.utcnow().isoformat()

    # Geolocation fallback (null for now)
    city = ""
    country = ""

    try:
        sheet = get_sheet("QR Redirects")
        dest = None
        for row in sheet.get_all_records():
            if row['User'] == user and row['Short Code'] == short_id:
                dest = row['Destination']
                break
        if not dest:
            return "Code not found", 404
    except Exception as e:
        print("[ERROR]", e)
        return "Error", 500

    try:
        log_sheet = get_sheet("QR Scan Archive")
        log_sheet.append_row([user, short_id, timestamp, ip, city, country, ua])
    except Exception as e:
        print("[LOG ERROR]", e)

    return redirect(dest)

# === DASHBOARD ===
@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        return redirect('/login')
    user = session['user']

    try:
        sheet = get_sheet("QR Redirects")
        stats = [row for row in sheet.get_all_records() if row['User'] == user]
    except Exception as e:
        print("[DASH ERROR]", e)
        stats = []

    try:
        logs = get_sheet("QR Scan Archive").get_all_records()
        scans = {row['Short Code']: 0 for row in stats}
        for row in logs:
            if row['User'] == user and row['Short Code'] in scans:
                scans[row['Short Code']] += 1
    except Exception as e:
        print("[SCAN COUNT ERROR]", e)
        scans = {}

    return render_template("dashboard.html", stats=stats, scans=scans, user=user)

# === ADD CODE ===
@app.route('/add', methods=['POST'])
def add():
    if 'user' not in session:
        return redirect('/login')
    user = session['user']
    short = request.form['short_id'].strip()
    dest = request.form['destination'].strip()
    try:
        sheet = get_sheet("QR Redirects")
        sheet.append_row([user, short, dest])
    except Exception as e:
        print("[ADD ERROR]", e)
    return redirect('/dashboard')

# === DELETE CODE ===
@app.route('/delete/<short_id>', methods=['POST'])
def delete(short_id):
    if 'user' not in session:
        return redirect('/login')
    user = session['user']
    try:
        sheet = get_sheet("QR Redirects")
        rows = sheet.get_all_records()
        for i, row in enumerate(rows, start=2):
            if row['User'] == user and row['Short Code'] == short_id:
                sheet.delete_rows(i)
                break
    except Exception as e:
        print("[DELETE ERROR]", e)
    return redirect('/dashboard')

# === QR VIEW ===
@app.route('/view-qr/<short_id>')
def view_qr(short_id):
    if 'user' not in session:
        return redirect('/login')
    user = session['user']
    url = f"{request.host_url}track?id={short_id}&user={user}"
    img = qrcode.make(url)
    buf = io.BytesIO()
    img.save(buf)
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

# === HOME ===
@app.route('/')
def home():
    return redirect('/dashboard')

if __name__ == '__main__':
    app.run(debug=True)

@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404

