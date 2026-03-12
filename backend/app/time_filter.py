from __future__ import annotations

from datetime import datetime, time, timedelta
import re
from zoneinfo import ZoneInfo

from dateparser import parse as parse_date
from dateparser.search import search_dates

from .schemas import TimeRange


MONTH_OR_DAY_PATTERN = re.compile(
    r"\b(today|yesterday|tomorrow|last|past|morning|afternoon|evening|night|tonight|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec|"
    r"\d{1,2}:\d{2}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4})\b",
    re.IGNORECASE,
)

RELATIVE_PATTERN = re.compile(
    r"\b(?:last|past)\s+(?P<number>\d+)\s+(?P<unit>seconds?|minutes?|hours?|days?|weeks?)\b",
    re.IGNORECASE,
)

TIME_TOKEN = r"(?:\d{1,2}:\d{2}\s*(?:am|pm)?|\d{1,2}\s*(?:am|pm)|noon|midnight)"
DATE_ANCHOR_TOKEN = (
    r"(?:today|yesterday|tonight|last\s+night|this\s+morning|this\s+afternoon|this\s+evening|"
    r"[A-Za-z]+\s+\d{1,2}(?:,\s*\d{4})?|\d{4}-\d{2}-\d{2})"
)

BETWEEN_PATTERN = re.compile(
    rf"\bbetween\s+(?P<start>{TIME_TOKEN})\s+and\s+(?P<end>{TIME_TOKEN})(?:\s+(?:on\s+)?(?P<anchor>{DATE_ANCHOR_TOKEN}))?\b",
    re.IGNORECASE,
)

AFTER_PATTERN = re.compile(
    rf"\bafter\s+(?P<time>{TIME_TOKEN})(?:\s+(?:on\s+)?(?P<anchor>{DATE_ANCHOR_TOKEN}))?\b",
    re.IGNORECASE,
)

BEFORE_PATTERN = re.compile(
    rf"\bbefore\s+(?P<time>{TIME_TOKEN})(?:\s+(?:on\s+)?(?P<anchor>{DATE_ANCHOR_TOKEN}))?\b",
    re.IGNORECASE,
)

DAYPARTS = {
    "morning": (6, 12),
    "afternoon": (12, 17),
    "evening": (17, 22),
    "night": (21, 23, 59, 59),
    "tonight": (18, 23, 59, 59),
}


def extract_time_range(
    text: str,
    *,
    timezone_name: str,
    now: datetime | None = None,
) -> TimeRange:
    text = (text or "").strip()
    tz = ZoneInfo(timezone_name)
    now = now.astimezone(tz) if now else datetime.now(tz)

    if not text:
        return TimeRange(query_without_time="")

    lower = text.lower()

    relative = _parse_relative(text, now)
    if relative:
        start, end, matched_text, source = relative
        return TimeRange(
            start=start,
            end=end,
            matched_text=matched_text,
            label=f"{start.isoformat()} to {end.isoformat()}",
            query_without_time=_remove_fragment(text, matched_text),
            source=source,
        )

    between = _parse_between(text, timezone_name, now)
    if between:
        start, end, matched_text, source = between
        return TimeRange(
            start=start,
            end=end,
            matched_text=matched_text,
            label=f"{start.isoformat()} to {end.isoformat()}",
            query_without_time=_remove_fragment(text, matched_text),
            source=source,
        )

    before_after = _parse_before_after(text, timezone_name, now)
    if before_after:
        start, end, matched_text, source = before_after
        return TimeRange(
            start=start,
            end=end,
            matched_text=matched_text,
            label=f"{start.isoformat()} to {end.isoformat()}",
            query_without_time=_remove_fragment(text, matched_text),
            source=source,
        )

    named = _parse_named_ranges(lower, now)
    if named:
        start, end, matched_text, source = named
        return TimeRange(
            start=start,
            end=end,
            matched_text=matched_text,
            label=f"{start.isoformat()} to {end.isoformat()}",
            query_without_time=_remove_fragment(text, matched_text),
            source=source,
        )

    if MONTH_OR_DAY_PATTERN.search(text):
        parsed = _parse_single_date(text, timezone_name, now)
        if parsed:
            start, end, matched_text, source = parsed
            return TimeRange(
                start=start,
                end=end,
                matched_text=matched_text,
                label=f"{start.isoformat()} to {end.isoformat()}",
                query_without_time=_remove_fragment(text, matched_text),
                source=source,
            )

    return TimeRange(query_without_time=text)


def _parse_relative(text: str, now: datetime):
    match = RELATIVE_PATTERN.search(text)
    if not match:
        return None
    number = int(match.group("number"))
    unit = match.group("unit").lower()
    delta = None
    if unit.startswith("second"):
        delta = timedelta(seconds=number)
    elif unit.startswith("minute"):
        delta = timedelta(minutes=number)
    elif unit.startswith("hour"):
        delta = timedelta(hours=number)
    elif unit.startswith("day"):
        delta = timedelta(days=number)
    elif unit.startswith("week"):
        delta = timedelta(weeks=number)
    if delta is None:
        return None
    return now - delta, now, match.group(0), "relative"


def _parse_between(text: str, timezone_name: str, now: datetime):
    match = BETWEEN_PATTERN.search(text)
    if not match:
        return None
    anchor = match.group("anchor")
    base_day = _resolve_anchor_day(anchor, now, timezone_name)
    if base_day is None:
        base_day = now
    start_dt = _parse_time_on_day(match.group("start"), base_day, timezone_name)
    end_dt = _parse_time_on_day(match.group("end"), base_day, timezone_name)
    if not start_dt or not end_dt:
        return None
    if end_dt <= start_dt:
        end_dt = end_dt + timedelta(days=1)
    return start_dt, end_dt, match.group(0), "between"


