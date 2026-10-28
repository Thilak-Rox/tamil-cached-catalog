import os
import re
import json
import time
import requests

# Secrets / Config from GitHub Actions
MDBLIST_API_KEY = os.environ.get("MDBLIST_API_KEY", "").strip()
TORBOX_API_KEY = os.environ.get("TORBOX_API_KEY", "").strip()

# Catalog definitions (Separate clean rows)
CATALOG_CONFIGS = [
    {
        "id": "tamil_hollywood",
        "name": "Hollywood (Tamil Dubbed)",
        "path": os.environ.get("MDBLIST_HOLLYWOOD_PATH", "2fineadventure/english-hits-tamil-dubbed").strip()
    },
    {
        "id": "tamil_indian",
        "name": "Indian Hits (Tamil Dubbed)",
        "path": os.environ.get("MDBLIST_INDIAN_PATH", "2fineadventure/indian-hits-tamil-dubbed").strip()
    }
]

TAMIL_REGEX = re.compile(r"(?i)(?:\b(tam|tamil|1tamilmv|tamilblasters|tamilyogi)\b|\[.*?(tam|tamil).*?\])")
INFOHASH_REGEX = re.compile(r"\b([a-fA-F0-9]{40})\b")

# Updated to use Midnight's MediaFusion instance instead of ElfHosted
SCRAPER_URLS = [
    "https://mediafusionfortheweebs.midnightignite.me/stream/movie/{imdb_id}.json",
    "https://torrentio.strem.fun/stream/movie/{imdb_id}.json"
]

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

def fetch_all_mdblist_items(slug_or_path):
    """Paginates through MDBList API to ensure all 700+ movies are fetched, not just the first page."""
    p = slug_or_path.replace("https://mdblist.com/lists/", "").strip("/")
    if not p:
        return []
    
    candidates = []
    seen = set()
    offset = 0
    limit = 100
    
    print(f"Fetching clean candidates from MDBList: {p}...")
    while True:
        url = f"https://api.mdblist.com/lists/{p}/items?apikey={MDBLIST_API_KEY}&limit={limit}&offset={offset}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=25)
            if resp.status_code != 200:
                print(f"MDBList API error ({resp.status_code}): {resp.text}")
                break
            
            data = resp.json()
            items = data.get("movies", data) if isinstance(data, dict) else data
            if not items or not isinstance(items, list):
                break
            
            for item in items:
                imdb_id = item.get("imdb_id")
                if imdb_id and imdb_id.startswith("tt") and imdb_id not in seen:
                    seen.add(imdb_id)
                    candidates.append({
                        "id": imdb_id,
                        "type": "movie",
                        "name": item.get("title", imdb_id),
                        "poster": item.get("poster") or f"https://images.metahub.space/poster/medium/{imdb_id}/img",
                        "description": item.get("description", ""),
                        "released": item.get("release_date") or item.get("released") or "",
                        "score": item.get("score") or 0
                    })
            
            has_more = resp.headers.get("X-Has-More", "").lower() == "true"
            if len(items) < limit and not has_more:
                break
                
            offset += limit
            time.sleep(0.2)
        except Exception as e:
            print(f"Failed to fetch page offset {offset} for {p}: {e}")
            break
            
    print(f"Loaded total {len(candidates)} candidates from {p}")
    return candidates

def find_tamil_hashes(imdb_id):
    """Queries scrapers with 429 rate-limit backoff handling."""
    hashes = set()
    for template in SCRAPER_URLS:
        url = template.format(imdb_id=imdb_id)
        for attempt in range(2):
            try:
                resp = requests.get(url, headers=HEADERS, timeout=12)
                if resp.status_code == 429:
                    time.sleep(1.5)
                    continue
                if resp.status_code != 200:
                    break
                    
                streams = resp.json().get("streams", [])
                for s in streams:
                    text_blob = f"{s.get('name', '')} {s.get('title', '')} {s.get('description', '')} {s.get('behaviorHints', {}).get('filename', '')}"
                    if TAMIL_REGEX.search(text_blob):
                        ih = s.get("infoHash")
                        if not ih and s.get("url"):
                            m = INFOHASH_REGEX.search(s.get("url", ""))
                            if m:
                                ih = m.group(1)
                        if ih:
                            hashes.add(ih.lower())
                break
            except Exception:
                break
    return list(hashes)

