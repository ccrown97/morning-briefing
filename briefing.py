#!/usr/bin/env python3
"""Morning Briefing – täglich 06:30 CEST via GitHub Actions.

Holt GPS-Koordinaten, Wetter und RSS-News, sendet WhatsApp via CallMeBot.
Keine externen Abhängigkeiten – nur Python-Standardbibliothek.
"""

import json
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta

# ── Konfiguration ─────────────────────────────────────────────────────────────

GIST_URL = (
    "https://gist.githubusercontent.com/ccrown97/"
    "b9e854688bb4c32e7069eb058f959660/raw/location.json"
)
WEATHER_KEY     = os.environ["OPENWEATHERMAP_KEY"]
CALLMEBOT_KEY   = os.environ["CALLMEBOT_KEY"]
CALLMEBOT_PHONE = os.environ["CALLMEBOT_PHONE"]

RSS_FEEDS = [
    ("Tagesschau", "https://www.tagesschau.de/xml/rss2/"),
    ("Spiegel",    "https://www.spiegel.de/schlagzeilen/index.rss"),
    ("FAZ",        "https://www.faz.net/rss/aktuell/"),
    ("Zeit",       "https://newsfeed.zeit.de/wirtschaft/index"),
    ("Reuters DE", "https://feeds.reuters.com/reuters/de/businessNews"),
]

DAYS_DE = {
    "Monday": "Montag", "Tuesday": "Dienstag", "Wednesday": "Mittwoch",
    "Thursday": "Donnerstag", "Friday": "Freitag",
    "Saturday": "Samstag", "Sunday": "Sonntag",
}

TARGET_HOURS = {
    "06:00", "08:00", "10:00", "12:00",
    "14:00", "16:00", "18:00", "20:00", "22:00",
}


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def fetch(url: str, timeout: int = 12) -> str:
    req = urllib.request.Request(
        url, headers={"User-Agent": "MorningBriefing/1.0 (+github.com)"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


# ── 1. Standort & Wetter ──────────────────────────────────────────────────────

city = "?"
weather_lines: list[str] = []

try:
    coords = fetch(GIST_URL).strip()
    lat, lon = coords.split(",")
    print(f"  GPS: {lat}, {lon}")

    weather_url = (
        f"https://api.openweathermap.org/data/2.5/forecast"
        f"?lat={lat}&lon={lon}&appid={WEATHER_KEY}&units=metric&lang=de"
    )
    data = json.loads(fetch(weather_url))
    city = data["city"]["name"]

    # Datum in lokaler Zeit (CEST = UTC+2, Sommernäherung)
    berlin_now = datetime.now(timezone.utc) + timedelta(hours=2)
    today_str  = berlin_now.strftime("%Y-%m-%d")

    for entry in data["list"]:
        date_part, time_part = entry["dt_txt"].split(" ")
        if date_part == today_str and time_part[:5] in TARGET_HOURS:
            temp = round(entry["main"]["temp"])
            desc = entry["weather"][0]["description"].capitalize()
            weather_lines.append(f"{time_part[:5]} – {temp}°C, {desc}")

    print(f"  Wetter: {city}, {len(weather_lines)} Einträge")

except Exception as e:
    print(f"  ✗ Wetter-Fehler: {e}")


# ── 2. News via RSS ───────────────────────────────────────────────────────────

articles: list[tuple[str, str, str]] = []  # (Quelle, Titel, URL)

for source, feed_url in RSS_FEEDS:
    try:
        xml_text = fetch(feed_url, timeout=15)
        root = ET.fromstring(xml_text)
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link  = (item.findtext("link")  or "").strip()
            if title and link and link.startswith("http"):
                articles.append((source, title, link))
                break  # nur neuester Artikel pro Quelle
        print(f"  RSS {source}: OK")
    except Exception as e:
        print(f"  ✗ RSS {source}: {e}")


# ── 3. Nachricht zusammenstellen ─────────────────────────────────────────────

now_berlin = datetime.now(timezone.utc) + timedelta(hours=2)
day_de  = DAYS_DE.get(now_berlin.strftime("%A"), now_berlin.strftime("%A"))
date_de = now_berlin.strftime("%d.%m.%Y")

weather_block = (
    "\n".join(weather_lines) if weather_lines else "Wetter nicht verfügbar"
)

news_lines = []
for i, (src, title, url) in enumerate(articles[:5], 1):
    news_lines.append(f"{i}. {src}: {title}\n   🔗 {url}")
news_block = "\n".join(news_lines) if news_lines else "News nicht verfügbar"

message = (
    f"🌅 Morning Briefing – {day_de}, {date_de}\n\n"
    f"📍 {city}\n\n"
    f"⏰ Wetter heute:\n{weather_block}\n\n"
    f"📰 Top News:\n{news_block}"
)

print("\n── Nachricht ─────────────────────────────────")
print(message)
print("──────────────────────────────────────────────\n")


# ── 4. WhatsApp senden ────────────────────────────────────────────────────────

params = urllib.parse.urlencode({
    "phone":  CALLMEBOT_PHONE,
    "apikey": CALLMEBOT_KEY,
    "text":   message,
})
result = fetch(f"https://api.callmebot.com/whatsapp.php?{params}", timeout=15)
print(f"  WhatsApp: {result.strip()}")