def _parse_before_after(text: str, timezone_name: str, now: datetime):
    for pattern, mode in ((AFTER_PATTERN, "after"), (BEFORE_PATTERN, "before")):
        match = pattern.search(text)
        if not match:
            continue
        anchor = match.group("anchor")
        base_day = _resolve_anchor_day(anchor, now, timezone_name)
        if base_day is None:
            base_day = now
        point = _parse_time_on_day(match.group("time"), base_day, timezone_name)
        if not point:
            continue
        day_start = datetime.combine(base_day.date(), time(0, 0), tzinfo=base_day.tzinfo)
        day_end = datetime.combine(base_day.date(), time(23, 59, 59), tzinfo=base_day.tzinfo)
        if mode == "after":
            return point, day_end, match.group(0), mode
        return day_start, point, match.group(0), mode
    return None


def _parse_named_ranges(lower: str, now: datetime):
    day_start = datetime.combine(now.date(), time(0, 0), tzinfo=now.tzinfo)
    day_end = datetime.combine(now.date(), time(23, 59, 59), tzinfo=now.tzinfo)

    if "today" in lower:
        return day_start, min(now, day_end), "today", "named"
    if "yesterday" in lower:
        y = now - timedelta(days=1)
        start = datetime.combine(y.date(), time(0, 0), tzinfo=now.tzinfo)
        end = datetime.combine(y.date(), time(23, 59, 59), tzinfo=now.tzinfo)
        for daypart, bounds in DAYPARTS.items():
            if daypart in lower:
                start, end = _range_for_daypart(y, daypart)
                return start, end, f"yesterday {daypart}", "named-daypart"
        return start, end, "yesterday", "named"

    if "last night" in lower:
        target = now - timedelta(days=1)
        start = datetime.combine(target.date(), time(21, 0), tzinfo=now.tzinfo)
        end = datetime.combine(target.date(), time(23, 59, 59), tzinfo=now.tzinfo)
        return start, end, "last night", "named-daypart"

    for daypart in DAYPARTS:
        phrase = f"this {daypart}"
        if phrase in lower or lower == daypart:
            start, end = _range_for_daypart(now, daypart)
            return start, min(end, now if now.date() == start.date() else end), phrase, "named-daypart"

    return None


def _parse_single_date(text: str, timezone_name: str, now: datetime):
    settings = {
        "TIMEZONE": timezone_name,
        "TO_TIMEZONE": timezone_name,
        "RETURN_AS_TIMEZONE_AWARE": True,
        "PREFER_DATES_FROM": "past",
        "RELATIVE_BASE": now,
    }
    results = search_dates(text, settings=settings)
    if not results:
        return None

    for matched_text, parsed_dt in results:
        if not matched_text or len(matched_text.strip()) < 2:
            continue
        if not MONTH_OR_DAY_PATTERN.search(matched_text):
            continue
        matched_text = matched_text.strip()
        if _looks_like_time_specific(matched_text):
            start = parsed_dt
            end = parsed_dt + timedelta(hours=1)
        else:
            start = datetime.combine(parsed_dt.date(), time(0, 0), tzinfo=parsed_dt.tzinfo)
            end = datetime.combine(parsed_dt.date(), time(23, 59, 59), tzinfo=parsed_dt.tzinfo)
        return start, end, matched_text, "dateparser"
    return None


def _resolve_anchor_day(anchor: str | None, now: datetime, timezone_name: str) -> datetime | None:
    if not anchor:
        return now
    anchor = anchor.strip().lower()
    if anchor == "today":
        return now
    if anchor == "yesterday":
        return now - timedelta(days=1)
    if anchor == "tonight":
        return now
    if anchor == "last night":
        return now - timedelta(days=1)
    parsed = parse_date(
        anchor,
        settings={
            "TIMEZONE": timezone_name,
            "TO_TIMEZONE": timezone_name,
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "past",
            "RELATIVE_BASE": now,
        },
    )
    return parsed


def _parse_time_on_day(value: str, base_day: datetime, timezone_name: str) -> datetime | None:
    candidate = parse_date(
        f"{base_day.date().isoformat()} {value.strip()}",
        settings={
            "TIMEZONE": timezone_name,
            "TO_TIMEZONE": timezone_name,
            "RETURN_AS_TIMEZONE_AWARE": True,
            "PREFER_DATES_FROM": "past",
            "RELATIVE_BASE": base_day,
        },
    )
    return candidate


def _range_for_daypart(base_day: datetime, daypart: str):
    bounds = DAYPARTS[daypart]
    if len(bounds) == 2:
        start_hour, end_hour = bounds
        start = datetime.combine(base_day.date(), time(start_hour, 0), tzinfo=base_day.tzinfo)
        end = datetime.combine(base_day.date(), time(end_hour, 0), tzinfo=base_day.tzinfo)
        return start, end
    start_hour, end_hour, end_minute, end_second = bounds
    start = datetime.combine(base_day.date(), time(start_hour, 0), tzinfo=base_day.tzinfo)
    end = datetime.combine(
        base_day.date(),
        time(end_hour, end_minute, end_second),
        tzinfo=base_day.tzinfo,
    )
    return start, end


def _remove_fragment(text: str, fragment: str) -> str:
    if not fragment:
        return text.strip()
    pattern = re.compile(re.escape(fragment), re.IGNORECASE)
    cleaned = pattern.sub(" ", text, count=1)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.;:-")
    return cleaned


def _looks_like_time_specific(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:\d{1,2}:\d{2}\s*(?:am|pm)?|\d{1,2}\s*(?:am|pm)|noon|midnight)\b",
            text,
            re.IGNORECASE,
        )
    )
