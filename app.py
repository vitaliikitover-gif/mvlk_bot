"""
Allegro → Telegram Bot (API Polling)
Co 2 minuty sprawdza nowe zamówienia i wiadomości w Baselinker
i wysyła powiadomienia do Telegram.
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

# ─── KONFIGURACJA ─────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "TWÓJ_TOKEN_BOTA")
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID",   "TWÓJ_CHAT_ID")
BASELINKER_TOKEN    = os.getenv("BASELINKER_TOKEN",    "TWÓJ_TOKEN_BASELINKER")
TIMEZONE            = os.getenv("TIMEZONE",            "Europe/Warsaw")
POLL_INTERVAL_SEC   = int(os.getenv("POLL_INTERVAL",  "120"))
BASELINKER_API_URL  = "https://api.baselinker.com/connector.php"
# ──────────────────────────────────────────────────────────────────────────────

tz = pytz.timezone(TIMEZONE)

# Mapa source_id → nazwa konta (ładowana przy starcie)
SOURCE_MAP: dict = {}

def load_source_map():
    """Pobiera nazwy kont Allegro z Baselinker."""
    global SOURCE_MAP
    data = bl_request("getOrderSources", {})
    allegro = data.get("sources", {}).get("allegro", {})
    SOURCE_MAP = {str(k): v for k, v in allegro.items()}
    print(f"[Sources] Załadowano: {SOURCE_MAP}")

def get_account_name(order: dict) -> str:
    source_id = str(order.get("order_source_id", ""))
    return SOURCE_MAP.get(source_id) or order.get("order_source", "Allegro")

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
        print(f"[TG] Wysłano: {text[:60]}...")
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


# ─── SPRAWDZANIE ZAMÓWIEŃ ─────────────────────────────────────────────────────
def check_orders():
    print(f"[Poll] Sprawdzam zamówienia...")
    now = int(time.time())

    data = bl_request("getOrders", {
        "date_confirmed_from": state["last_order_check"],
        "get_unconfirmed_orders": False,
    })

    for order in data.get("orders", []):
        order_id = str(order.get("order_id", ""))
        if order_id in state["seen_order_ids"]:
            continue
        state["seen_order_ids"].add(order_id)

        amount       = float(order.get("payment_done", 0) or order.get("price_brutto", 0) or 0)
        account      = get_account_name(order)
        products     = order.get("products", [])
        is_return    = order.get("order_source") == "order_return"

        # All products
        product_lines = ""
        for p in products:
            name = p.get("name", "")
            qty  = p.get("quantity", 1)
            if name:
                product_lines += f"\n📦 {name}" + (f" x{qty}" if qty > 1 else "")

        if is_return:
            msg = (
                f"🔴 <b>Return</b>\n"
                f"🏪 {account}\n"
                f"💸 -{amount:.2f} zł"
            )
        else:
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


# ─── SPRAWDZANIE WIADOMOŚCI ───────────────────────────────────────────────────
def check_messages():
    print(f"[Poll] Sprawdzam wiadomości...")
    now = int(time.time())

    data = bl_request("getOrderMessages", {
        "date_from": state["last_message_check"],
    })

    for msg_data in data.get("messages", []):
        msg_id = str(msg_data.get("message_id", ""))
        if msg_id in state["seen_message_ids"]:
            continue
        state["seen_message_ids"].add(msg_id)

        msg_type = msg_data.get("type", 0)
        if msg_type not in (1, "1"):
            continue

        order_id = str(msg_data.get("order_id", "—"))
        buyer    = msg_data.get("login") or msg_data.get("author") or "Kupujący"
        content  = msg_data.get("message", "") or ""
        if len(content) > 120:
            content = content[:120] + "…"

        msg = (
            f"💬 <b>New message</b>\n"
            f"👤 From: {buyer}\n"
            f"🔢 Order: {order_id}\n"
            f"✉️ {content}"
        )
        send_telegram(msg)

    state["last_message_check"] = now
    if len(state["seen_message_ids"]) > 5000:
        state["seen_message_ids"] = set(list(state["seen_message_ids"])[-2000:])


# ─── POLLING ──────────────────────────────────────────────────────────────────
def poll():
    check_orders()
    check_messages()


# ─── STATYSTYKI (pobierane z Baselinker w czasie rzeczywistym) ────────────────
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
            amount = float(order.get("payment_done", 0) or order.get("price_brutto", 0) or 0)
            order_time = order.get("date_add", cursor)
            if order_time > cursor:
                cursor = order_time
            sales_count += 1
            sales_total += amount
        if not new_orders:
            break

    return {"sales_count": sales_count, "sales_total": sales_total}


def send_stats_now():
    now_str = datetime.now(tz).strftime("%H:%M")
    send_telegram("⏳ Fetching data from Baselinker...")
    stats = fetch_todays_stats()
    msg = (
        f"📊 <b>Today's stats ({now_str})</b>\n"
        "━━━━━━━━━━━━━━━━\n"
        f"🟢 Sales: {stats['sales_count']} orders\n"
        f"💰 <b>Total: +{stats['sales_total']:.2f} zł</b>"
    )
    send_telegram(msg)


def send_daily_summary():
    stats = fetch_todays_stats()
    msg = (
        "📊 <b>Daily summary</b>\n"
        "━━━━━━━━━━━━━━━━\n"
        f"🟢 Sales: {stats['sales_count']} orders\n"
        f"💰 <b>Total: +{stats['sales_total']:.2f} zł</b>"
    )
    send_telegram(msg)


# ─── KOMENDY TELEGRAM ─────────────────────────────────────────────────────────
def listen_commands():
    offset = None
    print("[Commands] Słucham komend...")
    while True:
        try:
            params = {"timeout": 30, "allowed_updates": ["message"]}
            if offset:
                params["offset"] = offset
            resp = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
                params=params, timeout=40
            )
            data = resp.json()
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                msg     = update.get("message", {})
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text    = msg.get("text", "").strip().lower()

                if chat_id != str(TELEGRAM_CHAT_ID):
                    continue

                if text in ("/stats", "/statystyki"):
                    send_stats_now()
                elif text in ("/debug2",):
                    send_telegram("🔍 Sprawdzam pola zamówienia...")
                    raw = bl_request("getOrders", {
                        "date_confirmed_from": int(time.time()) - 86400 * 7,
                        "get_unconfirmed_orders": False,
                    })
                    orders = raw.get("orders", [])
                    if not orders:
                        send_telegram("❌ Brak zamówień z ostatnich 7 dni")
                    else:
                        first = orders[0]
                        fields = {k: v for k, v in first.items() if k not in ("products",)}
                        send_telegram(f"📋 Pola:\n{json.dumps(fields, ensure_ascii=False, indent=1)[:3000]}")
                elif text in ("/debug4",):
                    send_telegram("🔍 Szukam nazw integracji...")
                    # Próbujemy różne metody
                    for method in ("getOrderSources", "getIntegrationsList", "getStoragesList"):
                        raw = bl_request(method, {})
                        if raw:
                            send_telegram(f"✅ {method}:\n{json.dumps(raw, ensure_ascii=False)[:2000]}")
                        else:
                            send_telegram(f"❌ {method} — niedostępne")
                elif text in ("/help", "/pomoc"):
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
    print(f"[Start] Bot uruchomiony. Polling co {POLL_INTERVAL_SEC}s | TZ: {TIMEZONE}")
    load_source_map()
    send_telegram("✅ <b>Allegro Bot started</b>\nMonitoring orders and messages...")

    scheduler = BackgroundScheduler(timezone=tz)
    scheduler.add_job(poll,              IntervalTrigger(seconds=POLL_INTERVAL_SEC))
    scheduler.add_job(send_daily_summary, CronTrigger(hour=23, minute=59, timezone=tz))
    scheduler.start()

    poll()

    cmd_thread = threading.Thread(target=listen_commands, daemon=True)
    cmd_thread.start()

    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        print("[Stop] Bot zatrzymany.")
