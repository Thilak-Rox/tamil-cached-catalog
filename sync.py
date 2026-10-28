import os
import re
import json
import time
import requests

# Secrets / Config from GitHub Actions
MDBLIST_API_KEY = os.environ.get("MDBLIST_API_KEY", "").strip()
# Use your exact MDBList URL path: "2fineadventure/english-hits-tamil-dubbed"
MDBLIST_LIST_PATH = os.environ.get("MDBLIST_LIST_PATH", "2fineadventure/english-hits-tamil-dubbed").strip()
TORBOX_API_KEY = os.environ.get("TORBOX_API_KEY", "").strip()

TAMIL_REGEX = re.compile(r"(?i)(?:\b(tam|tamil|1tamilmv|tamilblasters|tamilyogi)\b|\[.*?(tam|tamil).*?\])")
INFOHASH_REGEX = re.compile(r"\b([a-fA-F0-9]{40})\b")

SCRAPER_URLS = [
    "https://torrentio.strem.fun/stream/movie/{imdb_id}.json",
    "https://mediafusion.elfhosted.com/stream/movie/{imdb_id}.json"
]

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

def fetch_mdblist_candidates(list_paths_str):
    candidates = []
    seen = set()
    for raw_path in list_paths_str.split(","):
        p = raw_path.strip().replace("https://mdblist.com/lists/", "").strip("/")
        if not p:
            continue
        
        # MDBList API endpoint supports /lists/{username}/{listname}/items
        url = f"https://api.mdblist.com/lists/{p}/items?apikey={MDBLIST_API_KEY}"
        print(f"Fetching clean MDBList candidates from: {p}...")
        resp = requests.get(url, headers=HEADERS, timeout=25)
        if resp.status_code != 200:
            print(f"MDBList API error ({resp.status_code}): {resp.text}")
            continue
        
        data = resp.json()
        items = data.get("movies", data) if isinstance(data, dict) else data
        for item in items:
            imdb_id = item.get("imdb_id")
            if imdb_id and imdb_id.startswith("tt") and imdb_id not in seen:
                seen.add(imdb_id)
                candidates.append({
                    "id": imdb_id,
                    "type": "movie",
                    "name": item.get("title", imdb_id),
                    "poster": item.get("poster") or f"https://images.metahub.space/poster/medium/{imdb_id}/img",
                    "description": item.get("description", "")
                })
    print(f"Total candidate movies from MDBList: {len(candidates)}")
    return candidates

def find_tamil_hashes(imdb_id):
    hashes = set()
    for template in SCRAPER_URLS:
        url = template.format(imdb_id=imdb_id)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=12)
            if resp.status_code != 200:
                continue
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
        except Exception:
            pass
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

def build_static_stremio_addon(verified_metas):
    # Ensure public output directory exists
    os.makedirs("public/catalog/movie", exist_ok=True)
    
    manifest = {
        "id": "org.community.dads.tamil.cached",
        "version": "1.0.0",
        "name": "Dad's Tamil Dubbed (TorBox Cached)",
        "description": "Verified cached Tamil-dubbed movies with zero nudity.",
        "resources": ["catalog"],
        "types": ["movie"],
        "catalogs": [
            {
                "type": "movie",
                "id": "tamil_cached_catalog",
                "name": "Tamil Dubbed (Instant TorBox)"
            }
        ],
        "idPrefixes": ["tt"]
    }
    
    # 1. Write manifest.json
    with open("public/manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        
    # 2. Write the Stremio / Nuvio catalog endpoint
    catalog_payload = {
        "metas": verified_metas
    }
    with open("public/catalog/movie/tamil_cached_catalog.json", "w", encoding="utf-8") as f:
        json.dump(catalog_payload, f, indent=2)
        
    print(f"Successfully generated static catalog with {len(verified_metas)} verified cached movies!")

def main():
    candidates = fetch_mdblist_candidates(MDBLIST_LIST_PATH)
    candidate_hash_map = {}
    all_hashes = set()
    
    for idx, c in enumerate(candidates, 1):
        imdb_id = c["id"]
        hashes = find_tamil_hashes(imdb_id)
        if hashes:
            candidate_hash_map[imdb_id] = hashes
            all_hashes.update(hashes)
            print(f"[{idx}/{len(candidates)}] Found {len(hashes)} Tamil release(s) for {c['name']} ({imdb_id})")
        time.sleep(0.3)
        
    print(f"Checking {len(all_hashes)} unique Tamil hashes against TorBox cache...")
    cached_hashes = check_torbox_cached(list(all_hashes))
    print(f"TorBox reports {len(cached_hashes)} cached Tamil releases.")
    
    verified_metas = []
    for c in candidates:
        imdb_id = c["id"]
        hashes = candidate_hash_map.get(imdb_id, [])
        if any(h in cached_hashes for h in hashes):
            verified_metas.append({
                "id": imdb_id,
                "type": "movie",
                "name": c["name"],
                "poster": c["poster"],
                "description": c["description"]
            })
            
    build_static_stremio_addon(verified_metas)

if __name__ == "__main__":
    main()
