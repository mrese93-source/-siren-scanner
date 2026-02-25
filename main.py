import requests
import os
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# Railway provides PORT dynamically. Must listen on it.
PORT = int(os.environ.get("PORT", "8080"))

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, format, *args):
        # silence default HTTP logs
        return

def run_server():
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"HTTP server listening on 0.0.0.0:{PORT}", flush=True)
    server.serve_forever()

def send_telegram(msg: str):
    try:
        if not TELEGRAM_TOKEN or not CHAT_ID:
            print("Missing TELEGRAM_TOKEN or CHAT_ID", flush=True)
            return
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        print("Telegram error:", e, flush=True)

def scan():
    print("Scanning...", flush=True)
    # שים פה את הלוגיקה שלך לסריקה (בינתיים דוגמה קטנה)
    # send_telegram("Scanner is alive ✅")

print("Starting scanner...", flush=True)

# start web server in background (so Railway keeps container alive)
threading.Thread(target=run_server, daemon=True).start()

# main loop
while True:
    try:
        scan()
    except Exception as e:
        print("Error:", e, flush=True)
    time.sleep(180)
