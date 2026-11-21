"""Warstwa AI. Klient zgodny z OpenAI API:
- hackathon: OpenAI (OPENAI_API_KEY)
- docelowo w urzędzie: Ollama / vLLM lokalnie (OPENAI_BASE_URL=http://ollama:11434/v1, LLM_MODEL=bielik...)
Ten sam kod, zmiana 2 zmiennych w .env."""
import json
import os

from openai import OpenAI

_client = None


def client() -> OpenAI:
    global _client
    if _client is None:
        kw = {"api_key": os.getenv("OPENAI_API_KEY", "ollama")}
        if os.getenv("OPENAI_BASE_URL"):
            kw["base_url"] = os.getenv("OPENAI_BASE_URL")
        _client = OpenAI(**kw)
    return _client


def model() -> str:
    return os.getenv("LLM_MODEL", "gpt-4o-mini")


def enabled() -> bool:
    return bool(os.getenv("OPENAI_API_KEY") or os.getenv("OPENAI_BASE_URL"))


def chat_json(system: str, user: str, temperature: float = 0.1) -> dict:
    r = client().chat.completions.create(
        model=model(),
        temperature=temperature,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    txt = r.choices[0].message.content or "{}"
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        s, e = txt.find("{"), txt.rfind("}")
        return json.loads(txt[s:e + 1]) if s >= 0 else {}


def chat_text(system: str, user: str, temperature: float = 0.2) -> str:
    r = client().chat.completions.create(
        model=model(),
        temperature=temperature,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return (r.choices[0].message.content or "").strip()


# ---------- zadania ----------

def extract_request_fields(text: str) -> dict:
    sys = ("Jesteś asystentem urzędnika Urzędu Miasta Bydgoszczy obsługującego wnioski o dostęp do informacji publicznej (UDIP). "
           "Z treści wniosku wyciągnij dane. Zwróć WYŁĄCZNIE JSON z kluczami: "
           "received_date (YYYY-MM-DD lub null – data wpływu/data pisma), requester (imię i nazwisko lub nazwa wnioskodawcy, null gdy brak), "
           "requester_type (mieszkaniec|dziennikarz|organizacja|firma|radny|inny), subject (temat, maks. 12 słów), "
           "summary (krótki opis czego dotyczy wniosek, 1-2 zdania), questions (lista pytań/żądań z wniosku – przepisane możliwie dosłownie, BEZ dopisywania imion, nazw ani faktów, których nie ma w treści), "
           "Nie uzupełniaj danych z własnej wiedzy – tylko to, co jest w piśmie. delivery (preferowany sposób udostępnienia: email|poczta|epuap|inny|null), number (sygnatura/numer jeśli występuje w piśmie, inaczej null).")
    return chat_json(sys, text[:15000])


def suggest_departments(text: str, summary: str, departments: list) -> list:
    dep_txt = "\n".join(f"- {d['code']}: {d['name']} – {d['scope']}" for d in departments)
    sys = ("Jesteś Administratorem Informacji Publicznej (AIP) Urzędu Miasta Bydgoszczy. Zgodnie z § 10 Zarządzenia nr 535/2024 "
           "przekazujesz zakres wniosku właściwym merytorycznie komórkom. Na podstawie wniosku UDIP wskaż maksymalnie 3 komórki "
           "organizacyjne, które powinny przygotować projekt odpowiedzi, od najbardziej właściwej. Korzystaj TYLKO z podanej listy (kody). "
           "Jeśli właściwa jest jednostka miejska spoza Urzędu (oznaczona 'poza Urzędem'), wskaż ją i zaznacz to w uzasadnieniu. "
           "Jeśli informacja zwykle jest publikowana w BIP (np. uchwały, budżet, interpelacje, wyniki konkursów), zaznacz to w polu bip_hint. "
           "Zwróć JSON: {\"departments\": [{\"code\": ..., \"confidence\": 0-100, \"reason\": \"krótkie uzasadnienie\", "
           "\"what_to_ask\": \"jakie dane konkretnie zamówić z wydziału\"}], \"processed_info_risk\": true/false, "
           "\"processed_info_note\": \"czy to może być informacja przetworzona (art. 3 ust. 1 pkt 1 UDIP) lub wymagać przedłużenia terminu\", "
           "\"protected_info_risk\": true/false, \"protected_info_note\": \"czy wniosek może dotyczyć informacji chronionej (art. 5 UDIP: prywatność, tajemnica przedsiębiorcy) – wtedy projekt decyzji odmownej (§ 11 ust. 9)\", "
           "\"bip_hint\": \"czy informacja może być już opublikowana w BIP (§ 8 ust. 1 – wtedy wskazuje się adres strony) albo null\"}")
    user = f"LISTA KOMÓREK:\n{dep_txt}\n\nSTRESZCZENIE: {summary}\n\nWNIOSEK:\n{text[:8000]}"
    return chat_json(sys, user)


def rerank_similar(request_text: str, candidates: list) -> list:
    if not candidates:
        return []
    cand = "\n\n".join(f"[{c['id']}] ({c['received_date']}) {c['subject']}\n{c['request_text'][:600]}" for c in candidates)
    sys = ("Oceń podobieństwo archiwalnych wniosków UDIP do nowego wniosku. Zwróć JSON {\"items\": [{\"id\": <int>, "
           "\"score\": 0-100, \"why\": \"1 zdanie\"}]} posortowane malejąco, maks. 5, pomiń zupełnie niepodobne (score<25).")
    out = chat_json(sys, f"NOWY WNIOSEK:\n{request_text[:4000]}\n\nARCHIWUM:\n{cand}")
    return out.get("items", [])


LETTER_SYS = """Jesteś doświadczonym urzędnikiem Urzędu Miasta Bydgoszczy. Przygotowujesz PROJEKT odpowiedzi na wniosek o udostępnienie
informacji publicznej na podstawie ustawy z dnia 6 września 2001 r. o dostępie do informacji publicznej (Dz.U. – tekst jednolity).
ZASADY (krytyczne):
1. Używaj WYŁĄCZNIE faktów z sekcji ODPOWIEDZI WYDZIAŁÓW. Nie wymyślaj liczb, dat, kwot, nazw ani przepisów.
2. Jeśli na któreś pytanie z wniosku brak danych w odpowiedziach wydziałów – wstaw w tym miejscu znacznik [[BRAK DANYCH: czego brakuje]].
3. Każde merytoryczne zdanie zakończ odnośnikiem do źródła w formie [źr. W1], [źr. W2] (numer odpowiedzi wydziału).
4. Styl: urzędowy, uprzejmy, zwięzły, odpowiedz na pytania w tej samej kolejności co we wniosku (numeruj).
5. Struktura: miejscowość i data (Bydgoszcz, {today}), znak sprawy, adresat (wnioskodawca), "Szanowni Państwo / Szanowna Pani / Szanowny Panie",
   wstęp z datą wpływu i przywołaniem art. 2 ust. 1 i art. 10 ust. 1 UDIP, odpowiedzi, ewentualnie pouczenie, podpis "Z poważaniem" + [[PODPIS]].
6. Jeśli wydział wskazał, że informacja jest przetworzona lub nie stanowi informacji publicznej – zaznacz to i wstaw [[DO DECYZJI URZĘDNIKA: ...]].
7. Procedura UMB (Zarządzenie nr 535/2024):
   - § 8 ust. 1: jeśli wydział wskazał, że informacja jest opublikowana w BIP – podaj dokładny adres strony BIP zamiast powielać treść.
   - § 11 ust. 3: jeśli wydział nie posiada informacji – poinformuj o tym i wskaż jednostkę właściwą, jeśli wydział ją podał.
   - § 11 ust. 7: informacja przetworzona – wstaw [[DO DECYZJI URZĘDNIKA: wezwanie do wykazania szczególnej istotności dla interesu publicznego]] z uzasadnieniem wydziału.
   - § 11 ust. 9: informacja chroniona prawem – NIE udostępniaj jej w piśmie; wstaw [[WYMAGANA DECYZJA ODMOWNA (art. 16 UDIP) – część: ...]].
   - § 11 ust. 2 / art. 14 UDIP: jeśli forma z wniosku jest niemożliwa – poinformuj o możliwej formie.
8. NIE przepisuj do pisma uwag wewnętrznych wydziału ani danych osobowych osób trzecich niepotrzebnych do odpowiedzi.
9. Jeśli w sekcji BAZA WIEDZY URZĘDU są przepisy lub instrukcje – stosuj je, ale nie cytuj ich obszernie.
Zwróć sam tekst pisma (bez markdown)."""


def draft_letter(req: dict, responses: list, similar: list, today: str, knowledge: str = "") -> str:
    resp_txt = "\n\n".join(f"=== W{i+1}: {r['department']} ===\n{r['text'][:6000]}" for i, r in enumerate(responses)) or "(brak)"
    sim_txt = "\n\n".join(f"--- wzór z archiwum ({s['received_date']}) ---\n{s['answer_text'][:1500]}" for s in similar[:2]) or "(brak)"
    user = (f"ZNAK SPRAWY: {req.get('number') or '[[ZNAK SPRAWY]]'}\nDATA WPŁYWU: {req.get('received_date')}\n"
            f"WNIOSKODAWCA: {req.get('requester') or '[[WNIOSKODAWCA]]'}\n\nTREŚĆ WNIOSKU:\n{req.get('content','')[:6000]}\n\n"
            f"ODPOWIEDZI WYDZIAŁÓW (jedyne źródło faktów):\n{resp_txt}\n\n"
            f"WZORY STYLU Z ARCHIWUM (tylko styl i układ, NIE fakty):\n{sim_txt}\n\n"
            f"BAZA WIEDZY URZĘDU (przepisy, zarządzenia, instrukcje):\n{knowledge or '(brak)'}")
    return chat_text(LETTER_SYS.replace("{today}", today), user)


PII_CATEGORIES = [
 ("OSOBA", "Imię i nazwisko osoby fizycznej (niepełniącej funkcji publicznej)"),
 ("PESEL", "Numer PESEL"),
 ("DOKUMENT", "Numer i seria dowodu osobistego lub innego dokumentu tożsamości"),
 ("NIP", "Numer NIP osoby fizycznej nieprowadzącej działalności gospodarczej"),
 ("DATA_UR", "Data i miejsce urodzenia"),
 ("ADRES", "Adres zamieszkania, zameldowania lub korespondencyjny osoby fizycznej"),
 ("TELEFON", "Prywatny numer telefonu"),
 ("EMAIL", "Prywatny adres poczty elektronicznej (e-mail)"),
 ("RACHUNEK", "Numer rachunku bankowego osoby fizycznej"),
 ("PODPIS", "Własnoręczny podpis (parafka) osoby niepełniącej funkcji publicznej"),
 ("WIZERUNEK", "Wizerunek osoby fizycznej niepełniącej funkcji publicznej"),
 ("RODZINA", "Stan cywilny i sytuacja rodzinna (np. liczba dzieci, imiona rodziców)"),
 ("ZDROWIE", "Dane dotyczące stanu zdrowia, niepełnosprawności lub nałogów"),
 ("MAJATEK", "Dane o sytuacji majątkowej, dochodach i zadłużeniu osoby fizycznej"),
 ("POMOC_SPOL", "Informacje o korzystaniu ze świadczeń pomocy społecznej"),
 ("POGLADY", "Dane dotyczące poglądów politycznych, przekonań religijnych lub światopoglądowych"),
 ("PRZYNALEZNOSC", "Informacje o przynależności związkowej lub partyjnej"),
 ("POCHODZENIE", "Dane o pochodzeniu rasowym lub etnicznym"),
 ("ORIENTACJA", "Dane o orientacji seksualnej"),
 ("KARALNOSC", "Informacje o karalności, wyrokach skazujących i naruszeniach prawa"),
 ("KW", "Numer księgi wieczystej (jeśli pozwala na identyfikację osoby fizycznej)"),
 ("DZIALKA", "Numer działki ewidencyjnej (gdy umożliwia ustalenie tożsamości prywatnego właściciela)"),
 ("POJAZD", "Numer rejestracyjny pojazdu prywatnego"),
 ("T_PRZEDSIEB", "Tajemnica przedsiębiorstwa (dane techniczne, technologiczne, organizacyjne lub handlowe firmy)"),
 ("T_SKARBOWA", "Tajemnica skarbowa (indywidualne dane podatników)"),
 ("T_BANKOWA", "Tajemnica bankowa i ubezpieczeniowa"),
 ("T_ZAWODOWA", "Dane objęte tajemnicą ustawowo chronioną (np. tajemnica zawodowa, lekarska, adwokacka)"),
 ("NIEJAWNE", "Informacje niejawne (klauzule poufności)"),
]


def detect_pii_llm(text: str) -> list:
    cats = "\n".join(f"{c} – {d}" for c, d in PII_CATEGORIES)
    sys = ("Jesteś inspektorem ochrony danych Urzędu Miasta Bydgoszczy. W tekście (projekt odpowiedzi urzędu lub dokument) znajdź WSZYSTKIE "
           "fragmenty, które zgodnie z listą urzędu podlegają anonimizacji przed udostępnieniem.\n"
           f"LISTA KATEGORII (kod – opis):\n{cats}\n\n"
           "Zasady: NIE oznaczaj osób pełniących funkcje publiczne w związku z ich funkcją (Prezydent, dyrektorzy wydziałów, urzędnicy podpisujący pismo, radni), "
           "nazw instytucji ani adresów urzędu. Imię i nazwisko przedsiębiorcy jednoosobowego w nazwie firmy oznacz jako OSOBA z uwagą w reason. "
           "Dla danych opisowych (zdrowie, rodzina, majątek, karalność) zaznacz najkrótszy fragment, który ujawnia tę informację. "
           "Zwróć JSON {\"items\": [{\"text\": \"fragment skopiowany DOSŁOWNIE z tekstu\", \"category\": \"KOD\", \"reason\": \"krótko\"}]}.")
    out = chat_json(sys, text[:12000])
    return out.get("items", [])
