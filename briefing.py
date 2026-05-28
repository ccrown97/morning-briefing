#!/usr/bin/env python3
"""Morning Briefing – täglich 06:30 CEST via GitHub Actions.

Features: GPS-Standort, Open-Meteo-Wetter (2-stündlich), RSS-News mit
Gemini-AI-Zusammenfassung, TickTick-Aufgaben, Telegram-Versand.
Keine externen Paketabhängigkeiten – nur Python-Standardbibliothek.
"""

import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

# ── Konfiguration ─────────────────────────────────────────────────────────────

GIST_URL = (
    "https://gist.githubusercontent.com/ccrown97/"
    "b9e854688bb4c32e7069eb058f959660/raw/location.json"
)
TELEGRAM_TOKEN       = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID     = os.environ["TELEGRAM_CHAT_ID"]
GEMINI_KEY           = os.environ.get("GEMINI_API_KEY")          # optional
TICKTICK_ACCESS      = os.environ.get("TICKTICK_ACCESS_TOKEN")   # optional
OUTLOOK_ICAL_URLS    = [u.strip() for u in (os.environ.get("OUTLOOK_ICAL_URLS") or "").split(",") if u.strip()]

RSS_FEEDS = [
    ("Handelsblatt", "https://www.handelsblatt.com/contentexport/feed/schlagzeilen"),
    ("Tagesschau",   "https://www.tagesschau.de/xml/rss2/"),
    ("Spiegel",      "https://www.spiegel.de/schlagzeilen/index.rss"),
    ("FAZ",          "https://www.faz.net/rss/aktuell/"),
    ("Reuters DE",   "https://feeds.reuters.com/reuters/de/businessNews"),
]

# WMO Weather Interpretation Codes → Deutsch
WMO_DE = {
    0: "☀️ Klarer Himmel",
    1: "🌤 Überwiegend klar",  2: "⛅ Leicht bewölkt",    3: "☁️ Bedeckt",
    45: "🌫 Nebel",            48: "🌫 Nebel (gefrierend)",
    51: "🌦 Nieselregen",      53: "🌦 Nieselregen",       55: "🌧 Starker Nieselregen",
    61: "🌧 Leichter Regen",   63: "🌧 Regen",             65: "🌧 Starker Regen",
    71: "🌨 Leichter Schnee",  73: "🌨 Schnee",            75: "❄️ Starker Schnee",
    77: "🌨 Schneekörner",
    80: "🌦 Leichte Schauer",  81: "🌧 Schauer",           82: "⛈ Starke Schauer",
    85: "🌨 Schneeschauer",    86: "❄️ Starke Schneeschauer",
    95: "⛈ Gewitter",         96: "⛈ Gewitter m. Hagel",  99: "⛈ Starkes Gewitter",
}

