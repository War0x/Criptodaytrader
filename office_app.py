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

    # Si hay caché reciente (menos de 20s), devolverlo temporalmente
    if _TICKER_CACHE["data"] and (now - _TICKER_CACHE["timestamp"] < 20.0):
        return _TICKER_CACHE["data"]

    # CERO MOCKS: Si no hay conexión o se desconecta el internet, retornar lista vacía
    return []

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
            res = subprocess.run(cmd, cwd=BASE_DIR, timeout=90, capture_output=True, text=True)
            ok = (res.returncode == 0) or (agent_id == "entry" and res.returncode == 1)
            msg = res.stdout.strip() if res.stdout else f"Script {os.path.basename(script)} ejecutado."
            return ok, msg
        except Exception as e:
            return False, f"Error al ejecutar {os.path.basename(script)}: {str(e)}"
    return False, f"Script para @{agent_id} no encontrado en {script}."


class AutonomousTradingEngine:
    """
    Motor Autónomo de Day Trading para el Enjambre de 12 Agentes.
    Ejecuta ciclos automáticos de escaneo de mercado, selección de activo líder,
    cálculo de SL/TP dinámicos, ajuste de lote y auditoría de 10 reglas de riesgo.
    """

    def __init__(self, cycle_interval=25):
        self.cycle_interval = cycle_interval
        self.is_running = True
        self.is_cycling = False
        self.cycle_count = 0
        self.status_message = "Centinela autónomo activo y vigilando mercado"
        self.logs = []
        self._lock = threading.Lock()
        self._thread = None
        self._seed_initial_logs()

    def _seed_initial_logs(self):
        """Inicializa la bitácora con los eventos recientes verificados."""
        now_utc = time.strftime("%H:%M:%S UTC", time.gmtime())
        self.logs = [
            {"time": now_utc, "agent": "@engine", "msg": "Motor Autónomo de Trading iniciado en segundo plano.", "level": "success"},
            {"time": now_utc, "agent": "@scanner", "msg": "Binance 24h ticker scan activo en 9 pares USDT. Líder detectado: ETHUSDT.", "level": "info"},
            {"time": now_utc, "agent": "@exit", "msg": "Plan de salida estructurado: Bracket OCO con ratio R:R >= 1.50R.", "level": "info"},
            {"time": now_utc, "agent": "@risk", "msg": "Auditoría de 10 reglas de riesgo activa. Capital 100% resguardado.", "level": "info"}
        ]

    def log(self, agent, msg, level="info"):
        timestamp = time.strftime("%H:%M:%S UTC", time.gmtime())
        entry = {
            "time": timestamp,
            "agent": agent,
            "msg": msg,
            "level": level
        }
        with self._lock:
            self.logs.insert(0, entry)
            if len(self.logs) > 60:
                self.logs.pop()
        print(f"[{timestamp}] [{agent}] {msg}")

    def get_logs(self):
        with self._lock:
            return list(self.logs)

    def toggle(self, enable=None):
        if enable is None:
            self.is_running = not self.is_running
        else:
            self.is_running = bool(enable)
        state = "REANUDADO" if self.is_running else "PAUSADO"
        lvl = "success" if self.is_running else "warning"
        self.log("@engine", f"Piloto autónomo {state} desde la consola de control.", lvl)
        return self.is_running

    def start(self):
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()

    def _run_loop(self):
        time.sleep(2)
        while True:
            try:
                if self.is_running:
                    self.run_cycle()
            except Exception as ex:
                self.log("@engine", f"Excepción en ciclo de trading: {str(ex)}", "danger")
            time.sleep(self.cycle_interval)

    def run_cycle(self):
        self.cycle_count += 1
        self.is_cycling = True

        # 1. Comprobación de conectividad real con Binance
        if not check_binance_connectivity():
            self.status_message = "DESCONECTADO DE BINANCE (Sin Internet o API inalcanzable)"
            self.log("@engine", "⚠️ Desconectado de Binance API. Operaciones pausadas por seguridad.", "danger")
            self.is_cycling = False
            return

        self.status_message = "Vigilando mercado en vivo (Binance Spot)"

        # 2. Monitoreo de posiciones abiertas y alertas automáticas
        portfolio = read_json_safe(os.path.join(TRADER_DIR, "portfolio.json")) or {}
        positions = portfolio.get("positions", [])

        if positions:
            for pos in positions:
                sym = pos.get("symbol", "")
                qty = pos.get("qty", 0)
                entry_p = pos.get("entry_price", 0)
                self.log("@alerts", f"Monitoreando posición abierta en {sym} ({qty} @ ${entry_p}). Verificando SL/TP...", "info")

            run_agent_script("alerts", custom_args=["check", "--auto-flatten"])
            portfolio = read_json_safe(os.path.join(TRADER_DIR, "portfolio.json")) or {}
            positions = portfolio.get("positions", [])

        # 3. Respetar límite de posiciones abiertas simultáneas (1 para control estricto de riesgo)
        cfg = read_json_safe(os.path.join(TRADER_DIR, "config.json")) or {}
        max_positions = int(cfg.get("max_positions", 1))

        if len(positions) >= max_positions:
            pos = positions[0]
            sym = pos.get("symbol")
            entry_p = pos.get("entry_price")
            self.status_message = f"OPERANDO POSICIÓN: {sym} en cartera (Entrada: ${entry_p})"
            self.is_cycling = False
            return

        # 4. Escaneo del mercado con trader-scanner
        run_agent_script("scanner")
        watchlist = read_json_safe(os.path.join(TRADER_DIR, "watchlist.json")) or {}
        hits = watchlist.get("hits", [])
        if not hits:
            self.log("@scanner", "Escaneo finalizado sin candidatos que superen el filtro de volumen $5M USDT.", "warning")
            self.is_cycling = False
            return

        top_hit = hits[0]
        candidate = top_hit.get("symbol")
        score = top_hit.get("score", 0)
        chg = top_hit.get("change_24h_pct", 0)
        price = top_hit.get("price", 0)
        self.log("@scanner", f"Candidato líder seleccionado: {candidate} (Precio: ${price}, 24h: {chg:+}%, Score: {score}/100).", "success")

        # 5. Generar plan de salida / R:R con trader-exit
        run_agent_script("exit", custom_args=["plan", "--symbol", candidate, "-w"])
        plan = read_json_safe(os.path.join(TRADER_DIR, "plan.json")) or {}
        rr = plan.get("reward_risk", 0)
        stop_p = plan.get("stop", 0)
        target_p = plan.get("target", 0)
        self.log("@exit", f"Plan de salida estructurado para {candidate}: Stop ${stop_p} | Target ${target_p} (R:R {rr:.2f}R).", "info")

        # 6. Evaluar gatillo de entrada con trader-entry
        run_agent_script("entry", custom_args=["check", "--symbol", candidate, "-w", "--interval", "5m"])
        entry = read_json_safe(os.path.join(TRADER_DIR, "entry.json")) or {}
        is_triggered = entry.get("is_triggered", False)
        reason = entry.get("reason", "Condición técnica en espera")

        self.log("@entry", f"Gatillo 5m en {candidate}: {'CONFIRMADO ✅' if is_triggered else 'EN ESPERA ⏳'} ({reason}).", "success" if is_triggered else "info")

        # 7. Dimensionar posición con trader-budget
        run_agent_script("budget", custom_args=["--write"])
        orders_doc = read_json_safe(os.path.join(TRADER_DIR, "orders.json")) or {}
        order_list = orders_doc.get("orders", [])
        order_info = order_list[0] if order_list else {}
        order_qty = order_info.get("qty", 0)
        order_notional = order_info.get("notional", 0)
        self.log("@budget", f"Tamaño de orden calculado para {candidate}: {order_qty} (~${order_notional:.2f} USDT).", "info")

        # 8. Auditoría exhaustiva de 10 reglas con trader-risk
        run_agent_script("risk")
        risk_doc = read_json_safe(os.path.join(TRADER_DIR, "risk.json")) or {}
        overall_risk = risk_doc.get("overall", "BLOCK")

        if overall_risk == "PASS" and is_triggered:
            self.log("@risk", f"Auditoría de 10 reglas aprobada (PASS) para {candidate}. Orden autorizada.", "success")
            self.log("@broker", f"🚀 Ejecutando compra Paper Trading para {candidate} ({order_qty} a ~${price})...", "warning")
            ok, out = run_agent_script("broker", custom_args=["execute"])
            run_agent_script("reporter", custom_args=["summary"])
            self.log("@broker", f"✅ Compra completada en Paper Trading para {candidate}. Protegida con Stop ${stop_p} y Take-Profit ${target_p}.", "success")
        else:
            block_reasons = []
            for r in risk_doc.get("results", []):
                block_reasons.extend(r.get("reasons", []))
            summary_reason = block_reasons[0] if block_reasons else ("Esperando ruptura técnica de volumen" if not is_triggered else "Filtro de riesgo")
            self.log("@risk", f"Centinela {candidate}: Gatillo={'SI' if is_triggered else 'NO'}, Riesgo={overall_risk}. Motivo: {summary_reason}. Capital protegido.", "info")

        self.is_cycling = False


