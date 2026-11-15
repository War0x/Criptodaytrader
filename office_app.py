#!/usr/bin/env python3
"""
Binance Crypto Day Trader - Virtual Office Server
Port: 8766
Simulador de Oficina Virtual Animada para el Enjambre de 12 Agentes de Binance Crypto Day Trading.
Proporciona servidor HTTP local, API REST en tiempo real y conexión a la API pública de Binance.
"""

import os
import sys
import json
import time
import subprocess
import threading
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import webbrowser

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TRADER_DIR = os.path.join(BASE_DIR, "trader")
PORT = 8766

# Cache para datos de mercado de Binance
_TICKER_CACHE = {"timestamp": 0, "data": []}
_CACHE_TTL = 6.0  # segundos

DEFAULT_PAIRS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
    "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "NEARUSDT", "SUIUSDT"
]

def read_json_safe(path):
    """Lee un archivo JSON de forma segura sin lanzar excepciones."""
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None

def get_tracked_pairs():
    """Lee pairs.txt o devuelve la lista por defecto."""
    pairs_file = os.path.join(TRADER_DIR, "pairs.txt")
    if os.path.exists(pairs_file):
        try:
            with open(pairs_file, "r", encoding="utf-8") as f:
                pairs = [line.strip().upper() for line in f if line.strip() and not line.startswith("#")]
                if pairs:
                    return pairs
        except Exception:
            pass
    return DEFAULT_PAIRS

def fetch_binance_tickers():
    """Consulta la API pública de Binance para obtener precios y cambios 24h con caché."""
    global _TICKER_CACHE
    now = time.time()
    if now - _TICKER_CACHE["timestamp"] < _CACHE_TTL and _TICKER_CACHE["data"]:
        return _TICKER_CACHE["data"]

    tracked = set(get_tracked_pairs())
    try:
        url = "https://api.binance.com/api/v3/ticker/24hr"
        req = urllib.request.Request(url, headers={"User-Agent": "BinanceCryptoSwarm/1.0"})
        with urllib.request.urlopen(req, timeout=4) as res:
            raw = json.loads(res.read().decode("utf-8"))
            tickers = []
            for item in raw:
                sym = item.get("symbol")
                if sym in tracked:
                    price = float(item.get("lastPrice", 0))
                    change = float(item.get("priceChangePercent", 0))
                    volume = float(item.get("quoteVolume", 0))
                    high = float(item.get("highPrice", 0))
                    low = float(item.get("lowPrice", 0))
                    tickers.append({
                        "symbol": sym,
                        "price": price,
                        "change_pct": round(change, 2),
                        "volume_usdt": round(volume, 2),
                        "high": high,
                        "low": low
                    })
            if tickers:
                # Mantener orden de tracked pairs
                order_map = {p: i for i, p in enumerate(get_tracked_pairs())}
                tickers.sort(key=lambda x: order_map.get(x["symbol"], 999))
                _TICKER_CACHE["data"] = tickers
                _TICKER_CACHE["timestamp"] = now
                return tickers
    except Exception as e:
        # Fallback si hay fallo de red momentáneo
        pass

    if _TICKER_CACHE["data"]:
        return _TICKER_CACHE["data"]

    # Simulación de respaldo si no hay conexión externa
    mock = [
        {"symbol": "BTCUSDT", "price": 96420.50, "change_pct": 3.12, "volume_usdt": 1845000000, "high": 97100, "low": 93200},
        {"symbol": "ETHUSDT", "price": 3180.20, "change_pct": 2.45, "volume_usdt": 920000000, "high": 3220, "low": 3080},
        {"symbol": "SOLUSDT", "price": 215.80, "change_pct": 5.88, "volume_usdt": 640000000, "high": 222, "low": 201},
        {"symbol": "BNBUSDT", "price": 645.10, "change_pct": 1.95, "volume_usdt": 310000000, "high": 655, "low": 632},
        {"symbol": "DOGEUSDT", "price": 0.3842, "change_pct": 8.15, "volume_usdt": 480000000, "high": 0.41, "low": 0.35},
        {"symbol": "AVAXUSDT", "price": 36.75, "change_pct": 4.20, "volume_usdt": 190000000, "high": 37.8, "low": 34.9},
        {"symbol": "LINKUSDT", "price": 14.85, "change_pct": 1.75, "volume_usdt": 120000000, "high": 15.2, "low": 14.3},
        {"symbol": "NEARUSDT", "price": 6.45, "change_pct": 3.90, "volume_usdt": 115000000, "high": 6.7, "low": 6.1},
        {"symbol": "SUIUSDT", "price": 3.25, "change_pct": 6.40, "volume_usdt": 210000000, "high": 3.4, "low": 3.02}
    ]
    return mock

def check_binance_connectivity():
    """Verifica la conectividad con Binance REST API ping."""
    try:
        req = urllib.request.Request("https://api.binance.com/api/v3/ping", headers={"User-Agent": "BinanceCryptoSwarm/1.0"})
        with urllib.request.urlopen(req, timeout=3) as res:
            if res.status == 200:
                return True
    except Exception:
        pass
    return False

