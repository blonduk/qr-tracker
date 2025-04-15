from flask import Flask, redirect, request, render_template, send_file
from datetime import datetime
import sqlite3
import os
import qrcode
import io
import csv
import requests
import gspread
from oauth2client.service_account import ServiceAccountCredentials

app = Flask(__name__)
DB_FILE = 'redirects.db'

def get_sheet():
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds_path = '/etc/secrets/google-credentials.json'
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    client = gspread.authorize(creds)
    sheet = client.open("QR Scan Archive").sheet1
    return sheet

def append_to_sheet(data):
    try:
        print(f"[SHEET] Attempting to write row: {data}")
        sheet = get_sheet()
        sheet.append_row(data)
        print("[SHEET] ✅ Row appended successfully")
    except Exception as e:
        print("[SHEET ERROR]", e)

@app.route('/test-sheets')
def test_sheets():
    try:
        append_to_sheet(["test", str(datetime.utcnow()), "ip", "city", "country", "agent"])
        return "✅ Google Sheets write succeeded!"
    except Exception as e:
        return f"❌ Sheets error: {e}"

@app.route('/log-test')
def log_test():
    print("[TEST] This is a log test from /log-test")
    return "✅ Log test triggered"

@app.route('/')
def home():
    return redirect('/dashboard')


def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                short_id TEXT,
                timestamp DATETIME,
                user_agent TEXT,
                ip TEXT,
                city TEXT,
                country TEXT,
                lat REAL,
                lon REAL
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS redirects (
                short_id TEXT PRIMARY KEY,
                destination TEXT
            )
        ''')
        conn.execute("INSERT OR IGNORE INTO redirects (short_id, destination) VALUES (?, ?)", ("blondart", "https://www.blondart.co.uk"))

@app.route('/track')
def track():
    short_id = request.args.get('id')
    if not short_id:
        print("[TRACK] No shortcode provided.")
        return "Missing tracking ID", 400

    user_agent = request.headers.get('User-Agent', '').replace('\n', ' ').replace('\r', ' ')[:250]
    ip = request.remote_addr
    timestamp = datetime.utcnow()

    # Geo lookup
    try:
        geo = requests.get(f"http://ip-api.com/json/{ip}").json()
        print("[GEO] Raw response:", geo)
        city = geo.get('city', '')
        country = geo.get('country', '')
        lat = geo.get('lat', 0)
        lon = geo.get('lon', 0)
    except Exception as geo_err:
        print("[GEO ERROR]", geo_err)
        city, country = '', ''
        lat, lon = 0, 0

    print(f"[TRACK] Logging scan: {short_id}, IP: {ip}, Location: {city}, {country}")

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO logs (short_id, timestamp, user_agent, ip, city, country, lat, lon)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (short_id, timestamp, user_agent, ip, city, country, lat, lon))
        conn.commit()
        dest = cursor.execute("SELECT destination FROM redirects WHERE short_id = ?", (short_id,)).fetchone()

    sheet_row = [short_id, str(timestamp), ip, city, country, user_agent]
    print(f"[TRACK] About to write this row to Google Sheet: {sheet_row}")
    try:
        append_to_sheet(sheet_row)
        print("[TRACK] ✅ Sheet write successful")
    except Exception as sheet_error:
        print("[TRACK] ❌ Sheet write FAILED:", sheet_error)

    if dest:
        return redirect(dest[0])
    else:
        return "Invalid tracking code", 404

# === For Render startup ===
if not os.path.exists(DB_FILE):
    init_db()