DAYS_DE = {
    "Monday": "Montag",   "Tuesday": "Dienstag", "Wednesday": "Mittwoch",
    "Thursday": "Donnerstag", "Friday": "Freitag",
    "Saturday": "Samstag", "Sunday": "Sonntag",
}


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def fetch(url: str, timeout: int = 12, headers: dict | None = None) -> str:
    h = {"User-Agent": "MorningBriefing/2.0 (github.com/ccrown97/morning-briefing)"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def post(url: str, data: bytes, headers: dict, timeout: int = 12) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()


def parse_ics_today(ics_text: str, today: str) -> list[str]:
    """Gibt heutige Kalendereinträge als formatierte Strings zurück."""
    # Zeilenfortsetzungen auflösen (RFC 5545 Line Folding)
    lines = ics_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    unfolded: list[str] = []
    for line in lines:
        if line.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += line[1:]
        else:
            unfolded.append(line)

    events: list[dict] = []
    in_event = False
    ev: dict = {}
    for line in unfolded:
        if line == "BEGIN:VEVENT":
            in_event, ev = True, {}
        elif line == "END:VEVENT":
            in_event = False
            if ev:
                events.append(ev)
        elif in_event and ":" in line:
            key, _, val = line.partition(":")
            base = key.split(";")[0].upper()
            if base == "SUMMARY":
                ev["summary"] = val
            elif base == "DTSTART":
                ev["dtstart"] = val
            elif base == "DTEND":
                ev["dtend"] = val
            elif base == "STATUS":
                ev["status"] = val

    def _to_berlin(s: str):
        """ICS-Datetime → naive Berlin-datetime (UTC+2)."""
        if not s:
            return None
        try:
            if s.endswith("Z"):
                return datetime.strptime(s, "%Y%m%dT%H%M%SZ") + timedelta(hours=2)
            if "T" in s:
                return datetime.strptime(s[:15], "%Y%m%dT%H%M%S")
        except Exception:
            return None

    def _bars(start, end) -> str:
        """Dauer-Balken: 1 █ pro Stunde (min 1, max 8)."""
        if start is None or end is None:
            return "█"
        mins = int((end - start).total_seconds() / 60)
        return "█" * max(1, min(8, round(mins / 60)))

    all_day: list[str] = []
    timed:   list[str] = []

    for ev in events:
        if ev.get("status", "").upper() == "CANCELLED":
            continue
        dtstart = ev.get("dtstart", "")
        dtend   = ev.get("dtend",   "")
        summary = ev.get("summary", "Kein Titel")
        try:
            if len(dtstart) == 8:                       # Ganztag: VALUE=DATE:20260528
                date = f"{dtstart[:4]}-{dtstart[4:6]}-{dtstart[6:8]}"
                if date == today:
                    all_day.append(f"◻️ Ganztag  │ {summary}")
            else:
                start_dt = _to_berlin(dtstart)
                if start_dt and start_dt.strftime("%Y-%m-%d") == today:
                    end_dt  = _to_berlin(dtend)
                    start_s = start_dt.strftime("%H:%M")
                    end_s   = end_dt.strftime("%H:%M") if end_dt else "?   "
                    timed.append(f"{start_s}–{end_s} │ {summary}")
        except Exception:
            pass

    return all_day + sorted(timed)


def first_sentence(text: str, max_chars: int = 140) -> str:
    text = strip_html(text)
    idx = text.find(".")
    return (text[: idx + 1] if idx != -1 else text[:max_chars]).strip()


# ── 1. GPS-Koordinaten ────────────────────────────────────────────────────────

coords = fetch(GIST_URL).strip()
lat, lon = coords.split(",")
print(f"  GPS: {lat}, {lon}")


# ── 2. Stadtname via Nominatim ────────────────────────────────────────────────

city = "?"
try:
    nom = json.loads(fetch(
        f"https://nominatim.openstreetmap.org/reverse"
        f"?lat={lat}&lon={lon}&format=json&accept-language=de"
    ))
    addr = nom.get("address", {})
    city = (
        addr.get("city") or addr.get("town") or
        addr.get("village") or addr.get("municipality") or "?"
    )
    print(f"  Stadt: {city}")
except Exception as e:
    print(f"  ✗ Nominatim: {e}")


# ── 3. Wetter (Open-Meteo, 2-stündlich 06–22 Uhr) ────────────────────────────

weather_lines: list[str] = []
try:
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&hourly=temperature_2m,weathercode"
        f"&timezone=Europe%2FBerlin&forecast_days=1"
    )
    om = json.loads(fetch(url))
    berlin_now = datetime.now(timezone.utc) + timedelta(hours=2)
    today_str  = berlin_now.strftime("%Y-%m-%d")

    for t, temp, code in zip(
        om["hourly"]["time"],
        om["hourly"]["temperature_2m"],
        om["hourly"]["weathercode"],
    ):
        date_s, time_s = t.split("T")
        hour = int(time_s[:2])
        if date_s == today_str and 6 <= hour <= 22 and hour % 2 == 0:
            desc = WMO_DE.get(code, f"Code {code}")
            weather_lines.append(f"{time_s[:5]} – {round(temp)}°C  {desc}")

    print(f"  Wetter: {city}, {len(weather_lines)} Einträge")
