# Asystent UDIP – Urząd Miasta Bydgoszczy (HackNation)

Kalendarz wniosków o dostęp do informacji publicznej + generator projektów odpowiedzi + anonimizacja.

## Instalacja na serwerze (Ubuntu 22.04/24.04)
```bash
scp asystent-udip.tar.gz root@SERWER:/root/
ssh root@SERWER
tar xzf asystent-udip.tar.gz && cd asystent-udip
sudo bash install.sh udip.twojadomena.pl jury 'MocneHaslo123'   # albo IP zamiast domeny (wtedy http)
nano /opt/asystent-udip/.env        # OPENAI_API_KEY=sk-...
cd /opt/asystent-udip && docker compose up -d
```
Logi: `docker compose logs -f app` · Restart: `docker compose restart app` · Reset demo: `rm db/udip.db && docker compose restart app`

## Lokalnie (dev)
```bash
pip install -r requirements.txt && cp .env.example .env
export $(grep -v '^#' .env | xargs) && uvicorn app.main:app --reload
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
