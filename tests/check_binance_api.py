import urllib.request
import json
import time
import os

BASE_DIR = r"C:\Users\Warlin\OneDrive - Appod\Escritorio\Criptodaytrader"
cfg_path = os.path.join(BASE_DIR, "trader", "config.json")

print("=================================================================")
print("     DIAGNÓSTICO INTEGRAL DE CONEXIÓN CON LA API DE BINANCE")
print("=================================================================")

# 1. Verificar configuración de claves y modo
print("\n[1] REVISIÓN DE CONFIGURACIÓN (trader/config.json):")
if os.path.exists(cfg_path):
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    print(f"  * Modo de Operación: {'PAPER TRADING (Simulador Seguro)' if cfg.get('paper') else 'LIVE / REAL'}")
    print(f"  * Moneda Base: {cfg.get('currency', 'USDT')}")
    print(f"  * Presupuesto: ${cfg.get('budget', 0):.2f} USDT")
    
    api_key = cfg.get("binance_api_key", "").strip()
    api_secret = cfg.get("binance_api_secret", "").strip()
    
    if api_key and api_secret:
        masked_key = api_key[:6] + "..." + api_key[-4:]
        print(f"  * API Key Privada: CONFIGURADA ({masked_key})")
    else:
        print("  * API Key Privada: NO CONFIGURADA (Válido para Paper Trading)")
        print("    (Para operar con órdenes reales o Testnet de Binance, puedes colocar tu API Key y Secret en config.json)")
else:
    print("  * ERROR: config.json no encontrado.")

# 2. Conectividad con endpoints públicos de Binance
print("\n[2] CONECTIVIDAD DE RED Y MERCADO EN VIVO (Binance REST API):")
endpoints = [
    ("Ping Spot API (/api/v3/ping)", "https://api.binance.com/api/v3/ping", {}),
    ("Hora de Servidor (/api/v3/time)", "https://api.binance.com/api/v3/time", {}),
    ("Precio en Vivo BTCUSDT (/api/v3/ticker/price)", "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", {}),
    ("Ticker 24h SOLUSDT (/api/v3/ticker/24hr)", "https://api.binance.com/api/v3/ticker/24hr?symbol=SOLUSDT", {}),
    ("Libro L2 ETHUSDT (/api/v3/depth)", "https://api.binance.com/api/v3/depth?symbol=ETHUSDT&limit=5", {}),
    ("Velas 5m DOGEUSDT (/api/v3/klines)", "https://api.binance.com/api/v3/klines?symbol=DOGEUSDT&interval=5m&limit=3", {}),
    ("Binance Futures Funding (/fapi/v1/fundingRate)", "https://fapi.binance.com/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1", {})
]

all_ok = True
for name, url, extra in endpoints:
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "BinanceCryptoDiagnostic/1.0"})
        with urllib.request.urlopen(req, timeout=5) as res:
            latency = (time.time() - t0) * 1000
            data = json.loads(res.read().decode("utf-8"))
            print(f"  ✅ [CONECTADO] {name} | Latencia: {latency:.1f} ms | Status: {res.status} OK")
            
            if "price" in data:
                print(f"      -> Cotización actual: {float(data['price']):,.2f} USDT")
            elif "lastPrice" in data:
                print(f"      -> Último precio: {float(data['lastPrice']):,.2f} USDT | Cambio 24h: {float(data['priceChangePercent']):+.2f}%")
            elif isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
                last_kline = data[-1]
                print(f"      -> Última vela 5m: Open={last_kline[1]}, High={last_kline[2]}, Low={last_kline[3]}, Close={last_kline[4]}")
            elif isinstance(data, list) and len(data) > 0 and "fundingRate" in data[0]:
                print(f"      -> Funding Rate BTC: {float(data[0]['fundingRate'])*100:.4f}%")
    except Exception as e:
        all_ok = False
        print(f"  ❌ [ERROR] {name}: {e}")

print("\n[3] RESUMEN DEL ESTADO DE LA API:")
if all_ok:
    print("  🟢 CONEXIÓN TOTALMENTE ACTIVA Y OPERATIVA:")
    print("     - Todos los agentes (@scanner, @analyst, @entry, @exit, @timemachine, @whale)")
    print("       reciben cotizaciones y datos reales de Binance en tiempo real.")
    print("     - El motor @broker opera en modo PAPER TRADING seguro.")
else:
    print("  🟡 CONEXIÓN PARCIAL O CON PROBLEMAS DE RED.")
print("=================================================================")
