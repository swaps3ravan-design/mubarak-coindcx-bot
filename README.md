
# MUBARAKUSDT TradingView -> CoinDCX Futures

This project is a webhook execution server for the Session POC TradingView strategy.

## Safety
The default is DRY_RUN=true. In dry-run mode NO CoinDCX order is placed.

Do not put the CoinDCX API secret in TradingView.

## 1. Install
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate
pip install -r requirements.txt

## 2. Configure
Copy .env.example to .env and fill in:
- COINDCX_API_KEY
- COINDCX_API_SECRET
- WEBHOOK_SECRET

The server uses:
- ₹10,000 capital
- 5x leverage
- USDT-margined futures
by default.

USDT_INR is a configuration value. Set it to the conversion you want to use for sizing, or replace the sizing logic with a live conversion source.

## 3. Run
The simplest option is to export the environment variables in your hosting platform.
Then:
python server.py

For local testing:
curl http://127.0.0.1:8080/health

## 4. Test DRY RUN
Send a request to:
POST /webhook

Header:
X-Webhook-Secret: YOUR_SECRET

JSON:
{"action":"buy","symbol":"MUBARAKUSDT","price":0.01}

The response should contain mode=DRY_RUN and a calculated quantity.

## 5. TradingView
Create an alert on your strategy using "Any alert() function call".
Webhook URL:
https://YOUR_PUBLIC_HOST/webhook

TradingView sends the webhook secret in the JSON body. The server validates data["secret"] before processing the signal.

## 6. Live mode
Only after DRY RUN has been verified:
DRY_RUN=false

The server:
- fetches live MUBARAK instrument limits
- calculates quantity from capital/leverage/price
- checks the current position
- ignores duplicate same-direction signals
- reverses an opposite position by adding the existing absolute position to the new desired quantity
- sends a CoinDCX Futures market order

Before live use, verify the account has the required USDT Futures margin and that 5x is permitted for the instrument.
