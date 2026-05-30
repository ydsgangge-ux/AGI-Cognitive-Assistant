"""
VRM Model Test Server
Open http://localhost:8899 in browser after running to see the effect
"""
import http.server
import threading
import webbrowser
import os
import sys

PORT = 8899
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

if not os.path.isdir(STATIC_DIR):
    print(f"[VRM] static dir not found: {STATIC_DIR}")
    sys.exit(1)

os.chdir(STATIC_DIR)

handler = http.server.SimpleHTTPRequestHandler
httpd = http.server.HTTPServer(("127.0.0.1", PORT), handler)
print(f"[VRM] Test server started: http://localhost:{PORT}")
print(f"[VRM] Press Ctrl+C to stop")
print()

# Auto open browser
url = f"http://localhost:{PORT}/vrm_viewer.html"
webbrowser.open(url)

try:
    httpd.serve_forever()
except KeyboardInterrupt:
    print("\n[VRM] Server stopped")
    httpd.server_close()
