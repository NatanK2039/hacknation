"""Dane demonstracyjne – w całości fikcyjne. 10 aktywnych wniosków + archiwum odpowiedzi."""
import json
from datetime import date, timedelta

from . import db
from .deadlines import compute_deadline

T = date.today()

ARCHIVE = [
 ("Wydatki na promocję miasta w mediach społecznościowych", "BP", 300,
  "Wnoszę o udostępnienie informacji, jakie kwoty Urząd Miasta Bydgoszczy wydał w 2024 r. na promocję w mediach społecznościowych (Facebook, Instagram, TikTok), z podziałem na platformy.",
  "W odpowiedzi na wniosek z dnia ... o udostępnienie informacji publicznej uprzejmie informuję, że w 2024 r. wydatki na promocję w mediach społecznościowych wyniosły łącznie 182 400 zł brutto, w tym: Facebook – 96 100 zł, Instagram – 51 300 zł, TikTok – 35 000 zł."),
 ("Liczba wyciętych drzew w 2024 roku", "WOŚ", 280,
  "Proszę o informację, ile drzew wycięto na terenie miasta w 2024 r. na podstawie decyzji Prezydenta oraz ile nasadzeń zastępczych wykonano.",
  "Uprzejmie informuję, że w 2024 r. wydano 412 decyzji zezwalających na usunięcie łącznie 1 960 drzew. Nałożono obowiązek nasadzeń zastępczych w liczbie 2 870 drzew, z czego do dnia udzielenia odpowiedzi wykonano 2 115."),
 ("Wynagrodzenia członków zarządów spółek miejskich", "ZW", 250,
  "Wnoszę o udostępnienie wysokości wynagrodzeń prezesów i członków zarządów spółek z udziałem Miasta Bydgoszczy za 2024 r.",
  "Informuję, że zgodnie z uchwałami zgromadzeń wspólników wynagrodzenie stałe członków zarządów spółek miejskich ustalane jest w przedziałach określonych ustawą o kształtowaniu wynagrodzeń... Szczegółowe zestawienie w załączniku."),
 ("Koszt remontu ulicy Gdańskiej", "ZDMiKP", 220,
  "Proszę o informację o całkowitym koszcie przebudowy ul. Gdańskiej, wykonawcy oraz terminie zakończenia prac.",
  "Uprzejmie informuję, że całkowity koszt przebudowy wynosi 48,2 mln zł brutto, wykonawcą jest konsorcjum wyłonione w przetargu nieograniczonym, a planowany termin zakończenia prac to IV kwartał 2025 r."),
 ("Umowy z kancelariami prawnymi", "ZP", 200,
  "Wnoszę o przesłanie wykazu umów zawartych przez Urząd Miasta z zewnętrznymi kancelariami prawnymi w latach 2023-2024 wraz z kwotami.",
  "W załączeniu przekazuję wykaz 7 umów zawartych z kancelariami zewnętrznymi w latach 2023–2024. Łączna wartość umów wyniosła 612 000 zł brutto."),
 ("Liczba miejsc w żłobkach i przedszkolach", "WE", 190,
  "Ile miejsc w przedszkolach publicznych było dostępnych w roku szkolnym 2024/2025 i ilu dzieciom odmówiono przyjęcia?",
  "Informuję, że w roku szkolnym 2024/2025 w przedszkolach publicznych prowadzonych przez Miasto dostępnych było 9 840 miejsc. W rekrutacji nie zostało przyjętych do wybranej placówki 312 dzieci, którym wskazano miejsca w innych placówkach."),
 ("Dotacje dla klubów sportowych", "WE", 170,
  "Proszę o wykaz dotacji przyznanych klubom sportowym w 2024 r. z budżetu miasta.",
  "Uprzejmie informuję, że wykaz dotacji przyznanych w 2024 r. klubom sportowym opublikowany jest w BIP w zakładce Sport – Wyniki konkursów. Łączna kwota dotacji wyniosła 14,6 mln zł."),
 ("Monitoring miejski – liczba kamer", "WZK", 150,
  "Ile kamer monitoringu miejskiego działa w Bydgoszczy, kto ma dostęp do nagrań i jak długo są przechowywane?",
  "Informuję, że system monitoringu wizyjnego obejmuje 318 kamer. Nagrania przechowywane są przez 30 dni. Dostęp mają uprawnieni operatorzy Centrum Monitoringu oraz uprawnione organy na podstawie przepisów prawa."),
 ("Koszty organizacji festiwalu", "BKB", 120,
  "Wnoszę o informację o kosztach organizacji festiwalu letniego w 2024 r. oraz wynagrodzeniach artystów.",
  "Informuję, że koszt organizacji wydarzenia wyniósł 2,1 mln zł brutto. W zakresie wynagrodzeń poszczególnych artystów informacja stanowi tajemnicę przedsiębiorcy wykonawców (art. 5 ust. 2 UDIP) – udostępniamy kwotę łączną 860 000 zł."),
 ("Interpelacje radnych w sprawie parkingów", "BRM", 100,
  "Proszę o kopie interpelacji radnych dotyczących strefy płatnego parkowania złożonych w 2024 r. wraz z odpowiedziami.",
  "Informuję, że interpelacje radnych wraz z odpowiedziami publikowane są w BIP w zakładce Rada Miasta – Interpelacje. W 2024 r. złożono 9 interpelacji dotyczących SPP; linki w załączniku."),
 ("Zadłużenie miasta", "WZB", 90,
  "Jaka jest wysokość zadłużenia Miasta Bydgoszczy na koniec 2024 r. i jaki jest planowany poziom na koniec 2025 r.?",
  "Uprzejmie informuję, że zadłużenie Miasta na 31.12.2024 r. wyniosło 1,98 mld zł. Zgodnie z WPF planowany poziom zadłużenia na koniec 2025 r. wynosi 2,07 mld zł."),
 ("Budżet obywatelski – zrealizowane projekty", "BAS", 70,
  "Proszę o listę projektów budżetu obywatelskiego zrealizowanych w 2024 r. oraz ich koszt.",
  "W załączeniu przekazuję zestawienie 46 projektów zrealizowanych w ramach BO 2024 o łącznej wartości 13,9 mln zł."),
 ("Schronisko dla zwierząt – liczba adopcji", "WGK", 55,
  "Ile zwierząt trafiło do schroniska w 2024 r., ile adoptowano i jaki był koszt utrzymania schroniska?",
  "Informuję, że w 2024 r. do schroniska trafiło 1 214 zwierząt, adoptowano 1 002. Koszt utrzymania schroniska wyniósł 4,3 mln zł."),
 ("Wydatki na promocję w mediach – 2025", "BP", 40,
  "Wnoszę o informację, ile wydano na kampanie promocyjne miasta w Internecie i social mediach w I półroczu 2025 r.",
  "Uprzejmie informuję, że w I półroczu 2025 r. wydatki na kampanie promocyjne w Internecie wyniosły 121 700 zł brutto, w tym media społecznościowe 88 200 zł."),
 ("Licencje oprogramowania w urzędzie", "WI", 30,
  "Proszę o informację, ile Urząd wydał na licencje oprogramowania Microsoft w latach 2023-2024.",
  "Informuję, że wydatki na licencje Microsoft wyniosły: 2023 r. – 1 240 000 zł, 2024 r. – 1 310 000 zł brutto."),
 ("Wycinka drzew przy ul. Jagiellońskiej", "WOŚ", 20,
  "Na jakiej podstawie wycięto drzewa przy ul. Jagiellońskiej i czy przewidziano nasadzenia zastępcze?",
  "Informuję, że usunięcie 14 drzew nastąpiło na podstawie decyzji związanej z realizacją inwestycji drogowej. Przewidziano nasadzenia zastępcze 28 drzew w pasie drogowym."),
]

