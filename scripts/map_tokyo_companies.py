"""Map Tokyo's densest registered addresses from the gBizINFO Tokyo CSV.
Usage: pip install folium, then run from scripts/ . Writes top_addresses.csv and map.html.
Geocodes are cached in geocache.json, so reruns skip finished addresses.
"""
import csv
import html
import json
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

import folium

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SRC = DATA_DIR / "kihonjoho_tokyo.csv"
CACHE = DATA_DIR / "geocache.json"
TOP_CSV = DATA_DIR / "top_addresses.csv"
MAP_HTML = Path(__file__).resolve().parent.parent / "map.html"

ADDRESS_COL = "登記住所"
CLOSURE_COL = "登記記録の閉鎖等年月日"
TOP_N = 300
GSI_URL = "https://msearch.gsi.go.jp/address-search/AddressSearch?q="
PINK = "#f4a6c0"
DEEP_PINK = "#d6588a"

KANJI = {c: i for i, c in enumerate("一二三四五六七八九", 1)}


def kanji_to_int(match):
    """Handles 一 to 九十九. Only ever applied directly before 丁目."""
    total, digit = 0, 0
    for char in match.group():
        if char == "十":
            total += (digit or 1) * 10
            digit = 0
        else:
            digit = KANJI[char]
    return str(total + digit)


def clean(raw):
    s = unicodedata.normalize("NFKC", raw).replace(" ", "").replace("ヶ", "ケ")
    s = re.sub(r"[一二三四五六七八九十]+(?=丁目)", kanji_to_int, s)
    return re.sub(r"(?<=\d)[-‐−–—―ー]+(?=\d)", "-", s)


def split_address(raw):
    """Return (building_key, unit) or None. Registry addresses glue the
    building and room onto the street number, so the key stops at the number."""
    s = clean(raw)
    s = re.sub(r"(?<=\d)丁目", "-", s)
    s = re.sub(r"(?<=\d)(?:番地の|番地|番の|番|の)(?=\d)", "-", s)
    m = re.match(r"(\D+\d+(?:-\d+(?![\d階F])){0,2})(.*)", s)
    if not m:
        return None
    return m.group(1), re.sub(r"^(?:番地|番|号|-)+", "", m.group(2))


def count_buildings():
    per_building = defaultdict(Counter)
    skipped_closed = skipped_unparsed = 0
    with open(SRC, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = [c.lstrip("\ufeff").strip('"') for c in next(reader)]
        assert ADDRESS_COL in header, header
        addr_i = header.index(ADDRESS_COL)
        closed_i = header.index(CLOSURE_COL) if CLOSURE_COL in header else None
        for row in reader:
            if len(row) <= addr_i:
                skipped_unparsed += 1
                continue
            if closed_i is not None and row[closed_i].strip():
                skipped_closed += 1
                continue
            parts = split_address(row[addr_i])
            if parts is None:
                skipped_unparsed += 1
                continue
            per_building[parts[0]][parts[1]] += 1
    print(f"{len(per_building):,} buildings, skipped {skipped_closed:,} closed, "
          f"{skipped_unparsed:,} unparsed")
    return per_building


def geocode(key, cache):
    if key in cache:
        return cache[key]
    url = GSI_URL + urllib.parse.quote(key)
    request = urllib.request.Request(url, headers={"User-Agent": "shell-map"})
    with urllib.request.urlopen(request, timeout=20) as response:
        hits = json.load(response)
    time.sleep(0.5)
    if not hits:
        result = None
    else:
        lon, lat = hits[0]["geometry"]["coordinates"]
        result = {"lat": lat, "lon": lon,
                  "matched": hits[0]["properties"]["title"]}
    cache[key] = result
    CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return result


def main():
    per_building = count_buildings()
    ranked = sorted(per_building.items(), key=lambda kv: -
                    sum(kv[1].values()))[:TOP_N]
    cache = json.loads(CACHE.read_text(encoding="utf-8")
                       ) if CACHE.exists() else {}

    rows, misses = [], []
    for key, units in ranked:
        hit = geocode(key, cache)
        ward = re.match(r"東京都.+?[区市町村]", key)
        if hit is None or (ward and not hit["matched"].startswith(ward.group())):
            misses.append(key)
            continue
        unit, unit_count = units.most_common(1)[0]
        rows.append({
            "address": key, "companies": sum(units.values()),
            "top_unit": unit, "top_unit_companies": unit_count,
            "lat": hit["lat"], "lon": hit["lon"], "gsi_matched": hit["matched"],
        })

    with open(TOP_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    points = {(r["lat"], r["lon"]) for r in rows}
    print(
        f"mapped {len(rows)}, {len(points)} unique points, {len(misses)} misses")
    print("misses:", misses[:20])

    tokyo_map = folium.Map(
        location=[35.68, 139.75], zoom_start=12,
        tiles="https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png",
        attr='<a href="https://maps.gsi.go.jp/development/ichiran.html">地理院タイル</a>',
    )
    for r in rows:
        popup = (f"<b>{html.escape(r['address'])}</b><br>"
                 f"{r['companies']} registered companies<br>"
                 f"Most at one unit: {r['top_unit_companies']} "
                 f"({html.escape(r['top_unit']) or 'no unit given'})")
        folium.CircleMarker(
            location=[r["lat"], r["lon"]],
            radius=max(3, r["companies"] ** 0.5 / 1.5),
            color=DEEP_PINK, weight=1,
            fill=True, fill_color=PINK, fill_opacity=0.55,
            popup=folium.Popup(popup, max_width=320),
        ).add_to(tokyo_map)
    tokyo_map.save(MAP_HTML)
    print(f"saved {MAP_HTML}")


if __name__ == "__main__":
    main()
