"""Pisma procesowe UDIP według wzorów Biura Komunikacji Społecznej UMB:
- zawiadomienie o przedłużeniu terminu (kwerenda) – art. 13 ust. 2 UDIP,
- wezwanie do doprecyzowania zakresu wniosku (+ ewentualnie nowy termin),
- projekt decyzji o odmowie udostępnienia – art. 16 ust. 1 UDIP w zw. z art. 5 / art. 3 ust. 1 pkt 1 UDIP.
Szkielet pisma jest stały (wzór UMB), AI uzupełnia wyłącznie opis zakresu wniosku i redaguje uzasadnienie z notatki urzędnika."""
from datetime import date

from . import ai

MONTHS = ["stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca", "lipca", "sierpnia", "września", "października",
          "listopada", "grudnia"]
KINDS = {"kwerenda": "Zawiadomienie o przedłużeniu terminu (kwerenda)",
         "doprecyzowanie": "Wezwanie do doprecyzowania wniosku",
         "odmowa": "Projekt decyzji o odmowie udostępnienia"}
GROUNDS = {
    "prywatnosc": "art. 5 ust. 2 ustawy – prywatność osoby fizycznej",
    "tajemnica_przedsiebiorcy": "art. 5 ust. 2 ustawy – tajemnica przedsiębiorcy",
    "niejawne": "art. 5 ust. 1 ustawy – informacje niejawne / inne tajemnice ustawowo chronione",
    "przetworzona": "art. 3 ust. 1 pkt 1 ustawy – informacja przetworzona bez wykazania szczególnej istotności dla interesu publicznego",
}
UDIP = "ustawy z dnia 6 września 2001 r. o dostępie do informacji publicznej (Dz. U. z 2022 r. poz. 902, z późn. zm.)"
FOOTER = "Łączę wyrazy szacunku,\n\nBiuro Komunikacji Społecznej\nUrząd Miasta Bydgoszczy\ne-mail: bip@um.bydgoszcz.pl\n85-102 Bydgoszcz, ul. Jezuicka 1, tel. (52) 58 58 365"


def pl_date(d: date, with_year=True) -> str:
    return f"{d.day} {MONTHS[d.month - 1]}" + (f" {d.year} r." if with_year else "")


def _salutation(req: dict):
    import re
    t = (req.get("requester_type") or "").lower()
    who = (req.get("requester") or "").strip()
    if t in ("organizacja", "firma") or re.search(r"(redakcja|stowarzyszeni|fundacj|sp\. ?z|s\.a\.|spółdziel|urząd|związek|portal|gazeta|radio|telewizja|instytut)", who, re.I):
        return "Szanowni Państwo,", "Państwa", "wystosowanym przez Państwa"
    words = [w for w in re.split(r"\s+", who) if w and not w.lower().startswith(("radny", "radna", "pan", "pani", "dr", "mgr"))]
    first = words[0] if words else ""
    if first.lower().endswith("a") and first.lower() not in ("kuba", "barnaba", "bonawentura", "kosma"):
        return "Szanowna Pani,", "Pani", "wystosowanym przez Panią"
    if first:
        return "Szanowny Panie,", "Pana", "wystosowanym przez Pana"
    return "Szanowni Państwo,", "Państwa", "wystosowanym przez Państwa"


def _ai_parts(req: dict, kind: str, notes: str) -> dict:
    if not ai.enabled():
        return {}
    sys = ("Redagujesz fragmenty pisma Biura Komunikacji Społecznej Urzędu Miasta Bydgoszczy w sprawie wniosku o dostęp do informacji publicznej. "
           "Zwróć JSON: {\"zakres_m\": \"opis przedmiotu wniosku zaczynający się od 'dotyczący' (mianownik, np. 'dotyczący kosztów remontu boisk szkolnych')\", "
           "\"zakres_n\": \"to samo zaczynające się od 'dotyczącym'\", \"zakres_d\": \"to samo zaczynające się od 'dotyczącego'\", "
           "\"tresc\": \"redakcja notatki urzędnika – " + {
               "kwerenda": "fraza dokańczająca zdanie '…, [tresc] powiadamiam, że ostateczna odpowiedź nastąpi w terminie późniejszym', zaczynająca się od 'ze względu na' lub 'z uwagi na'",
               "doprecyzowanie": "fraza dokańczająca zdanie '…komórka merytoryczna wnosi o doprecyzowanie wniosku [tresc]', zaczynająca się od 'poprzez wskazanie' lub 'poprzez określenie', bez podmiotu",
               "odmowa": "1–4 pełne zdania uzasadnienia w stylu urzędowym",
           }[kind] + "; NIE dodawaj faktów, przepisów ani dat, których nie ma w notatce; jeśli notatka jest pusta zwróć pusty tekst\"}")
    user = f"RODZAJ PISMA: {KINDS[kind]}\nTEMAT: {req.get('subject')}\nTREŚĆ WNIOSKU:\n{(req.get('content') or '')[:3000]}\n\nNOTATKA URZĘDNIKA:\n{notes or '(brak)'}"
    try:
        return ai.chat_json(sys, user, purpose=f"pismo: {kind}")
    except Exception:
        return {}