ACTIVE = [  # (dni temu wpływ, temat, typ, wnioskodawca, treść, status, kwerenda)
 (15, "Koszty utrzymania zieleni miejskiej 2025", "mieszkaniec", "Jan Kowalczyk",
  "Szanowni Państwo, na podstawie ustawy o dostępie do informacji publicznej proszę o informację, jaką kwotę wydano w 2025 r. na utrzymanie zieleni miejskiej oraz kto jest wykonawcą usług. Odpowiedź proszę przesłać na adres e-mail.", "w_wydziale", 0),
 (14, "Wydatki na promocję w mediach społecznościowych 2025", "dziennikarz", "Redakcja Gazety Pomorskiej",
  "Wnoszę o udostępnienie informacji o wydatkach Urzędu Miasta na promocję w mediach społecznościowych w 2025 r. z podziałem na platformy oraz o liczbie zawartych umów z influencerami.", "w_wydziale", 0),
 (13, "Liczba wniosków o wycinkę drzew", "organizacja", "Stowarzyszenie Zielona Bydgoszcz",
  "Proszę o informację ile wniosków o usunięcie drzew złożono w 2025 r., ile decyzji wydano oraz ile nasadzeń zastępczych zrealizowano.", "przygotowana", 0),
 (20, "Umowy na obsługę prawną 2025", "dziennikarz", "Marek Zieliński",
  "Wnoszę o wykaz wszystkich umów z kancelariami prawnymi zawartych w 2025 r. wraz z kwotami i zakresem usług.", "kwerenda", 14),
 (10, "Monitoring – nowe kamery na osiedlach", "mieszkaniec", "Anna Nowicka",
  "Proszę o informację, ile nowych kamer monitoringu zamontowano w 2025 r. i na których osiedlach.", "nowy", 0),
 (8, "Dotacje dla organizacji pozarządowych", "organizacja", "Fundacja Otwarte Miasto",
  "Wnoszę o wykaz dotacji przyznanych organizacjom pozarządowym w 2025 r. z podziałem na obszary.", "w_wydziale", 0),
 (6, "Koszt remontu ul. Focha", "mieszkaniec", "Piotr Wiśniewski",
  "Proszę o całkowity koszt remontu ul. Focha, nazwę wykonawcy i harmonogram prac.", "nowy", 0),
 (4, "Zadłużenie spółek miejskich", "radny", "Radny Tomasz Lewandowski",
  "Wnoszę o informację o zadłużeniu spółek z udziałem miasta na koniec III kwartału 2025 r.", "nowy", 0),
 (2, "Wynagrodzenia w Biurze Komunikacji Społecznej", "dziennikarz", "Katarzyna Wójcik",
  "Proszę o informację o łącznym funduszu wynagrodzeń w Biurze Komunikacji Społecznej w 2025 r. oraz liczbie etatów.", "nowy", 0),
 (40, "Schronisko – adopcje 2025", "mieszkaniec", "Ewa Kamińska",
  "Ile zwierząt adoptowano ze schroniska w I półroczu 2025 r.?", "wyslana", 0),
]