def check_torbox_cached(all_hashes):
    cached_set = set()
    if not all_hashes:
        return cached_set
        
    batch_size = 50
    headers = {"Authorization": f"Bearer {TORBOX_API_KEY}"}
    for i in range(0, len(all_hashes), batch_size):
        batch = all_hashes[i:i + batch_size]
        hash_csv = ",".join(batch)
        url = f"https://api.torbox.app/v1/api/torrents/checkcached?hash={hash_csv}&format=list"
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                payload = resp.json()
                data = payload.get("data", [])
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get("hash"):
                            cached_set.add(item["hash"].lower())
                        elif isinstance(item, str):
                            cached_set.add(item.lower())
                elif isinstance(data, dict):
                    for h, val in data.items():
                        if val:
                            cached_set.add(h.lower())
        except Exception as e:
            print(f"TorBox API check failed: {e}")
        time.sleep(0.4)
    return cached_set

def main():
    # 1. Fetch all candidates per catalog using pagination
    catalogs_data = []
    unique_imdb_ids = {}

    for cat in CATALOG_CONFIGS:
        items = fetch_all_mdblist_items(cat["path"])
        catalogs_data.append({
            "id": cat["id"],
            "name": cat["name"],
            "items": items
        })
        for it in items:
            unique_imdb_ids[it["id"]] = it["name"]

    print(f"\nTotal unique IMDb candidates to verify across both rows: {len(unique_imdb_ids)}")

    # 2. Scrape Tamil hashes
    movie_hash_map = {}
    all_hashes = set()
    for idx, (imdb_id, title) in enumerate(unique_imdb_ids.items(), 1):
        hashes = find_tamil_hashes(imdb_id)
        if hashes:
            movie_hash_map[imdb_id] = hashes
            all_hashes.update(hashes)
            print(f"[{idx}/{len(unique_imdb_ids)}] Found {len(hashes)} Tamil release(s) for {title} ({imdb_id})")
        time.sleep(0.3)

    # 3. Check TorBox cache in batches
    print(f"\nChecking {len(all_hashes)} candidate hashes with TorBox API...")
    cached_hashes = check_torbox_cached(list(all_hashes))
    print(f"TorBox verified {len(cached_hashes)} cached Tamil releases.")

    # 4. Generate manifest with 2 separate clean rows
    os.makedirs("public/catalog/movie", exist_ok=True)
    manifest = {
        "id": "org.community.tamil.dubs.cached",
        "version": "1.3.0",
        "name": "Tamil Dubs",
        "description": "Curated Tamil-dubbed releases cached on TorBox.",
        "resources": ["catalog"],
        "types": ["movie"],
        "catalogs": [
            {
                "type": "movie",
                "id": cat["id"],
                "name": cat["name"]
            }
            for cat in CATALOG_CONFIGS
        ],
        "idPrefixes": ["tt"]
    }

    with open("public/manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    # 5. Build JSON file for each row, preserving exact MDBList sort order
    for cat in catalogs_data:
        cat_id = cat["id"]
        cat_name = cat["name"]
        verified_metas = []

        for item in cat["items"]:
            imdb_id = item["id"]
            hashes = movie_hash_map.get(imdb_id, [])
            if any(h in cached_hashes for h in hashes):
                verified_metas.append({
                    "id": imdb_id,
                    "type": "movie",
                    "name": item["name"],
                    "poster": item["poster"],
                    "description": item["description"]
                })

        output_path = f"public/catalog/movie/{cat_id}.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump({"metas": verified_metas}, f, indent=2)

        print(f"Catalog '{cat_name}' ({cat_id}): {len(verified_metas)} verified cached movies saved.")

if __name__ == "__main__":
    main()
