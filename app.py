"""
Allegro → Telegram Bot (API Polling)
Co 2 minuty sprawdza nowe zamówienia/zwroty/wiadomości w Baselinker
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
POLL_INTERVAL_SEC   = int(os.getenv("POLL_INTERVAL",  "120"))   # co ile sekund sprawdzać
BASELINKER_API_URL  = "https://api.baselinker.com/connector.php"
# ──────────────────────────────────────────────────────────────────────────────

tz = pytz.timezone(TIMEZONE)

# Zapamiętujemy ostatni sprawdzony czas (unix timestamp)
state = {
    "last_order_check":   int(time.time()) - 300,   # start: 5 min wstecz
    "last_message_check": int(time.time()) - 300,
    "seen_order_ids":     set(),
    "seen_message_ids":   set(),
}

# Liczniki dzienne
daily_stats = {
    "sales_count":   0,
    "sales_total":   0.0,
    "returns_count": 0,
    "returns_total": 0.0,
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
    print(f"[Poll] Sprawdzam zamówienia... (od {state['last_order_check']})")
    now = int(time.time())

    data = bl_request("getOrders", {
        "date_confirmed_from": state["last_order_check"],
        "get_unconfirmed_orders": False,
    })

    orders = data.get("orders", [])
    new_last = state["last_order_check"]

    for order in orders:
        order_id = str(order.get("order_id", ""))
        if order_id in state["seen_order_ids"]:
            continue
        state["seen_order_ids"].add(order_id)

        # Czas zamówienia
        order_time = order.get("date_add", 0)
        if order_time > new_last:
            new_last = order_time

        # Kwota
        amount = float(order.get("payment_done", 0) or order.get("price_brutto", 0) or 0)

        # Konto / sklep
        source_id   = order.get("order_source_id", "")
        source_info = order.get("order_source", "Allegro")
        account     = order.get("extra_field_1") or source_info or "Allegro"

        # Produkt (pierwszy z listy)
        products = order.get("products", [])
        product_name = products[0].get("name", "") if products else ""

        # Zwrot / anulowanie — sprawdzamy status
        status = str(order.get("order_status_id", ""))
        is_return = order.get("is_return", False) or status in ("140", "141", "142")  # typowe statusy zwrotu BL

        if is_return:
            daily_stats["returns_count"] += 1
            daily_stats["returns_total"] += amount

            msg = (
                f"🔴 <b>Zwrot / Anulowanie</b>\n"
                f"🏪 Konto: {account}\n"
                f"💸 Kwota: -{amount:.2f} zł\n"
                f"🔢 Nr: {order_id}"
            )
            if product_name:
                msg += f"\n📦 {product_name}"
            send_telegram(msg)

        else:
            daily_stats["sales_count"] += 1
            daily_stats["sales_total"] += amount

            msg = (
                f"🟢 <b>Nowe zamówienie</b>\n"
                f"🏪 Konto: {account}\n"
                f"💵 Kwota: +{amount:.2f} zł\n"
                f"🔢 Nr: {order_id}"
            )
            if product_name:
                msg += f"\n📦 {product_name}"
            send_telegram(msg)

    state["last_order_check"] = now
    # Trzymamy maks 5000 ID w pamięci
    if len(state["seen_order_ids"]) > 5000:
        state["seen_order_ids"] = set(list(state["seen_order_ids"])[-2000:])


# ─── SPRAWDZANIE WIADOMOŚCI ───────────────────────────────────────────────────
def check_messages():
    print(f"[Poll] Sprawdzam wiadomości...")
    now = int(time.time())

    data = bl_request("getOrderMessages", {
        "date_from": state["last_message_check"],
    })

    messages = data.get("messages", [])

    for msg_data in messages:
        msg_id = str(msg_data.get("message_id", ""))
        if msg_id in state["seen_message_ids"]:
            continue
        state["seen_message_ids"].add(msg_id)

        # Tylko wiadomości od kupującego (type=1 w BL)
        msg_type = msg_data.get("type", 0)
        if msg_type not in (1, "1"):
            continue

        order_id = str(msg_data.get("order_id", "—"))
        buyer    = msg_data.get("login") or msg_data.get("author") or "Kupujący"
        content  = msg_data.get("message", "") or ""
        if len(content) > 120:
            content = content[:120] + "…"

        msg = (
            f"💬 <b>Nowa wiadomość</b>\n"
            f"👤 Od: {buyer}\n"
            f"🔢 Zamówienie: {order_id}\n"
            f"✉️ {content}"
        )
        send_telegram(msg)

    state["last_message_check"] = now
    if len(state["seen_message_ids"]) > 5000:
        state["seen_message_ids"] = set(list(state["seen_message_ids"])[-2000:])


# ─── POLLING: oba zadania razem ───────────────────────────────────────────────
def poll():
    check_orders()
    check_messages()


# ─── OBSŁUGA KOMEND TELEGRAM ──────────────────────────────────────────────────
def send_stats_now():
    """Wysyła aktualną statystykę na żądanie."""
    now_str = datetime.now(tz).strftime("%H:%M")
    net  = daily_stats["sales_total"] - daily_stats["returns_total"]
    sign = "+" if net >= 0 else ""
    msg = (
        f"📊 <b>Statystyki na dziś ({now_str})</b>\n"
        "━━━━━━━━━━━━━━━━\n"
        f"🟢 Sprzedaż:  {daily_stats['sales_count']} zam. / +{daily_stats['sales_total']:.2f} zł\n"
        f"🔴 Zwroty:    {daily_stats['returns_count']} szt. / -{daily_stats['returns_total']:.2f} zł\n"
        "━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Wynik: {sign}{net:.2f} zł</b>"
    )
    send_telegram(msg)


def listen_commands():
    """Słucha komend od użytkownika przez long polling Telegram."""
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
                msg = update.get("message", {})
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text = msg.get("text", "").strip().lower()

                # Только от авторизованного чата
                if chat_id != str(TELEGRAM_CHAT_ID):
                    continue

                if text in ("/stats", "/statystyki"):
                    send_stats_now()
                elif text in ("/help", "/pomoc"):
                    send_telegram(
                        "📋 <b>Dostępne komendy:</b>\n"
                        "/stats — statystyki za dziś\n"
                        "/help — lista komend"
                    )
        except Exception as e:
            print(f"[Commands ERROR] {e}")
            time.sleep(5)


# ─── PODSUMOWANIE DNIA ────────────────────────────────────────────────────────
def send_daily_summary():
    net  = daily_stats["sales_total"] - daily_stats["returns_total"]
    sign = "+" if net >= 0 else ""
    msg = (
        "📊 <b>Podsumowanie dnia</b>\n"
        "━━━━━━━━━━━━━━━━\n"
        f"🟢 Sprzedaż:  {daily_stats['sales_count']} zam. / +{daily_stats['sales_total']:.2f} zł\n"
        f"🔴 Zwroty:    {daily_stats['returns_count']} szt. / -{daily_stats['returns_total']:.2f} zł\n"
        "━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Wynik: {sign}{net:.2f} zł</b>"
    )
    send_telegram(msg)


def reset_daily_stats():
    for key in daily_stats:
        daily_stats[key] = 0.0 if "total" in key else 0
    print("[Scheduler] Statystyki zresetowane.")


# ─── START ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"[Start] Bot uruchomiony. Polling co {POLL_INTERVAL_SEC}s | TZ: {TIMEZONE}")
    send_telegram("✅ <b>Bot Allegro uruchomiony</b>\nMonitoruję zamówienia i wiadomości...")

    scheduler = BackgroundScheduler(timezone=tz)
    scheduler.add_job(poll,               IntervalTrigger(seconds=POLL_INTERVAL_SEC))
    scheduler.add_job(send_daily_summary, CronTrigger(hour=23, minute=59, timezone=tz))
    scheduler.add_job(reset_daily_stats,  CronTrigger(hour=0,  minute=0,  timezone=tz))
    scheduler.start()

    # Pierwsze sprawdzenie od razu
    poll()

    # Wątek słuchający komend (/stats, /help)
    cmd_thread = threading.Thread(target=listen_commands, daemon=True)
    cmd_thread.start()

    # Trzymamy proces żywy
    try:
        while True:
            time.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown()
        print("[Stop] Bot zatrzymany.")
