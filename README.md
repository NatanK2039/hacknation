# Asystent UDIP – Urząd Miasta Bydgoszczy (HackNation)

Kalendarz wniosków o dostęp do informacji publicznej + generator projektów odpowiedzi + anonimizacja.

## Instalacja na serwerze (Ubuntu 22.04/24.04)
```bash
scp asystent-udip.tar.gz root@SERWER:/root/
ssh root@SERWER
tar xzf asystent-udip.tar.gz && cd asystent-udip
sudo bash install.sh udip.twojadomena.pl 'MocneHaslo123'   # hasło konta admin; albo IP zamiast domeny (wtedy http)
nano /opt/asystent-udip/.env        # OPENAI_API_KEY=sk-...
cd /opt/asystent-udip && docker compose up -d
```
Logi: `docker compose logs -f app` · Restart: `docker compose restart app` · Reset demo: `rm db/udip.db && docker compose restart app`

## Konta i role
Logowanie w aplikacji (sesja w ciasteczku HttpOnly, 12 h, blokada po 5 błędnych próbach). Konta startowe z `.env` (`ADMIN_PASSWORD`, `JURY_PASSWORD`, `DEMO_WYDZIAL_PASSWORD`), kolejne w panelu **Administracja → Użytkownicy** (hasło tymczasowe, wymuszona zmiana przy pierwszym logowaniu).

| Rola | Uprawnienia |
|---|---|
| Administrator | wszystko + panel administracyjny, konta, kopie zapasowe |
| Urzędnik AIP | pełna obsługa wniosków, bez panelu administracyjnego |
| Wydział | tylko sprawy przypisane do swojej komórki; wgrywa do nich odpowiedzi |
| Podgląd | tylko odczyt |

Uprawnienia sprawdza serwer (middleware), nie tylko interfejs. Każda odmowa i logowanie trafia do dziennika zdarzeń.

## Lokalnie (dev)
```bash
pip install -r requirements.txt && cp .env.example .env
export $(grep -v '^#' .env | xargs) && COOKIE_SECURE=0 uvicorn app.main:app --reload
```

## Tryb offline (docelowo w urzędzie)
Kod używa API zgodnego z OpenAI. Zmiana w `.env`:
```
OPENAI_BASE_URL=http://ollama:11434/v1
LLM_MODEL=SpeakLeash/bielik-11b-v2.3-instruct:Q4_K_M
```
+ odkomentowanie usługi `ollama` w `docker-compose.yml`. Bez zmian w kodzie.

## Pliki testowe
`samples/` – wnioski (PDF, DOCX, skan), odpowiedź wydziału z danymi osobowymi, protokół do testu anonimizacji. Wszystkie dane fikcyjne.

## Struktura
- `app/main.py` – API (FastAPI) · `app/ai.py` – prompty LLM · `app/anonymize.py` – reguły PESEL/NIP/IBAN/… + LLM
- `app/deadlines.py` – terminy art. 13 UDIP + święta · `app/extract.py` – PDF/DOCX/OCR · `static/index.html` – UI
- `data/departments.json` – lista komórek UMB (do weryfikacji z BIP)
