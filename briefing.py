#!/usr/bin/env python3
"""Morning Briefing – täglich 06:30 CEST via GitHub Actions.

Features: GPS-Standort, Open-Meteo-Wetter (2-stündlich), RSS-News mit
Gemini-AI-Zusammenfassung, TickTick-Aufgaben, WhatsApp via CallMeBot.
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
CALLMEBOT_KEY        = os.environ["CALLMEBOT_KEY"]
CALLMEBOT_PHONE      = os.environ["CALLMEBOT_PHONE"]
GEMINI_KEY           = os.environ.get("GEMINI_API_KEY")          # optional
TICKTICK_ACCESS      = os.environ.get("TICKTICK_ACCESS_TOKEN")   # optional

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
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link  = (item.findtext("link")  or "").strip()
            desc  = first_sentence(item.findtext("description") or "")
            if title and link.startswith("http"):
                articles.append((source, title, link, desc))
                break
        print(f"  RSS {source}: OK")
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
                due = (task.get("dueDate") or "")[:10]
                if due == berlin_today:
                    priority_icon = {1: "🔴", 3: "🟡", 5: "🔵"}.get(task.get("priority", 0), "◻️")
                    tasks_lines.append(f"{priority_icon} {task['title']}")

        print(f"  TickTick: {len(tasks_lines)} Aufgaben heute")
    except Exception as e:
        print(f"  ✗ TickTick: {e}")


# ── 6. AI-Zusammenfassung via Gemini (falls API-Key vorhanden) ────────────────

summary_text = ""

if GEMINI_KEY and articles:
    try:
        news_text = "\n".join(f"- {s}: {t}" for s, t, u, d in articles[:5])
        prompt = (
            "Schreibe eine prägnante Zusammenfassung der heutigen Top-News "
            "in 2–3 Sätzen auf Deutsch. Kontext: Leser ist Management-Finance-Student, "
            "startet September 2026 als Restrukturierungsberater bei AlixPartners – "
            "betone wirtschaftlich und beratungsrelevante Aspekte. "
            "Nur Fließtext, kein Titel, keine Aufzählung.\n\n"
            f"Nachrichten:\n{news_text}"
        )
        body = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 500, "temperature": 0.7},
        }).encode()
        resp = post(
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-2.5-flash:generateContent?key={GEMINI_KEY}",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        summary_text = (
            resp["candidates"][0]["content"]["parts"][0]["text"].strip()
        )
        print("  AI-Zusammenfassung: OK")
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

if summary_text:
    parts.append(f"📋 News-Überblick:\n{summary_text}")

news_list = "\n".join(
    f"{i}. {s}: {t}\n   🔗 {u}"
    for i, (s, t, u, d) in enumerate(articles[:5], 1)
)
parts.append("📰 Top News:\n" + (news_list or "Nicht verfügbar"))

message = "\n\n".join(parts)

print("\n" + "─" * 44)
print(message)
print("─" * 44 + "\n")


# ── 8. WhatsApp senden ────────────────────────────────────────────────────────

params = urllib.parse.urlencode({
    "phone":  CALLMEBOT_PHONE,
    "apikey": CALLMEBOT_KEY,
    "text":   message,
})
result = fetch(f"https://api.callmebot.com/whatsapp.php?{params}", timeout=15)
print(f"  WhatsApp: {result.strip()}")
