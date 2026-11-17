from datetime import date

import pytest

from goodfoods.core.errors import DomainError
from goodfoods.core.timeparse import fmt_date, fmt_time, parse_date, parse_time

TODAY = date(2026, 11, 15)  # Sunday


@pytest.mark.parametrize("text, expected", [
    ("today", "2026-11-15"), ("tonight", "2026-11-15"), ("tomorrow", "2026-11-16"), ("kal", "2026-11-16"),
    ("kal raat", "2026-11-16"), ("parso", "2026-11-17"), ("day after tomorrow", "2026-11-17"),
    ("friday", "2026-11-20"), ("this friday", "2026-11-20"), ("sat", "2026-11-21"), ("shanivar", "2026-11-21"),
    ("sunday", "2026-11-15"), ("next sunday", "2026-11-22"), ("this weekend", "2026-11-15"),
    ("2026-11-20", "2026-11-20"), ("20 Nov", "2026-11-20"), ("20th november", "2026-11-20"), ("nov 20", "2026-11-20"),
    ("20/11", "2026-11-20"), ("5/1", "2027-01-05"), ("25th", "2026-11-25"), ("in 3 days", "2026-11-18"),
    ("कल", "2026-11-16"), ("Saturday 21 Nov", "2026-11-21"),
])
def test_parse_date(text, expected):
    assert parse_date(text, TODAY).isoformat() == expected


@pytest.mark.parametrize("text", ["next week", "someday", "", "31/02"])
def test_parse_date_rejects_unclear_input_with_a_hint(text):
    with pytest.raises(DomainError) as err:
        parse_date(text, TODAY)
    assert err.value.hint


@pytest.mark.parametrize("text, expected", [
    ("8pm", "8:00 PM"), ("8 pm", "8:00 PM"), ("8:30pm", "8:30 PM"), ("8.30 pm", "8:30 PM"), ("20:00", "8:00 PM"),
    ("2030", "8:30 PM"), ("830pm", "8:30 PM"), ("8", "8:00 PM"), ("11", "11:00 AM"), ("1", "1:00 PM"),
    ("noon", "12:00 PM"), ("lunch", "1:00 PM"), ("dinner", "8:00 PM"), ("8 baje", "8:00 PM"),
    ("breakfast", "9:00 AM"), ("9 am", "9:00 AM"), ("half past 7", "7:30 PM"), ("quarter to 9", "8:45 PM"),
    ("19:20", "7:15 PM"), ("at 8", "8:00 PM"),
    # regression (found by evals): models send 24h "09:00" for 9 AM; a bare "9:00" stays dinner-biased
    ("09:00", "9:00 AM"), ("0900", "9:00 AM"), ("07:30", "7:30 AM"), ("9:00", "9:00 PM"),
])
def test_parse_time(text, expected):
    assert fmt_time(parse_time(text)) == expected


def test_parse_time_rejects_nonsense():
    with pytest.raises(DomainError):
        parse_time("whenever")


def test_formatting():
    assert fmt_date(date(2026, 11, 21)) == "Sat, 21 Nov 2026"
    assert fmt_time(0) == "12:00 AM"
