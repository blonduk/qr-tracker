from flask import Flask, redirect, request, render_template
from datetime import datetime
import sqlite3
import os

app = Flask(__name__)

DB_FILE = 'redirects.db'

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                short_id TEXT,
                timestamp DATETIME,
                user_agent TEXT,
                ip TEXT
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS redirects (
                short_id TEXT PRIMARY KEY,
                destination TEXT
            )
        ''')
        # Example data
        conn.execute("INSERT OR IGNORE INTO redirects (short_id, destination) VALUES (?, ?)", ("blondart", "https://www.blondart.co.uk"))
        conn.execute("INSERT OR IGNORE INTO redirects (short_id, destination) VALUES (?, ?)", ("insta", "https://www.instagram.com/blondart"))

@app.route('/track')
def track():
    short_id = request.args.get('id')
    user_agent = request.headers.get('User-Agent')
    ip = request.remote_addr
    timestamp = datetime.utcnow()

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("INSERT INTO logs (short_id, timestamp, user_agent, ip) VALUES (?, ?, ?, ?)", (short_id, timestamp, user_agent, ip))
        conn.commit()

        dest = cursor.execute("SELECT destination FROM redirects WHERE short_id = ?", (short_id,)).fetchone()

    if dest:
        return redirect(dest[0])
    else:
        return "Invalid tracking code", 404

@app.route('/dashboard')
def dashboard():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT short_id, COUNT(*) FROM logs GROUP BY short_id")
        stats = cursor.fetchall()
        return render_template('dashboard.html', stats=stats)

if __name__ == '__main__':
    if not os.path.exists(DB_FILE):
        init_db()
    app.run(host='0.0.0.0', port=5000)
