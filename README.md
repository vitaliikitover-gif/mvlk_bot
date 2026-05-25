# 🛒 Allegro → Telegram Bot (API Polling)

Co 2 minuty sprawdza Baselinker i wysyła powiadomienia do Telegram.

---

## 📩 Przykładowe powiadomienia

**Nowe zamówienie:**
```
🟢 Nowe zamówienie
🏪 Konto: Mój Sklep Allegro
💵 Kwota: +249.99 zł
🔢 Nr: 12345678
📦 Słuchawki Bluetooth XYZ
```

**Zwrot:**
```
🔴 Zwrot / Anulowanie
🏪 Konto: Mój Sklep Allegro
💸 Kwota: -89.00 zł
🔢 Nr: 12345678
```

**Wiadomość od kupującego:**
```
💬 Nowa wiadomość
👤 Od: kowalski123
🔢 Zamówienie: 12345678
✉️ Kiedy wyślecie zamówienie?
```

**Podsumowanie dnia (23:59):**
```
📊 Podsumowanie dnia
━━━━━━━━━━━━━━━━
🟢 Sprzedaż:  12 zam. / +3 450.00 zł
🔴 Zwroty:    1 szt. / -89.00 zł
━━━━━━━━━━━━━━━━
💰 Wynik: +3 361.00 zł
```

---

## 🚀 Uruchomienie na Railway

### Krok 1 — Utwórz bota Telegram
1. Napisz do @BotFather → /newbot → skopiuj token
2. Napisz cokolwiek do swojego bota, potem otwórz:
   https://api.telegram.org/bot<TOKEN>/getUpdates
3. Skopiuj wartość "id" z sekcji "chat" — to twój CHAT_ID

### Krok 2 — Wgraj na GitHub
1. Utwórz nowe repozytorium na github.com
2. Wgraj wszystkie pliki z tego folderu

### Krok 3 — Deploy na Railway
1. railway.app → New Project → Deploy from GitHub
2. Wybierz swoje repozytorium
3. W zakładce Variables dodaj:
   - TELEGRAM_BOT_TOKEN = twój_token_telegram
   - TELEGRAM_CHAT_ID   = twój_chat_id
   - BASELINKER_TOKEN   = twój_token_z_baselinker
   - TIMEZONE           = Europe/Warsaw
   - POLL_INTERVAL      = 120
4. W zakładce Settings ustaw:
   - Start Command: python app.py

### Krok 4 — Gotowe!
Po uruchomieniu przyjdzie wiadomość:
✅ Bot Allegro uruchomiony
