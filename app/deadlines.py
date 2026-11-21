"""Terminy UDIP: art. 13 ust. 1 (14 dni), ust. 2 (przedłużenie maks. do 2 miesięcy).
Koniec terminu w sobotę / niedzielę / święto -> następny dzień roboczy (art. 57 KPA stosowany pomocniczo)."""
from datetime import date, timedelta


def _easter(y: int) -> date:
    a = y % 19; b = y // 100; c = y % 100; d = b // 4; e = b % 4
    f = (b + 8) // 25; g = (b - f + 1) // 3; h = (19 * a + b - d - g + 15) % 30
    i = c // 4; k = c % 4; l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31; day = ((h + l - 7 * m + 114) % 31) + 1
    return date(y, month, day)


def holidays(y: int) -> set:
    e = _easter(y)
    fixed = [(1, 1), (1, 6), (5, 1), (5, 3), (8, 15), (11, 1), (11, 11), (12, 25), (12, 26)]
    s = {date(y, m, d) for m, d in fixed}
    if y >= 2025:
        s.add(date(y, 12, 24))  # Wigilia – dzień wolny od 2025 r.
    s |= {e, e + timedelta(days=1), e + timedelta(days=49), e + timedelta(days=60)}
    return s


def is_working_day(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays(d.year)


def next_working_day(d: date) -> date:
    while not is_working_day(d):
        d += timedelta(days=1)
    return d


def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    import calendar
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day)


def compute_deadline(received: date, extended_days: int = 0) -> date:
    base = received + timedelta(days=14)
    if extended_days:
        cap = add_months(received, 2)
        base = min(base + timedelta(days=int(extended_days)), cap)
    return next_working_day(base)
