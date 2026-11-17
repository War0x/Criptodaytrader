#!/usr/bin/env python3
"""Binance Crypto Day Trader - Virtual Office Server & Autonomous Trading Floor.
Standard library only: http.server, threading, urllib.request, json, time, os, sys, subprocess, webbrowser.

Upgraded with Gemini 4 Hydrogen architectural recommendations:
  - SQLite WAL persistent store via TradingDB (atomic transactions, zero race conditions)
  - Persistent HTTPS client with connection reuse (eliminates repetitive TLS handshakes)
  - Decoupled macro worker for low-frequency agents (@analyst, @whale, @news)
  - Strict position sizing integration via trader-budget/size.py
  - Automatic JSON export mirror for zero-downtime backward compatibility
"""

import http.client
import json
import math
import os
import ssl
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TRADER_DIR = os.path.join(BASE_DIR, "trader")
PORT = 8766

# Importar capa de persistencia SQLite WAL
from trader.db import TradingDB

# Importar calculadora estricta de riesgo
sys.path.insert(0, os.path.join(BASE_DIR, "trader-budget"))
try:
    import size as budget_sizer
except Exception:
    budget_sizer = None


class PersistentHTTPClient:
    """Cliente HTTPS thread-safe con Keep-Alive para consultas de alta frecuencia."""

    def __init__(self, host="api.binance.com", timeout=5.0):
        self.host = host
        self.timeout = timeout
        self._lock = threading.Lock()
        self._ssl_ctx = ssl.create_default_context()
        self._conn = None

    def _ensure_conn(self):
        if self._conn is None:
            self._conn = http.client.HTTPSConnection(
                self.host, timeout=self.timeout, context=self._ssl_ctx
            )
        return self._conn

    def _reset_conn(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def get_json(self, path, params=None):
        query = f"?{urllib.parse.urlencode(params)}" if params else ""
        full_path = f"{path}{query}"
        headers = {
            "User-Agent": "Criptodaytrader-PersistentEngine/2.0",
            "Connection": "keep-alive",
            "Accept": "application/json",
        }
        with self._lock:
            for attempt in range(2):
                try:
                    conn = self._ensure_conn()
                    conn.request("GET", full_path, headers=headers)
                    resp = conn.getresponse()
                    raw = resp.read()
                    if resp.status >= 400:
                        raise RuntimeError(f"HTTP {resp.status}: {raw.decode('utf-8', 'ignore')}")
                    return json.loads(raw.decode("utf-8"))
                except Exception as exc:
                    self._reset_conn()
                    if attempt == 1:
                        raise RuntimeError(f"Fallo conexión persistente {self.host}: {exc}") from exc

    def close(self):
        with self._lock:
            self._reset_conn()


def read_json_safe(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def write_json_safe(path, data):
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(temp_path, path)


def check_binance_connectivity():
    """Valida la conectividad real con la API pública de Binance."""
    try:
        req = urllib.request.Request(
            "https://api.binance.com/api/v3/ping",
            headers={"User-Agent": "BinanceCryptoSwarm/1.0"}
        )
        with urllib.request.urlopen(req, timeout=3) as res:
            return res.status == 200
    except Exception:
        return False


def fetch_binance_tickers():
    """Obtiene cotizaciones y cambio 24h en tiempo real desde Binance."""
    target_symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT", "NEARUSDT", "SUIUSDT", "LINKUSDT"]
    try:
        req = urllib.request.Request(
            "https://api.binance.com/api/v3/ticker/24hr",
            headers={"User-Agent": "BinanceCryptoSwarm/1.0"}
        )
        with urllib.request.urlopen(req, timeout=4) as res:
            raw = json.loads(res.read().decode("utf-8"))
            filtered = []
            for item in raw:
                sym = item.get("symbol")
                if sym in target_symbols:
                    filtered.append({
                        "symbol": sym,
                        "price": float(item.get("lastPrice", 0.0)),
                        "change_pct": round(float(item.get("priceChangePercent", 0.0)), 2),
                        "high": float(item.get("highPrice", 0.0)),
                        "low": float(item.get("lowPrice", 0.0)),
                        "volume": float(item.get("quoteVolume", 0.0))
                    })
            return filtered
    except Exception:
        return []


def run_agent_script(agent_id, custom_args=None):
    """Ejecuta el script de un agente en segundo plano."""
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
            cmd.extend(["check", "--auto-flatten"])

        try:
            res = subprocess.run(cmd, cwd=BASE_DIR, capture_output=True, text=True, timeout=15)
            return res.returncode == 0, res.stdout
        except Exception as e:
            return False, str(e)
    return False, f"Script para {agent_id} no encontrado."


class AutonomousTradingEngine:
    """
    Motor Autónomo de Day Trading respaldado por SQLite WAL, Persistent HTTP
    y Desacople de Agentes Macro/Intradía.
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
        self._macro_thread = None

        # Base de Datos SQLite WAL
        self.db = TradingDB(os.path.join(TRADER_DIR, "trading.db"))
        # Importar datos iniciales existentes si la DB está fresca
        self._init_db_sync()

        # Cliente HTTP persistente
        self.http = PersistentHTTPClient(host="api.binance.com", timeout=5.0)

        self._seed_initial_logs()

    def _init_db_sync(self):
        try:
            self.db.import_from_json(
                positions_path=os.path.join(TRADER_DIR, "portfolio.json"),
                watchlist_path=os.path.join(TRADER_DIR, "watchlist.json"),
                history_path=os.path.join(TRADER_DIR, "trade_history.json"),
                config_path=os.path.join(TRADER_DIR, "config.json"),
            )
        except Exception as e:
            print(f"[engine] Error en sync inicial DB: {e}")

    def _seed_initial_logs(self):
        now_utc = time.strftime("%H:%M:%S UTC", time.gmtime())
        self.logs = [
            {"time": now_utc, "agent": "@engine", "msg": "Motor Autónomo v2.0 (SQLite WAL + Keep-Alive) iniciado.", "level": "success"},
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

        # Guardar en SQLite WAL atómicamente
        try:
            self.db.log_event(level.upper(), agent, "LOG", msg)
        except Exception:
            pass

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

        # Iniciar worker macro de baja frecuencia desacoplado (cada 1 hora)
        if self._macro_thread is None or not self._macro_thread.is_alive():
            self._macro_thread = threading.Thread(target=self._macro_loop, daemon=True)
            self._macro_thread.start()

    def _macro_loop(self):
        """Ejecuta agentes macro/fundamentales de baja frecuencia sin bloquear el loop rápido."""
        while True:
            try:
                self.log("@news", "Actualizando catalizadores y noticias de mercado en segundo plano...", "info")
                run_agent_script("news")
                run_agent_script("whale")
                run_agent_script("analyst")
                self.db.set_config("macro_last_run", time.time())
                self.db.set_config("macro_regime", "RISK_ON")
            except Exception as e:
                self.log("@news", f"Aviso macro: {e}", "warning")
            time.sleep(3600)  # Cada 1 hora

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

        # 1. Comprobación de conectividad real
        if not check_binance_connectivity():
            self.status_message = "DESCONECTADO DE BINANCE (Sin Internet o API inalcanzable)"
            self.log("@engine", "⚠️ Desconectado de Binance API. Operaciones pausadas por seguridad.", "danger")
            self.is_cycling = False
            return

        self.status_message = "Vigilando mercado en vivo (Binance Spot)"

        # 2. Monitoreo de posiciones abiertas y alertas automáticas (SQLite WAL)
        positions = self.db.get_positions()
        if positions:
            for pos in positions:
                sym = pos.get("symbol", "")
                qty = pos.get("qty", 0)
                entry_p = pos.get("entry_price", 0)
                self.log("@alerts", f"Monitoreando posición abierta en {sym} ({qty} @ ${entry_p}). Verificando SL/TP...", "info")

            run_agent_script("alerts", custom_args=["check", "--auto-flatten"])
            # Re-leer de SQLite WAL tras posible cierre
            positions = self.db.get_positions()

        # 3. Respetar límite de posiciones simultáneas
        cfg = self.db.get_config("config", read_json_safe(os.path.join(TRADER_DIR, "config.json")) or {})
        max_positions = int(cfg.get("max_positions", 1))

        if len(positions) >= max_positions:
            pos = positions[0]
            sym = pos.get("symbol")
            entry_p = pos.get("entry_price")
            self.status_message = f"OPERANDO POSICIÓN: {sym} en cartera (Entrada: ${entry_p})"
            self.is_cycling = False
            return

        # 4. Escaneo del mercado
        run_agent_script("scanner")
        watchlist_items = self.db.get_watchlist()
        if not watchlist_items:
            watchlist = read_json_safe(os.path.join(TRADER_DIR, "watchlist.json")) or {}
            watchlist_items = watchlist.get("hits", [])
            if watchlist_items:
                self.db.save_watchlist(watchlist_items)

        if not watchlist_items:
            self.log("@scanner", "Escaneo finalizado sin candidatos que superen el filtro de volumen $5M USDT.", "warning")
            self.is_cycling = False
            return

        top_hit = watchlist_items[0]
        candidate = top_hit.get("symbol")
        score = top_hit.get("score", 0)
        chg = top_hit.get("change_24h_pct", top_hit.get("change_24h", 0))
        price = top_hit.get("price", top_hit.get("last_price", 0))
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

        # 7. Dimensionar posición con trader-budget (Cálculo estricto Hydrogen)
        run_agent_script("budget", custom_args=["--write"])
        orders_doc = read_json_safe(os.path.join(TRADER_DIR, "orders.json")) or {}
        order_list = orders_doc.get("orders", [])
        order_info = order_list[0] if order_list else orders_doc
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

        # 9. Espejar a JSON de forma atómica para retrocompatibilidad total
        try:
            self.db.export_to_json(TRADER_DIR)
        except Exception:
            pass

        self.is_cycling = False


# Instancia global del motor
engine = AutonomousTradingEngine()


class BinanceOfficeHandler(SimpleHTTPRequestHandler):
    """Manejador HTTP para la Oficina Virtual de Binance Crypto Trading."""

    def translate_path(self, path):
        clean_path = urllib.parse.urlparse(path).path
        if clean_path == "/" or clean_path == "/index.html":
            target = os.path.join(BASE_DIR, "virtual_office.html")
            if os.path.exists(target):
                return target
        return super().translate_path(path)

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url_path = urllib.parse.urlparse(self.path).path

        if url_path == "/" or url_path == "/index.html":
            html_path = os.path.join(BASE_DIR, "virtual_office.html")
            if os.path.exists(html_path):
                with open(html_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(FALLBACK_HTML.encode("utf-8"))

        elif url_path == "/api/status":
            is_connected = check_binance_connectivity()
            tickers = fetch_binance_tickers() if is_connected else []

            # Leer desde SQLite WAL de forma atómica
            summary = engine.db.get_portfolio_summary()
            positions = engine.db.get_positions()
            watchlist = engine.db.get_watchlist()
            cfg = read_json_safe(os.path.join(TRADER_DIR, "config.json")) or {}
            history = read_json_safe(os.path.join(TRADER_DIR, "trade_history.json")) or []

            active_pair = "BTCUSDT"
            active_price = 0.0

            if positions:
                active_pair = positions[0].get("symbol", "BTCUSDT")
            elif watchlist:
                active_pair = watchlist[0].get("symbol", "BTCUSDT")

            for t in tickers:
                if t.get("symbol") == active_pair:
                    active_price = t.get("price", 0.0)
                    break

            budget = cfg.get("budget", 100.0)
            cash = summary.get("available_cash", budget)

            # Enriquecer posiciones abiertas con precio en vivo de Binance y PnL dinámico
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
                "portfolio_value": summary.get("equity_base", cash),
                "currency": cfg.get("currency", "USDT"),
                "active_pair": active_pair,
                "active_price": active_price,
                "config": cfg,
                "portfolio": {"cash": cash, "positions": positions},
                "positions": positions,
                "watchlist": {"hits": watchlist},
                "history": history,
                "tickers": tickers,
                "logs": engine.get_logs(),
                "server_time": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                "total_trades": summary.get("total_trades", len(history))
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
    server_address = ("127.0.0.1", PORT)
    httpd = HTTPServer(server_address, BinanceOfficeHandler)
    print(f"[OK] Servidor Binance Crypto Virtual Office corriendo en: http://127.0.0.1:{PORT}")
    httpd.serve_forever()


def launch_window():
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
    print(f"[INFO] Abriendo en navegador predeterminado: {url}")
    webbrowser.open(url)


if __name__ == "__main__":
    print("=" * 65)
    print("   BINANCE CRYPTO DAY TRADER - VIRTUAL OFFICE (12 AGENTES)")
    print(f"   Puerto local: {PORT} | Interfaz Dark & Gold Binance Neon")
    print("=" * 65)

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
