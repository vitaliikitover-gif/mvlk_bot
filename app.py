"""
Allegro → Telegram Bot (API Polling)
Checks new orders and returns every 2 minutes via Baselinker API.
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

# Status IDs that mean "return" — loaded at startup
RETURN_STATUS_IDS: set = set()

state = {
    "last_order_check":  int(time.time()) - 300,
    "seen_order_ids":    set(),
    "seen_return_ids":   set(),  # track orders already flagged as returns
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


def load_return_statuses():
    """Find status IDs that contain 'zwrot' in their name."""
    global RETURN_STATUS_IDS
    data = bl_request("getOrderStatusList", {})
    statuses = data.get("statuses", [])
    # API returns either a list or a dict
    if isinstance(statuses, list):
        for item in statuses:
            name = str(item.get("name", "")).lower()
            sid  = str(item.get("id", ""))
            if "zwrot" in name and sid:
                RETURN_STATUS_IDS.add(sid)
    else:
        for sid, info in statuses.items():
            name = info.get("name", "").lower() if isinstance(info, dict) else str(info).lower()
            if "zwrot" in name:
                RETURN_STATUS_IDS.add(str(sid))
    print(f"[Statuses] Return status IDs: {RETURN_STATUS_IDS}")


def get_account_name(order: dict) -> str:
    source_id = str(order.get("order_source_id", ""))
    return SOURCE_MAP.get(source_id) or order.get("order_source", "Allegro")

def is_return_order(order: dict) -> bool:
    status_id = str(order.get("order_status_id", ""))
    return status_id in RETURN_STATUS_IDS


# ─── CHECK ORDERS ─────────────────────────────────────────────────────────────
def check_orders():
    now = int(time.time())
    data = bl_request("getOrders", {
        "date_confirmed_from":    state["last_order_check"],
        "get_unconfirmed_orders": False,
    })
    for order in data.get("orders", []):
        order_id = str(order.get("order_id", ""))
        account  = get_account_name(order)
        amount   = float(order.get("payment_done", 0) or order.get("price_brutto", 0) or 0)

        if is_return_order(order):
            # New return notification (only once per order)
            if order_id in state["seen_return_ids"]:
                continue
            state["seen_return_ids"].add(order_id)
            state["seen_order_ids"].add(order_id)
            send_telegram(
                f"🔴 <b>Return</b>\n"
                f"🏪 {account}\n"
                f"💸 -{amount:.2f} zł"
            )
        else:
            # New order notification
            if order_id in state["seen_order_ids"]:
                continue
            state["seen_order_ids"].add(order_id)

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
    if len(state["seen_return_ids"]) > 5000:
        state["seen_return_ids"] = set(list(state["seen_return_ids"])[-2000:])


# ─── POLL ─────────────────────────────────────────────────────────────────────
def poll():
    check_orders()


# ─── STATS ────────────────────────────────────────────────────────────────────
def fetch_todays_stats() -> dict:
    """
    Returns per-account stats for today:
    {
      "mvlk":    {"sales_count": 5, "sales_total": 500.0, "returns_count": 1, "returns_total": 89.0},
      "mvlk_pl": {...},
    }
    """
    now = datetime.now(tz)
    start_of_day = int(datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=tz).timestamp())

    per_account = {}
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

            account = get_account_name(order)
            amount  = float(order.get("payment_done", 0) or order.get("price_brutto", 0) or 0)

            if account not in per_account:
                per_account[account] = {
                    "sales_count": 0, "sales_total": 0.0,
                    "returns_count": 0, "returns_total": 0.0,
                }

            if is_return_order(order):
                per_account[account]["returns_count"] += 1
                per_account[account]["returns_total"] += amount
            else:
                per_account[account]["sales_count"] += 1
                per_account[account]["sales_total"] += amount

        if not new_orders:
            break

    return per_account


def build_stats_message(per_account: dict, title: str, time_str: str = "") -> str:
    if not per_account:
        return f"📊 <b>{title}</b>\nNo orders today."

    header = f"📊 <b>{title}</b>" + (f" ({time_str})" if time_str else "")
    lines = [header]

    total_sales = 0.0
    total_returns = 0.0

    for account, s in sorted(per_account.items()):
        net  = s["sales_total"] - s["returns_total"]
        sign = "+" if net >= 0 else ""
        lines.append(
            f"\n🏪 <b>{account}</b>\n"
            f"  🟢 {s['sales_count']} orders / +{s['sales_total']:.2f} zł\n"
            f"  🔴 {s['returns_count']} returns / -{s['returns_total']:.2f} zł\n"
            f"  💰 Net: {sign}{net:.2f} zł"
        )
        total_sales   += s["sales_total"]
        total_returns += s["returns_total"]

    if len(per_account) > 1:
        total_net  = total_sales - total_returns
        total_sign = "+" if total_net >= 0 else ""
        lines.append(
            f"\n━━━━━━━━━━━━━━━━\n"
            f"💰 <b>Total: {total_sign}{total_net:.2f} zł</b>"
        )

    return "\n".join(lines)


def send_stats_now():
    now_str = datetime.now(tz).strftime("%H:%M")
    send_telegram("⏳ Fetching data from Baselinker...")
    per_account = fetch_todays_stats()
    send_telegram(build_stats_message(per_account, "Today's stats", now_str))


def send_daily_summary():
    per_account = fetch_todays_stats()
    send_telegram(build_stats_message(per_account, "Daily summary"))


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
                text    = msg.get("text", "").strip().lower()
                if "@" in text:
                    text = text.split("@")[0]

                if chat_id not in (str(TELEGRAM_CHAT_ID), "421633181"):
                    continue

                if text == "/stats":
                    send_stats_now()
                elif text == "/debug7":
                    send_telegram(f"🔍 Return status IDs found: {RETURN_STATUS_IDS or 'NONE'}")
                    raw = bl_request("getOrders", {
                        "date_confirmed_from": int(time.time()) - 86400 * 14,
                        "get_unconfirmed_orders": False,
                    })
                    orders = raw.get("orders", [])
                    status_counts = {}
                    for o in orders:
                        sid = str(o.get("order_status_id", ""))
                        status_counts[sid] = status_counts.get(sid, 0) + 1
                    msg = f"📋 Statuses in last 14 days ({len(orders)} orders):\n"
                    for sid, cnt in sorted(status_counts.items()):
                        flag = " ← RETURN" if sid in RETURN_STATUS_IDS else ""
                        msg += f"ID {sid}: {cnt}{flag}\n"
                    send_telegram(msg[:3500])
                elif text == "/help":
                    send_telegram(
                        "📋 <b>Available commands:</b>\n"
                        "/stats — today's stats per account\n"
                        "/help — list of commands"
                    )
        except Exception as e:
            print(f"[Commands ERROR] {e}")
            time.sleep(5)


# ─── START ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"[Start] Bot running. Polling every {POLL_INTERVAL_SEC}s | TZ: {TIMEZONE}")
    load_source_map()
    load_return_statuses()
    send_telegram("✅ <b>Allegro Bot started</b>\nMonitoring orders and returns...")

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
