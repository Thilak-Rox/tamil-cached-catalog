import os
import re
import json
import time
import requests

# Secrets / Config from GitHub Actions
MDBLIST_API_KEY = os.environ.get("MDBLIST_API_KEY", "").strip()
TORBOX_API_KEY = os.environ.get("TORBOX_API_KEY", "").strip()

# Minimum size constraint for Hollywood catalog
HOLLYWOOD_MIN_SIZE_GB = 15.0

# Catalog definitions
CATALOG_CONFIGS = [
    {
        "id": "tamil_hollywood",
        "name": "Hollywood (Tamil Dubbed)",
        "path": os.environ.get("MDBLIST_HOLLYWOOD_PATH", "2fineadventure/english-hits-tamil-dubbed").strip(),
        "is_indian": False
    },
    {
        "id": "tamil_indian",
        "name": "Indian Hits (Tamil Dubbed)",
        "path": os.environ.get("MDBLIST_INDIAN_PATH", "2fineadventure/indian-hits-tamil-dubbed").strip(),
        "is_indian": True
    }
]

# Audio matching: Tamil audio, regional trackers, bracketed tags [Tam+Tel] or (Tamil+Hindi), and Multi/Dual audio
TAMIL_REGEX = re.compile(
    r"(?i)("
    r"\b(tam|tamil|1tamilmv|tamilblasters|tamilyogi)\b|"
    r"\[[^\]]*(?:tam|tamil|multi)[^\]]*\]|"
    r"\([^)]*(?:tam|tamil|multi)[^)]*\)|"
    r"\b(?:multi|dual)[\s\.\-_]?(?:audio|dub)\b"
    r")"
)

# Resolution filters
HOLLYWOOD_RES_REGEX = re.compile(r"(?i)\b(1080p|1080i|2160p|4k|uhd|fhd)\b")
DISALLOWED_RES_REGEX = re.compile(r"(?i)\b(720p|480p|576p|360p|cam|hdcam|telesync|hdts|predvd|dvdrip)\b")
INFOHASH_REGEX = re.compile(r"\b([a-fA-F0-9]{40})\b")

SCRAPER_URLS = [
    "https://mediafusionfortheweebs.midnightignite.me/stream/movie/{imdb_id}.json",
    "https://torrentio.strem.fun/stream/movie/{imdb_id}.json"
]

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

def fetch_all_mdblist_items(slug_or_path):
    p = slug_or_path.replace("https://mdblist.com/lists/", "").strip("/")
    if not p:
        return []
    
    candidates = []
    seen = set()
    offset = 0
    limit = 100
    
    print(f"Fetching candidates from MDBList: {p}...")
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

def extract_stream_size_gb(stream):
    """Extracts stream file size in GB from stream object or text metadata."""
    # 1. Check Stremio behaviorHints.videoSize (bytes)
    video_size = stream.get("behaviorHints", {}).get("videoSize")
    if isinstance(video_size, (int, float)) and video_size > 0:
        return video_size / (1024 ** 3)

    text_blob = f"{stream.get('title', '')} {stream.get('description', '')} {stream.get('name', '')}"

    # 2. Match floppy disk emoji format: 💾 72.4 GB or 💾 15.6 GiB
    emoji_match = re.search(r"💾\s*([0-9]+(?:\.[0-9]+)?)\s*(TB|TiB|GB|GiB|MB|MiB)", text_blob, re.IGNORECASE)
    if emoji_match:
        val = float(emoji_match.group(1))
        unit = emoji_match.group(2).upper()
        if "TB" in unit:
            return val * 1024.0
        if "GB" in unit:
            return val
        if "MB" in unit:
            return val / 1024.0

    # 3. Match generic patterns: e.g. '72.4 GB', '15.5GiB'
    matches = re.findall(r"\b([0-9]+(?:\.[0-9]+)?)\s*(TB|TiB|GB|GiB|MB|MiB)\b", text_blob, re.IGNORECASE)
    for val_str, unit_str in matches:
        try:
            val = float(val_str)
            unit = unit_str.upper()
            if "TB" in unit:
                return val * 1024.0
            if "GB" in unit:
                return val
            if "MB" in unit:
                return val / 1024.0
        except ValueError:
            continue

    return None

def get_stream_infohash(stream):
    ih = stream.get("infoHash")
    if not ih and stream.get("url"):
        m = INFOHASH_REGEX.search(stream.get("url", ""))
        if m:
            ih = m.group(1)
    return ih.lower() if ih else None

def find_movie_streams(imdb_id):
    """Scrapes raw stream objects from both MediaFusion and Torrentio."""
    streams = []
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
                    
                data = resp.json().get("streams", [])
                streams.extend(data)
                break
            except Exception:
                break
    return streams

