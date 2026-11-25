# Tracker obniżek Euro RTV AGD

Skanuje strony z urls.txt, wykrywa produkty z obniżką co najmniej 70% i wysyła powiadomienie na Telegram.
Powiadamia tylko o nowych okazjach (albo gdy cena spadła jeszcze bardziej).

## Konfiguracja Telegrama
1. W Telegramie napisz do @BotFather, wyślij /newbot i skopiuj token.
2. Napisz cokolwiek do swojego nowego bota, potem otwórz w przeglądarce
   https://api.telegram.org/bot<TOKEN>/getUpdates i odczytaj `chat` → `id`.

## Uruchomienie w GitHub Actions
1. Wrzuć te pliki do nowego repozytorium na GitHubie.
2. Settings → Secrets and variables → Actions → dodaj `TELEGRAM_BOT_TOKEN` i `TELEGRAM_CHAT_ID`.
3. Zakładka Actions → "Euro price tracker" → Run workflow (test ręczny).

## Uruchomienie lokalnie
    pip install -r requirements.txt
    python tracker.py --selftest     # test parsera
    python tracker.py --dump         # pobierz strony do debug/ bez wysyłania powiadomień
    TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python tracker.py

## Ważne
- Parser opiera się na tekstach "Przed obniżką" / "Najniższa cena" widocznych przy produktach, nie na klasach CSS.
  Jeśli skrypt zwróci 0 produktów, uruchom z `--dump` i sprawdź zapisany HTML w debug/ (blokada antybotowa lub zmiana układu).
- Obniżka liczona jest względem ceny "Przed obniżką", a jeśli jej brak, względem "Najniższej ceny z 30 dni".
  Sklepy bywają zawyżały ceny przed promocją, więc przy 70% warto sprawdzić historię ceny na Ceneo.
- Progi: `MIN_DISCOUNT` (domyślnie 70), `MIN_PRICE` (np. 100, żeby ignorować drobiazgi).
