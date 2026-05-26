"""
Allegro → Telegram Bot (API Polling)
Checks new orders and messages every 2 minutes via Baselinker API.
"""

import os
import json
import time
import threading
import requests
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
import pytz

# ─── CONFIG ───────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "YOUR_CHAT_ID")
BASELINKER_TOKEN   = os.getenv("BASELINKER_TOKEN",    "YOUR_BASELINKER_TOKEN")
TIMEZONE           = os.getenv("TIMEZONE",            "Europe/Warsaw")
POLL_INTERVAL_SEC  = int(os.getenv("POLL_INTERVAL",   "120"))
BASELINKER_API_URL = "https://api.baselinker.com/connector.php"
# ──────────────────────────────────────────────────────────────────────────────

tz = pytz.timezone(TIMEZONE)

# Account name map: source_id → account name
SOURCE_MAP: dict = {}

state = {
    "last_order_check":   int(time.time()) - 300,
    "last_message_check": int(time.time()) - 300,
    "seen_order_ids":     set(),
    "seen_message_ids":   set(),
}


# ─── TELEGRAM ─────────────────────────────────────────────────────────────────
def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
        }, timeout=10)
        resp.raise_for_status()
        print(f"[TG] Sent: {text[:60]}...")
    except Exception as e:
        print(f"[TG ERROR] {e}")