def build(req: dict, kind: str, notes: str = "", new_date: str = None, ground: str = None, dept: str = None, days: int = 7) -> dict:
    if kind not in KINDS:
        raise ValueError("Nieznany rodzaj pisma")
    today = date.today()
    sal, gen, by = _salutation(req)
    parts = _ai_parts(req, kind, notes)
    fallback = f"w sprawie: {req.get('subject') or '[[PRZEDMIOT WNIOSKU]]'}"
    zm = (parts.get("zakres_m") or "").strip() or fallback
    zn = (parts.get("zakres_n") or "").strip() or fallback
    zd = (parts.get("zakres_d") or "").strip() or fallback
    tresc = (parts.get("tresc") or "").strip() or (notes or "").strip()
    rd = date.fromisoformat(req["received_date"]) if req.get("received_date") else today
    nd = date.fromisoformat(new_date) if new_date else None
    head = f"Bydgoszcz, dnia {pl_date(today)}\n{req.get('number') or '[[ZNAK SPRAWY]]'}\n\n{req.get('requester') or '[[WNIOSKODAWCA]]'}\n\n"

    if kind == "kwerenda":
        nd_txt = pl_date(nd, nd.year != today.year) + (" br." if nd and nd.year == today.year else "") if nd else "[[NOWY TERMIN]]"
        reason = tresc or "ze względu na konieczność przeprowadzenia całościowej kwerendy w zakresie wnioskowanych danych"
        body = (f"{sal}\n\nw odpowiedzi na {by.replace('wystosowanym', 'wystosowany')} wniosek o udostępnienie informacji publicznej z dnia {pl_date(rd)}, "
                f"{zm.rstrip('.')}, {reason.rstrip('.')[:1].lower() + reason.rstrip('.')[1:]} "
                f"powiadamiam, że ostateczna odpowiedź nastąpi w terminie późniejszym wskazanym w art. 13 ust. 2 {UDIP} – "
                f"orientacyjny termin udzielenia odpowiedzi: {nd_txt}\n\n{FOOTER}")
        title = "ZAWIADOMIENIE O PRZEDŁUŻENIU TERMINU"
    elif kind == "doprecyzowanie":
        unit = f"komórka merytoryczna, tj. {dept}," if dept else "komórka merytoryczna [[NAZWA KOMÓRKI]]"
        if parts.get("tresc"):
            ask = " " + parts["tresc"].strip().rstrip(".")
        elif tresc:
            ask = ": " + tresc.rstrip(".")
        else:
            ask = ": [[CZEGO DOTYCZY DOPRECYZOWANIE – np. wskazanie konkretnych dokumentów, okresu, zakresu danych]]"
        body = (f"WEZWANIE DO DOPRECYZOWANIA\nZAKRESU OTRZYMANEGO WNIOSKU\n\n{sal}\n\nw związku z {by} wnioskiem o udostępnienie informacji publicznej "
                f"z dnia {pl_date(rd)}, {zn.rstrip('.')}, {unit} wnosi o doprecyzowanie wniosku{ask}.\n\n"
                f"Proszę o przesłanie doprecyzowania w terminie {days} dni od otrzymania niniejszego pisma. Po upływie tego terminu wniosek zostanie "
                f"pozostawiony bez rozpoznania, o czym Wnioskodawca zostanie powiadomiony odrębnym pismem.")
        if nd:
            body += (f"\n\nJednocześnie stosownie do treści art. 13 ust. 2 {UDIP} powiadamiam, że wyznaczam nowy termin na rozpatrzenie wniosku, "
                     f"wskazując dzień {pl_date(nd, nd.year != today.year)}{' br.' if nd.year == today.year else ''}")
        body += f"\n\n{FOOTER}"
        title = None
    else:  # odmowa
        g = GROUNDS.get(ground or "", "[[PODSTAWA ODMOWY – art. 5 ust. 1/2 lub art. 3 ust. 1 pkt 1 ustawy]]")
        just = tresc or "[[UZASADNIENIE FAKTYCZNE – jakie informacje są chronione i dlaczego, wskazanie dobra chronionego, ważenie interesów]]"
        body = (f"[[PROJEKT – wymaga weryfikacji przez Zespół Prawny i podpisu osoby upoważnionej (§ 11 ust. 9 Zarządzenia nr 535/2024)]]\n\n"
                f"DECYZJA nr [[NUMER]]\n\n"
                f"Na podstawie art. 16 ust. 1 w związku z {g.split(' – ')[0].replace(' ustawy', '')} {UDIP} oraz art. 104 ustawy z dnia 14 czerwca 1960 r. – "
                f"Kodeks postępowania administracyjnego, po rozpatrzeniu wniosku z dnia {pl_date(rd)} {zd.rstrip('.')},\n\n"
                f"odmawiam udostępnienia informacji publicznej w zakresie: [[ZAKRES ODMOWY]].\n\n"
                f"UZASADNIENIE\n\nW dniu {pl_date(rd)} do Urzędu Miasta Bydgoszczy wpłynął wniosek o udostępnienie informacji publicznej {zd.rstrip('.')}. "
                f"Podstawa odmowy: {g}. {just.rstrip('.')}.\n\nW pozostałym zakresie informacja została / zostanie udostępniona odrębnym pismem.\n\n"
                f"POUCZENIE\n\nOd niniejszej decyzji przysługuje odwołanie do Samorządowego Kolegium Odwoławczego w Bydgoszczy za pośrednictwem "
                f"Prezydenta Miasta Bydgoszczy w terminie 14 dni od dnia jej doręczenia (art. 16 ust. 2 pkt 1 ustawy o dostępie do informacji publicznej "
                f"w zw. z art. 127 i 129 Kodeksu postępowania administracyjnego).\n\n"
                f"[[PODPIS OSOBY UPOWAŻNIONEJ]]\n\nOtrzymuje:\n1. Wnioskodawca\n2. a/a")
        title = None
    text = head + (title + "\n\n" if title else "") + body
    return {"kind": kind, "title": KINDS[kind], "text": text, "new_date": new_date}