except Exception as e:
    print(f"  ✗ Wetter: {e}")


# ── 4. News via RSS ───────────────────────────────────────────────────────────

articles: list[tuple[str, str, str, str]] = []   # (source, title, url, desc)

for source, feed_url in RSS_FEEDS:
    try:
        root = ET.fromstring(fetch(feed_url, timeout=15))
        count = 0
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link  = (item.findtext("link")  or "").strip()
            desc  = first_sentence(item.findtext("description") or "")
            if title and link.startswith("http"):
                articles.append((source, title, link, desc))
                count += 1
                if count >= 3:      # max. 3 Artikel pro Quelle
                    break
        print(f"  RSS {source}: {count} Artikel")
    except Exception as e:
        print(f"  ✗ RSS {source}: {e}")


# ── 5. TickTick-Aufgaben (falls Token vorhanden) ──────────────────────────────

tasks_lines: list[str] = []

if TICKTICK_ACCESS:
    try:
        projects = json.loads(fetch(
            "https://api.ticktick.com/open/v1/project",
            headers={"Authorization": f"Bearer {TICKTICK_ACCESS}"},
        ))

        berlin_today = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d")

        for project in projects:
            proj_data = json.loads(fetch(
                f"https://api.ticktick.com/open/v1/project/{project['id']}/data",
                headers={"Authorization": f"Bearer {TICKTICK_ACCESS}"},
            ))
            for task in proj_data.get("tasks", []):
                if task.get("status", 0) != 0:  # 0 = offen
                    continue
                due_raw = task.get("dueDate") or ""
                if not due_raw:
                    continue
                # TickTick speichert All-Day-Termine als UTC (22:00 UTC = 00:00 Berlin)
                # → UTC-Datum in Berliner Zeit umrechnen vor dem Vergleich
                try:
                    due_utc = datetime.strptime(due_raw[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                    due = (due_utc + timedelta(hours=2)).strftime("%Y-%m-%d")
                except ValueError:
                    due = due_raw[:10]
                if due == berlin_today:
                    priority_icon = {1: "🔴", 3: "🟡", 5: "🔵"}.get(task.get("priority", 0), "◻️")
                    tasks_lines.append(f"{priority_icon} {task['title']}")

        print(f"  TickTick: {len(tasks_lines)} Aufgaben heute")
    except Exception as e:
        print(f"  ✗ TickTick: {e}")


# ── 6. Outlook-Kalender (iCal, falls URLs vorhanden) ─────────────────────────

calendar_lines: list[str] = []

if OUTLOOK_ICAL_URLS:
    berlin_today_cal = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%d")
    for ical_url in OUTLOOK_ICAL_URLS:
        try:
            ics = fetch(ical_url, timeout=15)
            entries = parse_ics_today(ics, berlin_today_cal)
            calendar_lines.extend(entries)
            print(f"  Outlook iCal: {len(entries)} Termine heute")
        except Exception as e:
            print(f"  ✗ Outlook iCal: {e}")
    # Nach Uhrzeit sortieren (📅 HH:MM Titel oder 📅 Titel für All-Day)
    calendar_lines.sort()


# ── 7. AI-Artikelauswahl + Zusammenfassung via Gemini ────────────────────────

summary_text = ""
top_articles  = articles[:5]   # Fallback: erste 5 Artikel

if GEMINI_KEY and articles:
    try:
        news_list = "\n".join(
            f"[{i}] {s}: {t}"
            for i, (s, t, u, d) in enumerate(articles)
        )
        prompt = (
            "Du erhältst eine Liste von Nachrichtenartikeln.\n"
            "Aufgaben:\n"
            "1. Wähle die 5 wichtigsten Artikel aus – priorisiere Themen aus "
            "Geopolitik, Wirtschaft, Finanzen und Unternehmensrestrukturierung "
            "(Kontext: Leser startet September 2026 als Restrukturierungsberater "
            "bei AlixPartners).\n"
            "2. Schreibe einen zusammenhängenden Nachrichtenüberblick auf Deutsch, "
            "der ALLE 5 gewählten Themen abdeckt. Genau 4–6 Sätze als Fließtext "
            "(kein Titel, keine Aufzählung, kein Markdown).\n\n"
            "Antworte NUR mit einem JSON-Objekt, ohne Markdown-Codeblock:\n"
            '{"picks": [idx1, idx2, idx3, idx4, idx5], "summary": "Fließtext..."}\n\n'
            f"Artikelliste:\n{news_list}"
        )
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": 1000,
                "temperature": 0.4,
                "responseMimeType": "application/json",   # JSON-Modus, kein Schema nötig
            },
        }).encode()
        resp = post(
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-2.5-flash:generateContent?key={GEMINI_KEY}",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        raw = resp["candidates"][0]["content"]["parts"][0]["text"].strip()
        try:
            parsed       = json.loads(raw)
            summary_text = parsed.get("summary", "").strip()
            picks        = parsed.get("picks", [])
            if picks and all(isinstance(p, int) for p in picks):
                chosen = [articles[i] for i in picks if 0 <= i < len(articles)]
                if chosen:
                    top_articles = chosen
        except (json.JSONDecodeError, KeyError):
            # Fallback: Rohtext als Zusammenfassung, Artikel-Auswahl bleibt Fallback
            summary_text = raw
        print(f"  AI-Zusammenfassung: OK ({len(top_articles)} Artikel gewählt)")
    except Exception as e:
        print(f"  ✗ AI-Zusammenfassung: {e}")


# ── 7. Nachricht zusammenstellen ─────────────────────────────────────────────

now_b   = datetime.now(timezone.utc) + timedelta(hours=2)
day_de  = DAYS_DE.get(now_b.strftime("%A"), now_b.strftime("%A"))
date_de = now_b.strftime("%d.%m.%Y")

parts: list[str] = []

parts.append(f"🌅 Morning Briefing – {day_de}, {date_de}")
parts.append(f"📍 {city}")

parts.append(
    "⏰ Wetter heute:\n" +
    ("\n".join(weather_lines) if weather_lines else "Nicht verfügbar")
)

if tasks_lines:
    parts.append("✅ Aufgaben heute:\n" + "\n".join(tasks_lines[:8]))

if calendar_lines:
    parts.append("📅 Termine heute:\n" + "\n".join(calendar_lines[:8]))

if summary_text:
    parts.append(f"📋 News-Überblick:\n{summary_text}")

# News: Telegram erlaubt 4096 Zeichen – großzügiges Budget
LIMIT = 3500
base_msg = "\n\n".join(parts) + "\n\n📰 Top News:\n"
budget   = LIMIT - len(base_msg)

news_entries: list[str] = []
for i, (s, t, u, d) in enumerate(top_articles[:5], 1):
    title = t[:70] + "…" if len(t) > 70 else t          # Titel kürzen
    entry = f"{i}. {s}: {title}\n   🔗 {u}"
    cost  = len(entry) + (1 if news_entries else 0)      # +1 für \n-Trenner
    if cost > budget:
        break
    news_entries.append(entry)
    budget -= cost

parts.append("📰 Top News:\n" + ("\n".join(news_entries) if news_entries else "Nicht verfügbar"))
message = "\n\n".join(parts)

print("\n" + "─" * 44)
print(message)
print(f"  Länge: {len(message)} Zeichen")
print("─" * 44 + "\n")


# ── 8. Telegram senden ───────────────────────────────────────────────────────

body = json.dumps({
    "chat_id":    TELEGRAM_CHAT_ID,
    "text":       message,
    "parse_mode": "",          # kein Markdown – Emojis + URLs funktionieren plain
}).encode()
resp = post(
    f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
    data=body,
    headers={"Content-Type": "application/json"},
)
msg_id = resp.get("result", {}).get("message_id", "?")
print(f"  Telegram: message_id={msg_id} ✅")