def run_agent_script(agent_id, custom_args=None):
    """Ejecuta el script de un agente en segundo plano con argumentos apropiados."""
    agent_map = {
        "scanner": os.path.join(BASE_DIR, "trader-scanner", "scan.py"),
        "news": os.path.join(BASE_DIR, "trader-news", "news.py"),
        "whale": os.path.join(BASE_DIR, "trader-whale", "whale.py"),
        "analyst": os.path.join(BASE_DIR, "trader-analyst", "analyst.py"),
        "entry": os.path.join(BASE_DIR, "trader-entry", "entry.py"),
        "exit": os.path.join(BASE_DIR, "trader-exit", "exit.py"),
        "timemachine": os.path.join(BASE_DIR, "trader-timemachine", "timemachine.py"),
        "budget": os.path.join(BASE_DIR, "trader-budget", "size.py"),
        "risk": os.path.join(BASE_DIR, "trader-risk", "check.py"),
        "broker": os.path.join(BASE_DIR, "trader-broker", "broker.py"),
        "alerts": os.path.join(BASE_DIR, "trader-alerts", "alerts.py"),
        "reporter": os.path.join(BASE_DIR, "trader-reporter", "report.py")
    }
    script = agent_map.get(agent_id)
    if script and os.path.exists(script):
        cmd = [sys.executable, script]
        if custom_args:
            cmd.extend(custom_args)
        elif agent_id == "budget":
            cmd.append("--write")
        elif agent_id == "broker":
            cmd.append("status")
        elif agent_id == "alerts":
            cmd.append("check")
        try:
            res = subprocess.run(cmd, cwd=BASE_DIR, timeout=90, capture_output=True)
            return True, f"Script {os.path.basename(script)} ejecutado con éxito."
        except Exception as e:
            return False, f"Error al ejecutar {os.path.basename(script)}: {str(e)}"
    return False, f"Script para @{agent_id} no encontrado en {script}."


class BinanceOfficeHandler(BaseHTTPRequestHandler):
    """Manejador HTTP para la Oficina Virtual de Binance Crypto."""

    def log_message(self, format, *args):
        # Silenciar logs ruidosos en consola salvo errores
        pass

    def send_json(self, data, status_code=200):
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode("utf-8"))

    def do_GET(self):
        url_path = urllib.parse.urlparse(self.path).path

        if url_path in ["/", "/index.html"]:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            html_file = os.path.join(BASE_DIR, "virtual_office.html")
            if os.path.exists(html_file):
                with open(html_file, "r", encoding="utf-8") as f:
                    self.wfile.write(f.read().encode("utf-8"))
            else:
                self.wfile.write(FALLBACK_HTML.encode("utf-8"))

        elif url_path == "/api/status":
            cfg = read_json_safe(os.path.join(TRADER_DIR, "config.json")) or {}
            portfolio = read_json_safe(os.path.join(TRADER_DIR, "portfolio.json")) or {}
            plan = read_json_safe(os.path.join(TRADER_DIR, "plan.json")) or {}
            entry = read_json_safe(os.path.join(TRADER_DIR, "entry.json")) or {}
            orders = read_json_safe(os.path.join(TRADER_DIR, "orders.json")) or {}
            history = read_json_safe(os.path.join(TRADER_DIR, "trade_history.json")) or []
            watchlist = read_json_safe(os.path.join(TRADER_DIR, "watchlist.json")) or {}
            timemachine = read_json_safe(os.path.join(TRADER_DIR, "timemachine.json")) or {}

            is_connected = check_binance_connectivity()
            tickers = fetch_binance_tickers()

            # Determinar activo principal en foco
            active_pair = None
            if plan and plan.get("symbol"):
                active_pair = plan.get("symbol")
            elif watchlist and watchlist.get("hits") and len(watchlist["hits"]) > 0:
                first_hit = watchlist["hits"][0]
                active_pair = first_hit.get("symbol") if isinstance(first_hit, dict) else str(first_hit)
            else:
                active_pair = "BTCUSDT"

            # Buscar precio del activo activo
            active_price = 0.0
            for t in tickers:
                if t["symbol"] == active_pair:
                    active_price = t["price"]
                    break

            budget = cfg.get("budget", 100.0)
            cash = portfolio.get("cash", budget)

            # Enriquecer posiciones abiertas con precio en vivo de Binance y PnL dinamico
            positions = portfolio.get("positions", [])
            for pos in positions:
                sym = pos.get("symbol")
                for t in tickers:
                    if t.get("symbol") == sym and t.get("price"):
                        live_p = float(t["price"])
                        pos["current_price"] = live_p
                        entry_p = float(pos.get("entry_price", live_p))
                        qty = float(pos.get("qty", 0))
                        if entry_p > 0 and qty > 0:
                            pos["pnl"] = round((live_p - entry_p) * qty, 4)
                            pos["pnl_pct"] = round(((live_p - entry_p) / entry_p) * 100.0, 2)
                        break

            res = {
                "exchange": "binance",
                "connected": is_connected,
                "mode": "BINANCE PAPER TRADING" if cfg.get("paper", True) else "BINANCE LIVE",
                "budget": budget,
                "cash": cash,
                "portfolio_value": portfolio.get("portfolio_value", cash),
                "currency": cfg.get("currency", "USDT"),
                "active_pair": active_pair,
                "active_price": active_price,
                "config": cfg,
                "portfolio": portfolio,
                "watchlist": watchlist,
                "plan": plan,
                "entry": entry,
                "orders": orders,
                "history": history,
                "timemachine": timemachine,
                "tickers": tickers,
                "server_time": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                "total_trades": len(history) if isinstance(history, list) else 0
            }
            self.send_json(res)

        elif url_path == "/api/tickers":
            tickers = fetch_binance_tickers()
            self.send_json({"ok": True, "tickers": tickers})

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        url_path = urllib.parse.urlparse(self.path).path

        if url_path == "/api/run-scanner":
            def _task():
                run_agent_script("scanner")
            threading.Thread(target=_task, daemon=True).start()
            self.send_json({"ok": True, "msg": "Binance Market Scanner lanzado en segundo plano."})

        elif url_path == "/api/execute-paper":
            def _task():
                run_agent_script("broker", custom_args=["execute"])
            threading.Thread(target=_task, daemon=True).start()
            self.send_json({"ok": True, "msg": "Orden de Paper Trading enviada al broker de Binance."})

        elif url_path == "/api/run-agent":
            content_len = int(self.headers.get("Content-Length", 0))
            post_body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
            try:
                body = json.loads(post_body)
            except Exception:
                body = {}
            agent_id = body.get("agent")
            if not agent_id:
                self.send_json({"ok": False, "msg": "Falta el campo 'agent' en la petición."}, 400)
                return

            def _task():
                run_agent_script(agent_id)
            threading.Thread(target=_task, daemon=True).start()
            self.send_json({"ok": True, "msg": f"Agente @{agent_id} ejecutado en segundo plano."})

        elif url_path == "/api/open-history":
            history_file = os.path.join(TRADER_DIR, "trade_history.json")
            excel_file = os.path.join(TRADER_DIR, "Resumen_Ejecutivo_Crypto.xlsx")
            target = excel_file if os.path.exists(excel_file) else history_file

            if os.path.exists(target):
                try:
                    os.startfile(target)
                    self.send_json({"ok": True, "msg": f"Archivo abierto: {os.path.basename(target)}"})
                except Exception as e:
                    self.send_json({"ok": False, "msg": f"Error al abrir archivo: {str(e)}"})
            else:
                self.send_json({"ok": False, "msg": "Archivo de historial aún no creado."})

        else:
            self.send_response(404)
            self.end_headers()


