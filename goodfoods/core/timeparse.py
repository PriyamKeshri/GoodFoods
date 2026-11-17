"""Deterministic natural-language date & time resolution.

8B models are unreliable at calendar arithmetic ("next Friday" -> wrong date), so tools accept
what the guest actually said ("tomorrow", "this sat", "kal", "20 Nov", "8pm", "dinner") and the
server resolves it here, then echoes the absolute date back for the agent to confirm.
Supports English plus common Hindi/Hinglish and Kannada words.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from goodfoods.core.errors import DomainError

WEEKDAYS = {
    "monday": 0, "mon": 0, "somvar": 0, "somvaar": 0,
    "tuesday": 1, "tue": 1, "tues": 1, "mangalvar": 1, "mangalvaar": 1,
    "wednesday": 2, "wed": 2, "budhvar": 2, "budhvaar": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "guruvar": 3, "guruvaar": 3,
    "friday": 4, "fri": 4, "shukravar": 4, "shukravaar": 4,
    "saturday": 5, "sat": 5, "shanivar": 5, "shanivaar": 5,
    "sunday": 6, "sun": 6, "ravivar": 6, "ravivaar": 6, "itvaar": 6,
}
MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9,
    "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
RELATIVE = {
    "today": 0, "tonight": 0, "this evening": 0, "this afternoon": 0, "aaj": 0, "aaj raat": 0, "ivattu": 0,
    "indu": 0, "now": 0, "tomorrow": 1, "tmrw": 1, "tmr": 1, "tomorow": 1, "tommorow": 1, "kal": 1,
    "naale": 1, "nale": 1, "day after tomorrow": 2, "day after": 2, "parso": 2, "parson": 2, "nadidu": 2,
}
DEVANAGARI = {"आज": "aaj", "कल": "kal", "परसों": "parso", "शनिवार": "shanivar", "रविवार": "ravivar",
              "सोमवार": "somvar", "शुक्रवार": "shukravar"}
NAMED_TIMES = {
    "breakfast": 9 * 60, "morning": 9 * 60, "brunch": 11 * 60 + 30, "noon": 12 * 60, "lunch": 13 * 60,
    "lunchtime": 13 * 60, "afternoon": 14 * 60, "evening": 19 * 60 + 30, "dinner": 20 * 60,
    "tonight": 20 * 60, "night": 20 * 60, "late night": 22 * 60, "dopahar": 13 * 60, "shaam": 19 * 60 + 30,
    "raat": 20 * 60,
}

_ORD = r"(\d{1,2})(?:st|nd|rd|th)?"


def _clean(text: str) -> str:
    t = str(text).strip().lower()
    for dev, latin in DEVANAGARI.items():
        t = t.replace(dev, latin)
    t = re.sub(r"[,]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _upcoming(today: date, weekday: int, *, allow_today: bool = True) -> date:
    delta = (weekday - today.weekday()) % 7
    if delta == 0 and not allow_today:
        delta = 7
    return today + timedelta(days=delta)


def _with_year(today: date, month: int, day: int, year: int | None = None) -> date:
    try:
        if year:
            return date(year if year > 100 else 2000 + year, month, day)
        candidate = date(today.year, month, day)
        return candidate if candidate >= today else date(today.year + 1, month, day)
    except ValueError as exc:
        raise DomainError(f"'{day}/{month}' is not a valid calendar date.",
                          hint="Ask the guest for the date again.") from exc


def parse_date(text: str | None, today: date) -> date:
    """Resolve a date expression relative to ``today``. Raises DomainError if it can't."""
    if text is None or not str(text).strip():
        raise DomainError("No date given.", hint="Ask the guest which day they want.")
    t = _clean(text)

    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError as exc:
            raise DomainError(f"'{text}' is not a valid date.", hint="Use YYYY-MM-DD.") from exc

    stripped = re.sub(r"\b(on|for|the|coming|this|upcoming|at|raat|night|evening|ko|ke liye)\b", " ", t)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    for phrase in sorted(RELATIVE, key=len, reverse=True):
        if t == phrase or stripped == phrase or re.search(rf"\b{re.escape(phrase)}\b", t):
            if phrase in ("now",) and t != "now":
                continue
            return today + timedelta(days=RELATIVE[phrase])

    m = re.search(r"\bin (\d{1,2}) days?\b", t)
    if m:
        return today + timedelta(days=int(m[1]))

    if re.search(r"\b(this )?weekend\b", t):
        return today if today.weekday() in (5, 6) else _upcoming(today, 5)

    m = re.search(r"\b(next|coming|this)?\s*(" + "|".join(sorted(WEEKDAYS, key=len, reverse=True)) + r")\b", t)
    if m:
        target = WEEKDAYS[m[2]]
        if m[1] == "next":
            return _upcoming(today, target, allow_today=False)
        return _upcoming(today, target, allow_today=True)

    month_names = "|".join(sorted(MONTHS, key=len, reverse=True))
    m = re.search(rf"\b{_ORD}\s*(?:of\s+)?({month_names})\b(?:\s+(\d{{4}}))?", t)
    if m:
        return _with_year(today, MONTHS[m[2]], int(m[1]), int(m[3]) if m[3] else None)
    m = re.search(rf"\b({month_names})\s+{_ORD}\b(?:\s+(\d{{4}}))?", t)
    if m:
        return _with_year(today, MONTHS[m[1]], int(m[2]), int(m[3]) if m[3] else None)

    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2,4}))?", t)  # Indian order: dd/mm(/yyyy)
    if m:
        return _with_year(today, int(m[2]), int(m[1]), int(m[3]) if m[3] else None)

    m = re.fullmatch(_ORD, t)
    if m:
        day = int(m[1])
        month, year = today.month, today.year
        if day < today.day:
            month, year = (1, year + 1) if month == 12 else (month + 1, year)
        return _with_year(today, month, day, year)

    raise DomainError(
        f"Couldn't understand the date '{text}'.",
        hint="Pass YYYY-MM-DD or words like 'today', 'tomorrow', 'friday', '20 Nov'.",
    )


