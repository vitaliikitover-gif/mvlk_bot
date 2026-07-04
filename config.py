"""
Конфигурация интеграции Lagrana <-> Baselinker.
Все секреты берутся ТОЛЬКО из переменных окружения — никогда не хардкодь
логин/пароль/токены прямо в коде.

При деплое на Railway задай эти переменные в разделе Variables проекта.
"""
import os

# ── Lagrana B2B API ──────────────────────────────────────────────────────
LAGRANA_BASE_URL = "https://b2b.lagrana.pl/api"
LAGRANA_USERNAME = os.environ["LAGRANA_USERNAME"]
LAGRANA_PASSWORD = os.environ["LAGRANA_PASSWORD"]

# ── Baselinker API ───────────────────────────────────────────────────────
BASELINKER_API_URL = "https://api.baselinker.com/connector.php"
BASELINKER_TOKEN = os.environ["BASELINKER_TOKEN"]

# ID склада (inventory) в Baselinker, куда заливаем товары Lagrana.
# Найти можно в BL: Каталог продуктов -> настройки склада -> ID в URL.
BASELINKER_INVENTORY_ID = os.environ["BASELINKER_INVENTORY_ID"]

# ID склада (magazyn/warehouse) ВНУТРИ каталога, в который пишем остатки.
# У тебя склад "La Grana" = 145822. Ключ в API имеет вид "bl_145822".
BASELINKER_WAREHOUSE_ID = os.environ["BASELINKER_WAREHOUSE_ID"]

# Источник остатков из Lagrana:
#   warehouse — только stock_warehouse (физически на складе Lagrana)
#   supplier  — только stock_supplier (у поставщика Lagrana)
#   sum       — stock_warehouse + stock_supplier (общая доступность)
STOCK_SOURCE = os.environ.get("STOCK_SOURCE", "sum").lower()

# Задержка между запросами к Baselinker API (в секундах).
# Лимит Baselinker — 100 запросов/мин на токен. 0.7 сек = ~85 запросов/мин,
# оставляем запас на случай других интеграций на том же токене.
BASELINKER_CALL_DELAY = float(os.environ.get("BASELINKER_CALL_DELAY", "0.7"))

# ID прайс-листа (price group), в который пишем цены.
BASELINKER_PRICE_GROUP_ID = os.environ["BASELINKER_PRICE_GROUP_ID"]

# Категория по умолчанию в BL для товаров без явного маппинга категорий.
BASELINKER_DEFAULT_CATEGORY_ID = os.environ.get("BASELINKER_DEFAULT_CATEGORY_ID", "0")

# Наценка поверх client_price Lagrana. По умолчанию 1.0 — заливаем актуальную
# цену Lagrana как есть, без наценки. Если позже понадобится наценка — поменяй здесь.
PRICE_MARKUP_MULTIPLIER = float(os.environ.get("PRICE_MARKUP_MULTIPLIER", "1.0"))

# Префикс SKU, по которому распознаём товары Lagrana среди прочих в BL
# (если в одном складе BL смешаны товары разных поставщиков).
# Например все SKU Lagrana начинаются с "LG-".
LAGRANA_SKU_PREFIX = os.environ.get("LAGRANA_SKU_PREFIX", "")

# Интервалы синхронизации (в секундах), если скрипт работает в режиме демона.
CATALOG_SYNC_INTERVAL = int(os.environ.get("CATALOG_SYNC_INTERVAL", str(6 * 3600)))   # раз в 6 часов
STOCK_SYNC_INTERVAL = int(os.environ.get("STOCK_SYNC_INTERVAL", str(15 * 60)))         # раз в 15 минут
