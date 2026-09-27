
import os, time, json, hmac, hashlib, math, uuid, logging
from decimal import Decimal, ROUND_DOWN
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BASE = "https://api.coindcx.com"
PAIR = os.getenv("COINDCX_PAIR", "B-MUBARAK_USDT")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "CHANGE_ME")
API_KEY = os.getenv("COINDCX_API_KEY", "")
API_SECRET = os.getenv("COINDCX_API_SECRET", "")
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

CAPITAL_INR = Decimal(os.getenv("CAPITAL_INR", "10000"))
USDT_INR = Decimal(os.getenv("USDT_INR", "90"))
LEVERAGE = Decimal(os.getenv("LEVERAGE", "5"))
MAX_MARGIN_USDT = Decimal(os.getenv("MAX_MARGIN_USDT", "120"))
MARGIN_MODE = os.getenv("MARGIN_MODE", "USDT")

session = requests.Session()
session.headers.update({"User-Agent": "mubarak-tradingview-bot/1.0"})

def auth_post(path, body):
    if not API_KEY or not API_SECRET:
        raise RuntimeError("CoinDCX API credentials are missing.")
    payload = json.dumps(body, separators=(",", ":"))
    sig = hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    r = session.post(
        BASE + path,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-AUTH-APIKEY": API_KEY,
            "X-AUTH-SIGNATURE": sig,
        },
        timeout=10,
    )
    r.raise_for_status()
    return r.json()

def get_instrument():
    r = session.get(
        BASE + "/exchange/v1/derivatives/futures/data/instrument",
        params={"pair": PAIR, "margin_currency_short_name": MARGIN_MODE},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    inst = data.get("instrument", data)
    if isinstance(inst, list):
        inst = inst[0]
    return inst

def get_position():
    body = {
        "timestamp": int(time.time() * 1000),
        "page": "1",
        "size": "10",
        "pairs": PAIR,
        "margin_currency_short_name": [MARGIN_MODE],
    }
    data = auth_post("/exchange/v1/derivatives/futures/positions", body)
    if isinstance(data, dict):
        rows = data.get("positions", data.get("data", []))
    else:
        rows = data
    for p in rows or []:
        if p.get("pair") == PAIR:
            return p
    return {"pair": PAIR, "active_pos": 0}

def floor_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step

def dec(v):
    return Decimal(str(v))

def calculate_quantity(price: Decimal, inst):
    margin_usdt = min(CAPITAL_INR / USDT_INR, MAX_MARGIN_USDT)
    notional = margin_usdt * LEVERAGE
    raw_qty = notional / price
    step = dec(inst["quantity_increment"])
    min_qty = max(dec(inst["min_quantity"]), dec(inst["min_trade_size"]))
    qty = floor_step(raw_qty, step)

    if qty < min_qty:
        qty = min_qty

    min_notional = dec(inst.get("min_notional", "0"))
    if qty * price < min_notional:
        qty = floor_step((min_notional / price) + step, step)

    max_qty = dec(inst["max_quantity"])
    if qty > max_qty:
        raise ValueError(f"Calculated quantity {qty} exceeds exchange max {max_qty}.")

    return qty, margin_usdt, notional

def create_market_order(side, quantity):
    client_id = "tv-mubarak-" + uuid.uuid4().hex[:20]
    body = {
        "timestamp": int(time.time() * 1000),
        "order": {
            "side": side,
            "pair": PAIR,
            "order_type": "market_order",
            "price": 0,
            "stop_price": 0,
            "total_quantity": float(quantity),
            "leverage": int(LEVERAGE),
            "notification": "no_notification",
            "hidden": False,
            "post_only": False,
            "margin_currency_short_name": [MARGIN_MODE],
            "client_order_id": client_id,
        },
    }
    # CoinDCX documentation says not to include time_in_force for market orders.
    return auth_post("/exchange/v1/derivatives/futures/orders/create", body)

@app.get("/health")
def health():
    return jsonify({"ok": True, "dry_run": DRY_RUN, "pair": PAIR})

@app.get("/instrument")
def instrument():
    return jsonify(get_instrument())

@app.post("/webhook")
def webhook():
    data = request.get_json(silent=True) or {}
    if data.get("secret") != WEBHOOK_SECRET:
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    action = str(data.get("action", "")).lower()
    symbol = str(data.get("symbol", ""))
    price = dec(data.get("price", "0"))

    if action not in ("buy", "sell"):
        return jsonify({"ok": False, "error": "action must be buy or sell"}), 400
    if symbol.upper().replace("/", "") not in ("MUBARAKUSDT", "B-MUBARAK_USDT"):
        return jsonify({"ok": False, "error": "wrong symbol"}), 400
    if price <= 0:
        return jsonify({"ok": False, "error": "price must be positive"}), 400

    inst = get_instrument()
    if inst.get("status") != "active":
        return jsonify({"ok": False, "error": "instrument is not active"}), 409

    qty, margin_usdt, notional = calculate_quantity(price, inst)
    desired_sign = Decimal("1") if action == "buy" else Decimal("-1")

    if DRY_RUN:
        return jsonify({
            "ok": True, "mode": "DRY_RUN", "action": action, "pair": PAIR,
            "price": str(price), "quantity": str(qty),
            "margin_usdt": str(margin_usdt), "notional_usdt": str(notional)
        })

    current = get_position()
    active = dec(current.get("active_pos", 0))

    # One-position stop-and-reverse behavior:
    # same direction => ignore duplicate signal
    # opposite direction => trade current abs position + desired quantity
    if active != 0 and ((active > 0 and action == "buy") or (active < 0 and action == "sell")):
        return jsonify({"ok": True, "status": "ignored_same_direction", "active_pos": str(active)})

    order_qty = qty if active == 0 else abs(active) + qty
    result = create_market_order(action, order_qty)

    return jsonify({
        "ok": True, "mode": "LIVE", "action": action, "pair": PAIR,
        "requested_quantity": str(order_qty),
        "previous_position": str(active),
        "order": result,
    })

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