def parse_time(text: str | None) -> int:
    """Resolve a time expression to minutes after midnight, rounded to 15 minutes."""
    if text is None or not str(text).strip():
        raise DomainError("No time given.", hint="Ask the guest what time they'd like.")
    t = _clean(text).replace("a.m.", "am").replace("p.m.", "pm").replace("baje", "").strip()

    for phrase in sorted(NAMED_TIMES, key=len, reverse=True):
        if t == phrase or t in (f"for {phrase}", f"at {phrase}"):
            return NAMED_TIMES[phrase]

    m = re.search(r"\b(half past|quarter past|quarter to)\s+(\d{1,2})\b", t)
    if m:
        hour = int(m[2])
        minute = {"half past": 30, "quarter past": 15, "quarter to": -15}[m[1]]
        return _finish(hour, minute, None, t)

    m = re.search(r"\b(\d{1,2})(?::|\.|h)?(\d{2})?\s*(am|pm|hrs|hours|h)?\b", t)
    m4 = re.fullmatch(r"(\d{3,4})\s*(am|pm|hrs)?", t)
    if m4:  # "2030", "0900" (24h literal) or "830pm"
        digits = m4[1]
        return _finish(int(digits[:-2]), int(digits[-2:]), m4[2], t, literal_24h=len(digits) == 4)
    if m:
        hour = int(m[1])
        minute = int(m[2]) if m[2] else 0
        # "09:00" / "07:30" are unambiguous 24-hour times (models write these); a bare "9" is not
        return _finish(hour, minute, m[3], t, literal_24h=len(m[1]) == 2 and m[1].startswith("0"))

    for phrase in sorted(NAMED_TIMES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(phrase)}\b", t):
            return NAMED_TIMES[phrase]

    raise DomainError(f"Couldn't understand the time '{text}'.", hint="Pass HH:MM (24h) or e.g. '8pm', '7:30 pm'.")


def _finish(hour: int, minute: int, meridiem: str | None, context: str, *, literal_24h: bool = False) -> int:
    if hour > 23 or minute > 59 or minute < -59:
        raise DomainError("That isn't a valid time.", hint="Ask the guest for the time again.")
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0
    elif meridiem not in ("am", "pm") and hour <= 11 and not literal_24h:
        morning = any(w in context for w in ("morning", "breakfast", "subah", "brunch"))
        if not morning and (hour <= 6 or 7 <= hour <= 10):
            hour += 12  # "8" at a restaurant almost always means 8 PM
    total = hour * 60 + minute
    return int(round(total / 15) * 15) % (24 * 60)


def fmt_time(minutes: int) -> str:
    h, m = divmod(int(minutes) % (24 * 60), 60)
    suffix = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {suffix}"


def fmt_date(d: date) -> str:
    return d.strftime("%a, %d %b %Y").replace(" 0", " ")


def hhmm(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{h:02d}:{m:02d}"
