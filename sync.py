import os
import re
import time
import requests

# Configuration via Environment Variables (GitHub Secrets)
MDBLIST_API_KEY = os.environ.get("MDBLIST_API_KEY", "").strip()
MDBLIST_LIST_ID = os.environ.get("MDBLIST_LIST_ID", "").strip()  # Comma-separated if you have both English & Indian lists
TORBOX_API_KEY = os.environ.get("TORBOX_API_KEY", "").strip()
TRAKT_CLIENT_ID = os.environ.get("TRAKT_CLIENT_ID", "").strip()
TRAKT_ACCESS_TOKEN = os.environ.get("TRAKT_ACCESS_TOKEN", "").strip()
TRAKT_USERNAME = os.environ.get("TRAKT_USERNAME", "").strip()
TRAKT_LIST_SLUG = os.environ.get("TRAKT_LIST_SLUG", "dads-cached-tamil-dubs").strip()

# Regex to strictly detect Tamil audio tracks or trusted South Indian release groups
TAMIL_REGEX = re.compile(r"(?i)(?:\b(tam|tamil|1tamilmv|tamilblasters|tamilyogi)\b|\[.*?(tam|tamil).*?\])")
INFOHASH_REGEX = re.compile(r"\b([a-fA-F0-9]{40})\b")

# Stream scrapers to query for Tamil torrents (Torrentio + MediaFusion public instances)
SCRAPER_URLS = [
    "https://torrentio.strem.fun/stream/movie/{imdb_id}.json",
    "https://mediafusion.elfhosted.com/stream/movie/{imdb_id}.json"
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

def fetch_mdblist_movies(list_ids_str):
    movies = []
    seen = set()
    for list_id in list_ids_str.split(","):
        list_id = list_id.strip()
        if not list_id:
            continue
        url = f"https://api.mdblist.com/lists/{list_id}/items?apikey={MDBLIST_API_KEY}"
        print(f"Fetching MDBList ID: {list_id}...")
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            print(f"Error fetching MDBList {list_id}: {resp.status_code} {resp.text}")
            continue
        data = resp.json()
        items = data.get("movies", data) if isinstance(data, dict) else data
        for item in items:
            imdb_id = item.get("imdb_id")
            if imdb_id and imdb_id.startswith("tt") and imdb_id not in seen:
                seen.add(imdb_id)
                movies.append({
                    "imdb_id": imdb_id,
                    "title": item.get("title", imdb_id)
                })
    print(f"Total unique clean candidate movies from MDBList: {len(movies)}")
    return movies

def find_tamil_hashes_for_movie(imdb_id):
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
        except Exception as e:
            pass
    return list(hashes)

def check_torbox_cached_batches(all_hashes):
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
            print(f"TorBox check error: {e}")
        time.sleep(0.5)
    return cached_set

def sync_to_trakt(verified_imdb_ids):
    if not verified_imdb_ids:
        print("No verified cached movies found to sync.")
        return

    url = f"https://api.trakt.tv/users/{TRAKT_USERNAME}/lists/{TRAKT_LIST_SLUG}/items"
    headers = {
        "Content-Type": "application/json",
        "trakt-api-version": "2",
        "trakt-api-key": TRAKT_CLIENT_ID,
        "Authorization": f"Bearer {TRAKT_ACCESS_TOKEN}"
    }
    payload = {
        "movies": [{"ids": {"imdb": imdb_id}} for imdb_id in verified_imdb_ids]
    }
    resp = requests.post(url, json=payload, headers=headers, timeout=20)
    print(f"Trakt Sync Status: {resp.status_code} - {resp.text}")

def main():
    movies = fetch_mdblist_movies(MDBLIST_LIST_ID)
    movie_hash_map = {}
    all_candidate_hashes = set()

    for idx, m in enumerate(movies, 1):
        imdb_id = m["imdb_id"]
        hashes = find_tamil_hashes_for_movie(imdb_id)
        if hashes:
            movie_hash_map[imdb_id] = hashes
            all_candidate_hashes.update(hashes)
            print(f"[{idx}/{len(movies)}] Found {len(hashes)} Tamil hash(es) for {m['title']} ({imdb_id})")
        time.sleep(0.4)  # Polite rate limit for scrapers

    print(f"Checking {len(all_candidate_hashes)} unique Tamil hashes against TorBox cache...")
    cached_hashes = check_torbox_cached_batches(list(all_candidate_hashes))
    print(f"TorBox reported {len(cached_hashes)} cached Tamil hashes.")

    verified_movies = []
    for imdb_id, hashes in movie_hash_map.items():
        if any(h in cached_hashes for h in hashes):
            verified_movies.append(imdb_id)

    print(f"Verified {len(verified_movies)} movies with instant TorBox-cached Tamil audio!")
    sync_to_trakt(verified_movies)

if __name__ == "__main__":
    main()
