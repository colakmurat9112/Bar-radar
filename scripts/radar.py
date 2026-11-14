#!/usr/bin/env python3
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
STATE_PATH = ROOT / "state.json"
RESULTS_PATH = ROOT / "docs" / "results.json"

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")
ACTOR = "compass~crawler-google-places"
APIFY = "https://api.apify.com/v2"

def log(*a):
    print(*a, flush=True)

def http(method, url, payload=None, headers=None, timeout=90):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def parse_date(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None

def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default

def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

def apify_run(actor_input, max_usd):
    auth = {"Authorization": f"Bearer {APIFY_TOKEN}"}
    run = http("POST", f"{APIFY}/acts/{ACTOR}/runs", actor_input, auth)["data"]
    run_id = run["id"]
    deadline = time.time() + 25 * 60
    status = run["status"]
    info = run
    while time.time() < deadline:
        info = http("GET", f"{APIFY}/actor-runs/{run_id}", headers=auth)["data"]
        status = info["status"]
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            break
        time.sleep(10)
    else:
        http("POST", f"{APIFY}/actor-runs/{run_id}/abort", {}, auth)
        raise RuntimeError("Apify run zaman aşımına uğradı")
    ds = info["defaultDatasetId"]
    items = http(
        "GET", f"{APIFY}/datasets/{ds}/items?format=json&clean=true", headers=auth, timeout=180
    )
    log(f"  Apify run {status}, {len(items)} öğe")
    return items

def place_id(p):
    return p.get("placeId") or p.get("cid") or p.get("url")

def stage1_search(city_query):
    out = []
    for term in CFG["search_terms"]:
        items = apify_run(
            {
                "searchStringsArray": [term],
                "locationQuery": city_query,
                "maxCrawledPlacesPerSearch": CFG["places_per_city"],
                "language": "en",
                "maxReviews": 0,
                "scrapePlaceDetailPage": False,
                "skipClosedPlaces": True,
            },
            CFG["max_usd_per_search"],
        )
        out.extend(items)
    return out

def passes_cheap_filter(p):
    if p.get("permanentlyClosed") or p.get("temporarilyClosed"):
        return False
    rating = p.get("totalScore") or 0
    n = p.get("reviewsCount") or 0
    return rating >= CFG["min_rating"] and CFG["min_reviews"] <= n <= CFG["max_reviews_count"]

def stage2_reviews(candidates):
    items = apify_run(
        {
            "startUrls": [{"url": c["url"]} for c in candidates],
            "maxReviews": CFG["max_reviews_fetch"],
            "reviewsSort": "newest",
            "scrapeReviewsPersonalData": False,
            "language": "en",
            "maxCrawledPlacesPerSearch": 1,
        },
        CFG["max_usd_detail_run"],
    )
    by = {}
    for it in items:
        if isinstance(it.get("reviews"), list):
            by.setdefault(place_id(it), []).extend(it["reviews"])
        elif it.get("publishedAtDate") or it.get("reviewId"):
            by.setdefault(it.get("placeId") or it.get("placeUrl"), []).append(it)
    return by

def main():
    if not APIFY_TOKEN:
        sys.exit("APIFY_TOKEN ortam değişkeni gerekli.")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=int(CFG["max_age_months"] * 30.4))
    state = load_json(STATE_PATH, {"city_idx": 0, "seen": {}})
    results = load_json(RESULTS_PATH, [])
    cities = CFG["cities"]

    start = state["city_idx"] % len(cities)
    batch = [cities[(start + i) % len(cities)] for i in range(min(CFG["cities_per_run"], len(cities)))]
    state["city_idx"] = (start + len(batch)) % len(cities)
    log("Bu hafta:", ", ".join(c["name"] for c in batch))

    candidates = []
    for c in batch:
        try:
            places = stage1_search(c["query"])
        except Exception as e:
            log(f"  {c['name']} atlandı: {e}")
            continue
        for p in places:
            pid = place_id(p)
            if not pid or pid in state["seen"] or not p.get("url"):
                continue
            if passes_cheap_filter(p):
                p["_city"] = c["name"]
                candidates.append(p)
        log(f"{c['name']}: {len(places)} mekan tarandı, toplam aday {len(candidates)}")

    candidates.sort(key=lambda p: p.get("reviewsCount") or 0)
    candidates = candidates[: CFG["max_detail_per_run"]]
    if not candidates:
        log("Yeni aday yok.")
        save_json(STATE_PATH, state)
        return

    log(f"{len(candidates)} adayın yorumları çekiliyor...")
    reviews_by = stage2_reviews(candidates)

    added = 0
    for p in candidates:
        pid = place_id(p)
        revs = reviews_by.get(pid) or reviews_by.get(p.get("url")) or []
        dates = [d for d in (parse_date(r.get("publishedAtDate")) for r in revs) if d]
        if not dates:
            log(f"  {p.get('title')}: yorum verisi yok, atlandı (tekrar denenecek)")
            continue
        first = min(dates)
        if first < cutoff:
            state["seen"][pid] = {"status": "old", "first_review": first.date().isoformat()}
            log(f"  {p.get('title')}: ELENDİ (ilk yorum {first.date()})")
            continue
        
        # YAPAY ZEKA İPTAL EDİLDİ - DİREKT LİSTEYE EKLİYORUZ
        summary = f"Mekan filtreden geçti! {p.get('reviewsCount')} yoruma ve {p.get('totalScore')} yıldıza sahip. Menü, ortam ve detaylar için aşağıdaki butondan Google Maps'e git."
        
        results.append(
            {
                "id": pid,
                "name": p.get("title"),
                "city": p["_city"],
                "address": p.get("address"),
                "rating": p.get("totalScore"),
                "reviews_count": p.get("reviewsCount"),
                "first_review": first.date().isoformat(),
                "summary": summary,
                "maps_url": p.get("url"),
                "added": now.date().isoformat(),
            }
        )
        state["seen"][pid] = {"status": "ok", "first_review": first.date().isoformat()}
        added += 1
        log(f"  {p.get('title')}: EKLENDİ (ilk yorum {first.date()})")

    results.sort(key=lambda r: (r["added"], r.get("rating") or 0), reverse=True)
    save_json(RESULTS_PATH, results)
    save_json(STATE_PATH, state)
    log(f"Bitti. Bu hafta {added} yeni mekan eklendi, toplam {len(results)}.")

if __name__ == "__main__":
    main()