# ─── BASELINKER API ───────────────────────────────────────────────────────────
def bl_request(method: str, params: dict) -> dict:
    try:
        resp = requests.post(BASELINKER_API_URL, data={
            "token":      BASELINKER_TOKEN,
            "method":     method,
            "parameters": json.dumps(params),
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "SUCCESS":
            print(f"[BL ERROR] {method}: {data.get('error_message', data)}")
            return {}
        return data
    except Exception as e:
        print(f"[BL ERROR] {method}: {e}")
        return {}


def load_source_map():
    global SOURCE_MAP
    data = bl_request("getOrderSources", {})
    allegro = data.get("sources", {}).get("allegro", {})
    SOURCE_MAP = {str(k): v for k, v in allegro.items()}
    print(f"[Sources] Loaded: {SOURCE_MAP}")

def get_account_name(order: dict) -> str:
    source_id = str(order.get("order_source_id", ""))
    return SOURCE_MAP.get(source_id) or order.get("order_source", "Allegro")


# ─── CHECK ORDERS ─────────────────────────────────────────────────────────────
def check_orders():
    now = int(time.time())
    data = bl_request("getOrders", {
        "date_confirmed_from":    state["last_order_check"],
        "get_unconfirmed_orders": False,
    })
    for order in data.get("orders", []):
        order_id = str(order.get("order_id", ""))
        if order_id in state["seen_order_ids"]:
            continue
        state["seen_order_ids"].add(order_id)

        # Skip returns
        if order.get("order_source") == "order_return":
            continue

        amount   = float(order.get("payment_done", 0) or order.get("price_brutto", 0) or 0)
        account  = get_account_name(order)
        products = order.get("products", [])

        product_lines = ""
        for p in products:
            name = p.get("name", "")
            qty  = p.get("quantity", 1)
            if name:
                product_lines += f"\n📦 {name}" + (f" x{qty}" if qty > 1 else "")

        msg = (
            f"🟢 <b>New order</b>\n"
            f"🏪 {account}\n"
            f"💵 +{amount:.2f} zł"
        )
        msg += product_lines
        send_telegram(msg)

    state["last_order_check"] = now
    if len(state["seen_order_ids"]) > 5000:
        state["seen_order_ids"] = set(list(state["seen_order_ids"])[-2000:])


# ─── CHECK MESSAGES ───────────────────────────────────────────────────────────
def check_messages():
    now = int(time.time())
    data = bl_request("getOrderMessages", {
        "date_from": state["last_message_check"],
    })
    for msg_data in data.get("messages", []):
        msg_id = str(msg_data.get("message_id", ""))
        if msg_id in state["seen_message_ids"]:
            continue
        state["seen_message_ids"].add(msg_id)

        if msg_data.get("type", 0) not in (1, "1"):
            continue

        order_id = str(msg_data.get("order_id", "—"))
        buyer    = msg_data.get("login") or msg_data.get("author") or "Buyer"
        content  = msg_data.get("message", "") or ""
        if len(content) > 120:
            content = content[:120] + "…"

        send_telegram(
            f"💬 <b>New message</b>\n"
            f"👤 From: {buyer}\n"
            f"🔢 Order: {order_id}\n"
            f"✉️ {content}"
        )

    state["last_message_check"] = now
    if len(state["seen_message_ids"]) > 5000:
        state["seen_message_ids"] = set(list(state["seen_message_ids"])[-2000:])


# ─── POLL ─────────────────────────────────────────────────────────────────────
def poll():
    check_orders()
    check_messages()


# ─── STATS ────────────────────────────────────────────────────────────────────
def fetch_todays_stats() -> dict:
    now = datetime.now(tz)
    start_of_day = int(datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=tz).timestamp())

    sales_count = 0
    sales_total = 0.0
    cursor = start_of_day
    seen   = set()

    while True:
        data = bl_request("getOrders", {
            "date_confirmed_from":    cursor,
            "get_unconfirmed_orders": False,
        })
        orders = data.get("orders", [])
        if not orders:
            break
        new_orders = False
        for order in orders:
            order_id = str(order.get("order_id", ""))
            if order_id in seen:
                continue
            seen.add(order_id)
            new_orders = True
            order_time = order.get("date_add", cursor)
            if order_time > cursor:
                cursor = order_time
            if order.get("order_source") == "order_return":
                continue
            amount = float(order.get("payment_done", 0) or order.get("price_brutto", 0) or 0)
            sales_count += 1
            sales_total += amount
        if not new_orders:
            break

    return {"sales_count": sales_count, "sales_total": sales_total}


def send_stats_now():
    now_str = datetime.now(tz).strftime("%H:%M")
    send_telegram("⏳ Fetching data from Baselinker...")
    stats = fetch_todays_stats()
    send_telegram(
        f"📊 <b>Today's stats ({now_str})</b>\n"
        "━━━━━━━━━━━━━━━━\n"
        f"🟢 Sales: {stats['sales_count']} orders\n"
        f"💰 <b>Total: +{stats['sales_total']:.2f} zł</b>"
    )


def send_daily_summary():
    stats = fetch_todays_stats()
    send_telegram(
        "📊 <b>Daily summary</b>\n"
        "━━━━━━━━━━━━━━━━\n"
        f"🟢 Sales: {stats['sales_count']} orders\n"
        f"💰 <b>Total: +{stats['sales_total']:.2f} zł</b>"
    )


# ─── COMMANDS ─────────────────────────────────────────────────────────────────
def listen_commands():
    offset = None
    print("[Commands] Listening...")
    while True:
        try:
            params = {"timeout": 30, "allowed_updates": ["message"]}
            if offset:
                params["offset"] = offset
            resp = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
                params=params, timeout=40
            )
            for update in resp.json().get("result", []):
                offset  = update["update_id"] + 1
                msg     = update.get("message", {})
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text = msg.get("text", "").strip().lower()
                # Strip bot username suffix (e.g. /stats@MVLK_orders_bot → /stats)
                if "@" in text:
                    text = text.split("@")[0]

                if chat_id != str(TELEGRAM_CHAT_ID):
                    print(f"[Commands] Ignored message from chat {chat_id}")
                    continue

                if text in ("/stats",):
                    send_stats_now()
                elif text in ("/help",):
                    send_telegram(
                        "📋 <b>Available commands:</b>\n"
                        "/stats — today's stats\n"
                        "/help — list of commands"
                    )
        except Exception as e:
            print(f"[Commands ERROR] {e}")
            time.sleep(5)


# ─── START ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"[Start] Bot running. Polling every {POLL_INTERVAL_SEC}s | TZ: {TIMEZONE}")
    load_source_map()
    send_telegram("✅ <b>Allegro Bot started</b>\nMonitoring orders and messages...")

    scheduler = BackgroundScheduler(timezone=tz)
    scheduler.add_job(poll,               IntervalTrigger(seconds=POLL_INTERVAL_SEC))
    scheduler.add_job(send_daily_summary, CronTrigger(hour=23, minute=59, timezone=tz))
    scheduler.start()

    poll()

    threading.Thread(target=listen_commands, daemon=True).start()

    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        print("[Stop] Bot stopped.")