def check_torbox_cached_details(all_hashes):
    """Checks cache status and file size metadata using TorBox format=object."""
    cached_info = {}
    if not all_hashes or not TORBOX_API_KEY:
        return cached_info
        
    batch_size = 50
    headers = {"Authorization": f"Bearer {TORBOX_API_KEY}"}
    for i in range(0, len(all_hashes), batch_size):
        batch = all_hashes[i:i + batch_size]
        hash_csv = ",".join(batch)
        url = f"https://api.torbox.app/v1/api/torrents/checkcached?hash={hash_csv}&format=object"
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                payload = resp.json()
                data = payload.get("data", {})
                if isinstance(data, dict):
                    for h, item in data.items():
                        if item:
                            h_lower = h.lower()
                            size_bytes = item.get("size") if isinstance(item, dict) else None
                            size_gb = (size_bytes / (1024 ** 3)) if isinstance(size_bytes, (int, float)) else None
                            cached_info[h_lower] = {
                                "cached": True,
                                "size_gb": size_gb
                            }
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get("hash"):
                            h_lower = item["hash"].lower()
                            size_bytes = item.get("size")
                            size_gb = (size_bytes / (1024 ** 3)) if isinstance(size_bytes, (int, float)) else None
                            cached_info[h_lower] = {
                                "cached": True,
                                "size_gb": size_gb
                            }
                        elif isinstance(item, str):
                            cached_info[item.lower()] = {
                                "cached": True,
                                "size_gb": None
                            }
        except Exception as e:
            print(f"TorBox API check failed: {e}")
        time.sleep(0.4)
    return cached_info

def is_valid_hollywood_stream(stream, cached_info):
    """Strict filter: Tamil audio + 1080p/4K only (no 720p/CAM) + min 15GB + TorBox cached."""
    ih = get_stream_infohash(stream)
    if not ih or ih not in cached_info:
        return False

    text_blob = f"{stream.get('name', '')} {stream.get('title', '')} {stream.get('description', '')} {stream.get('behaviorHints', {}).get('filename', '')}"

    # 1. Must match Tamil / Multi audio
    if not TAMIL_REGEX.search(text_blob):
        return False

    # 2. Reject 720p, 480p, and cam/theatrical rips
    if DISALLOWED_RES_REGEX.search(text_blob):
        return False

    # 3. Must be at least 1080p or 4K/UHD
    if not HOLLYWOOD_RES_REGEX.search(text_blob):
        return False

    # 4. Enforce minimum 15 GB size
    size_gb = extract_stream_size_gb(stream)
    if size_gb is None:
        size_gb = cached_info[ih].get("size_gb")

    if size_gb is not None:
        if size_gb < HOLLYWOOD_MIN_SIZE_GB:
            return False
    else:
        # Fallback if size is entirely missing from metadata: require Remux/BluRay tag
        if not re.search(r"(?i)\b(remux|bdremux|uhd|bluray)\b", text_blob):
            return False

    return True

def is_valid_indian_stream(stream, cached_info):
    """Indian filter: Native/regional release + TorBox cached + excludes CAM/Telesync rips."""
    ih = get_stream_infohash(stream)
    if not ih or ih not in cached_info:
        return False

    text_blob = f"{stream.get('name', '')} {stream.get('title', '')} {stream.get('description', '')} {stream.get('behaviorHints', {}).get('filename', '')}"

    # Exclude CAM/Telesync theater recordings
    if re.search(r"(?i)\b(cam|hdcam|telesync|hdts|predvd)\b", text_blob):
        return False

    return True

def main():
    catalogs_data = []
    unique_candidates = {}

    for cat in CATALOG_CONFIGS:
        items = fetch_all_mdblist_items(cat["path"])
        catalogs_data.append({
            "id": cat["id"],
            "name": cat["name"],
            "is_indian": cat["is_indian"],
            "items": items
        })
        for it in items:
            unique_candidates[it["id"]] = it["name"]

    print(f"\nTotal unique IMDb candidates to query: {len(unique_candidates)}")

    movie_streams_map = {}
    all_hashes = set()
    
    for idx, (imdb_id, title) in enumerate(unique_candidates.items(), 1):
        streams = find_movie_streams(imdb_id)
        if streams:
            movie_streams_map[imdb_id] = streams
            for s in streams:
                ih = get_stream_infohash(s)
                if ih:
                    all_hashes.add(ih)
            print(f"[{idx}/{len(unique_candidates)}] Found {len(streams)} stream(s) for {title} ({imdb_id})")
        else:
            print(f"[{idx}/{len(unique_candidates)}] No streams found for {title} ({imdb_id})")
        time.sleep(0.3)

    print(f"\nChecking cache and size for {len(all_hashes)} candidate hashes via TorBox API...")
    cached_info = check_torbox_cached_details(list(all_hashes))
    print(f"TorBox verified {len(cached_info)} cached release(s).")

    os.makedirs("public/catalog/movie", exist_ok=True)
    manifest = {
        "id": "org.community.tamil.dubs.cached",
        "version": "1.4.0",
        "name": "Tamil Dubs",
        "description": "Curated Tamil releases verified cached on TorBox.",
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

    for cat in catalogs_data:
        cat_id = cat["id"]
        cat_name = cat["name"]
        is_indian = cat["is_indian"]
        verified_metas = []

        for item in cat["items"]:
            imdb_id = item["id"]
            streams = movie_streams_map.get(imdb_id, [])
            
            if is_indian:
                has_valid = any(is_valid_indian_stream(s, cached_info) for s in streams)
            else:
                has_valid = any(is_valid_hollywood_stream(s, cached_info) for s in streams)

            if has_valid:
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

        print(f"Catalog '{cat_name}' ({cat_id}): {len(verified_metas)} verified movies saved.")

if __name__ == "__main__":
    main()