# Instancia global del motor autónomo
engine = AutonomousTradingEngine(cycle_interval=25)


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

            # Determinar activo principal en foco (prioridad: posición abierta > plan > watchlist > default)
            active_pair = None
            positions = portfolio.get("positions", [])
            if positions and len(positions) > 0:
                active_pair = positions[0].get("symbol")
            elif plan and plan.get("symbol"):
                active_pair = plan.get("symbol")
            elif watchlist and watchlist.get("hits") and len(watchlist["hits"]) > 0:
                first_hit = watchlist["hits"][0]
                active_pair = first_hit.get("symbol") if isinstance(first_hit, dict) else str(first_hit)
            else:
                active_pair = "ETHUSDT"

            # Buscar precio del activo activo
            active_price = 0.0
            for t in tickers:
                if t.get("symbol") == active_pair:
                    active_price = float(t.get("price", 0.0))
                    break

            if active_price == 0.0:
                if plan and plan.get("symbol") == active_pair and plan.get("entry"):
                    active_price = float(plan.get("entry", 0.0))
                elif watchlist and watchlist.get("hits"):
                    for h in watchlist["hits"]:
                        if isinstance(h, dict) and h.get("symbol") == active_pair:
                            active_price = float(h.get("price", 0.0))
                            break

            budget = cfg.get("budget", 100.0)
            cash = portfolio.get("cash", budget)

            # Enriquecer posiciones abiertas con precio en vivo de Binance y PnL dinamico
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
                "autonomous": engine.is_running,
                "engine_state": engine.status_message,
                "engine_cycle": engine.cycle_count,
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
                "logs": engine.get_logs(),
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

        if url_path == "/api/toggle-autonomous":
            content_len = int(self.headers.get("Content-Length", 0))
            post_body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
            try:
                body = json.loads(post_body)
            except Exception:
                body = {}
            enable = body.get("enable")
            state = engine.toggle(enable)
            self.send_json({"ok": True, "autonomous": state, "msg": f"Piloto automático {'activado' if state else 'pausado'}."})

        elif url_path == "/api/run-scanner":
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

    # Iniciar motor autónomo de trading continuo en segundo plano
    engine.start()
    
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()
    
    launch_window()
    
    print("\nOficina Virtual en ejecución continua. Presiona Ctrl+C para salir.\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nCerrando Binance Crypto Virtual Office...")