def _y(s):
    return s.replace("2025", str(T.year)).replace("2024", str(T.year - 1)).replace("2023", str(T.year - 2))


KNOWLEDGE = [("zarzadzenie_535_2024.txt", "Zarządzenie nr 535/2024 Prezydenta Miasta Bydgoszczy – zasady i tryb udostępniania informacji publicznej", "zarządzenie"),
             ("dane_podlegajace_anonimizacji.txt", "Dane podlegające anonimizacji – lista UMB", "instrukcja"),
             ("instrukcja_udostepniania_bip.txt", "Instrukcja udostępniania informacji publicznej (BIP UMB)", "instrukcja"),
             ("ustawy_kluczowe_przepisy.txt", "UDIP i KPA – kluczowe przepisy", "ustawa")]


def run():
    import os
    kdir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "knowledge")
    with db.conn() as c:
        if c.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0] == 0:
            for fn, title, kind in KNOWLEDGE:
                fp = os.path.join(kdir, fn)
                if os.path.exists(fp):
                    c.execute("INSERT INTO knowledge(title,kind,text) VALUES(?,?,?)", (title, kind, open(fp, encoding="utf-8").read()))
        if c.execute("SELECT COUNT(*) FROM archive").fetchone()[0] == 0:
            for subj, dep, ago, rq, ans in ARCHIVE:
                d = (T - timedelta(days=ago)).isoformat()
                c.execute("INSERT INTO archive(received_date,number,subject,department,request_text,answer_text) VALUES(?,?,?,?,?,?)",
                          (d, f"DEMO.{ago}.{d[:4]}", _y(subj), dep, _y(rq), _y(ans)))
        if c.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 0:
            for i, (ago, subj, typ, who, txt, st, ext) in enumerate(sorted(ACTIVE, key=lambda a: -a[0]), start=1):
                subj, txt = _y(subj), _y(txt)
                rd = T - timedelta(days=ago)
                dl = compute_deadline(rd, ext)
                c.execute("""INSERT INTO requests(year,lp,number,received_date,deadline,extended_days,status,initials,requester,requester_type,
                             subject,summary,content,questions,answered_date,history) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (rd.year, i, f"BKS.IV.1431.{100+i}.{rd.year}", rd.isoformat(), dl.isoformat(), ext, st, "AK", who, typ,
                           subj, txt[:160], txt, json.dumps([txt]), (T - timedelta(days=30)).isoformat() if st == "wyslana" else None,
                           json.dumps([{"at": rd.isoformat(), "status": "nowy", "note": "wniosek zarejestrowany (demo)"}])))