FALLBACK_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <title>Binance Crypto Day Trader - Virtual Office</title>
  <style>
    body { background: #0b0e14; color: #F0B90B; font-family: sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
    .box { text-align: center; border: 1px solid #F0B90B; padding: 40px; border-radius: 12px; }
  </style>
</head>
<body>
  <div class="box">
    <h1>BINANCE CRYPTO DAY TRADER VIRTUAL OFFICE</h1>
    <p>Cargando interfaz gráfica completa desde virtual_office.html...</p>
    <p><a href="/" style="color: #0ecb81;">Recargar página</a></p>
  </div>
</body>
</html>"""


def start_server():
    """Inicia el servidor HTTP de la Oficina Virtual en el puerto 8766."""
    server_address = ("127.0.0.1", PORT)
    httpd = HTTPServer(server_address, BinanceOfficeHandler)
    print(f"[OK] Servidor Binance Crypto Virtual Office corriendo en: http://127.0.0.1:{PORT}")
    httpd.serve_forever()

def launch_window():
    """Abre la ventana de la oficina en Chrome o Edge en modo standalone app."""
    time.sleep(1.2)
    url = f"http://127.0.0.1:{PORT}"
    
    browser_paths = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
    ]
    
    for browser in browser_paths:
        if os.path.exists(browser):
            try:
                subprocess.Popen([browser, f"--app={url}", "--window-size=1420,900"])
                print(f"[OK] Ventana nativa lanzada con: {os.path.basename(browser)}")
                return
            except Exception:
                pass

    # Fallback al navegador web por defecto
    print(f"[INFO] Abriendo en navegador predeterminado: {url}")
    webbrowser.open(url)

if __name__ == "__main__":
    print("=" * 65)
    print("   BINANCE CRYPTO DAY TRADER - VIRTUAL OFFICE (12 AGENTES)")
    print(f"   Puerto local: {PORT} | Interfaz Dark & Gold Binance Neon")
    print("=" * 65)
    
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()
    
    launch_window()
    
    print("\nOficina Virtual en ejecución continua. Presiona Ctrl+C para salir.\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nCerrando Binance Crypto Virtual Office...")
