#!/usr/bin/env python3
"""
Restore OriginalTitle for milasha items modified on 2026-11-13
"""
import sqlite3
import os
import xml.etree.ElementTree as ET
import re
import json
import urllib.request
import ssl
import sys

JELLYFIN_URL = "https://jellyfine.olthon.com"
API_KEY = "9a2a1d5578344b0eaaaeb65fa9f2ff0f"
DB_PATH = "file:/var/lib/jellyfin/data/jellyfin.db?immutable=1"

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

headers = {
    'Accept': 'application/json',
    'Content-Type': 'application/json',
    'Authorization': f'MediaBrowser Token="{API_KEY}"',
    'X-Emby-Token': API_KEY,
}

ALL_UPDATE_FIELDS = (
    'Name,OriginalTitle,Overview,Genres,Tags,Studios,ProductionLocations,'
    'OfficialRating,CustomRating,CommunityRating,CriticRating,IndexNumber,ParentIndexNumber,'
    'PremiereDate,EndDate,ProductionYear,People,ProviderIds,PreferredMetadataLanguage,'
    'PreferredMetadataCountryCode,DateCreated,Status,AirDays,SeriesTimerId,Taglines,'
    'AspectRatio,DisplayOrder,RemoteTrailers,MediaStreams,SeasonUserData,DateLastRefreshed,DateLastSaved,RefreshState,'
    'ChannelImage,EnableMediaSourceDisplay,Width,Height,ExtraIds,LocalTrailerCount,IsHD,SpecialFeatureCount,'
    'CommunityRating,PremiereDate,ProductionYear'
)

def get_user_id():
    req = urllib.request.Request(f"{JELLYFIN_URL}/Users", headers=headers)
    with urllib.request.urlopen(req, context=ctx) as resp:
        users = json.loads(resp.read().decode('utf-8'))
        if users:
            return users[0]['Id']
    raise RuntimeError("No user found")

def collect_targets():
    conn = sqlite3.connect(DB_PATH, uri=True)
    c = conn.cursor()

    rows = c.execute("""
    SELECT Id, Name, Path, Type
    FROM BaseItems
    WHERE DateLastSaved >= '2026-11-12' AND DateLastSaved < '2026-11-16'
      AND (OriginalTitle IS NULL OR OriginalTitle = '')
      AND Type IN (
        'MediaBrowser.Controller.Entities.Movies.Movie',
        'MediaBrowser.Controller.Entities.TV.Episode',
        'MediaBrowser.Controller.Entities.Video'
      )
    ORDER BY Path
    """).fetchall()

    targets = {}

    for item_id, name, path, item_type in rows:
        if not path or not os.path.exists(path):
            continue
        folder = os.path.dirname(path)

        if '/Volumes/Anime/' in path:
            targets[item_id] = (name, name, "Anime episode name")
        elif '/Volumes/ERVIDEOS/ER/' in path:
            nfo_candidates = [f for f in os.listdir(folder) if f.endswith('.nfo') and not f.startswith('._')]
            scraper_nfo = [f for f in nfo_candidates if f.lower() not in ('movie.nfo', 'tvshow.nfo')]
            ot_found = None
            if scraper_nfo:
                try:
                    tree = ET.parse(os.path.join(folder, scraper_nfo[0]))
                    ot = tree.getroot().findtext('originaltitle')
                    if ot and ot.strip():
                        ot_found = ot.strip()
                except Exception:
                    pass
            if ot_found:
                targets[item_id] = (name, ot_found, f"NFO: {scraper_nfo[0]}")
            else:
                m = re.match(r'^[A-Z0-9_\-]+[\s　]+(.+)$', name)
                if m:
                    clean_title = m.group(1).strip()
                    targets[item_id] = (name, clean_title, "Stripped 品番 prefix")
        elif '/Volumes/NERVIDEOS/IV/' in path:
            m = re.match(r'^(.*?)(?:[\s　]+DMM)?$', name)
            clean_title = m.group(1).strip() if m else name
            targets[item_id] = (name, clean_title, "IV title cleaned")

    return targets

def main():
    dry_run = "--dry-run" in sys.argv
    print(f"=== milasha Nov 13 OriginalTitle Restoration ===")
    print(f"Server: {JELLYFIN_URL}")

    targets = collect_targets()
    print(f"Total targets identified: {len(targets)}")
    if dry_run:
        print("[DRY-RUN MODE] No changes will be saved to Jellyfin.")
    print("-" * 50)

    user_id = get_user_id()
    print(f"Using User ID: {user_id}")

    restored = 0
    errors = 0

    for idx, (item_id, (name, target_ot, reason)) in enumerate(targets.items(), 1):
        clean_id = item_id.replace("-", "").lower()
        get_url = f"{JELLYFIN_URL}/Users/{user_id}/Items/{clean_id}?fields={ALL_UPDATE_FIELDS}"
        req = urllib.request.Request(get_url, headers=headers)
        try:
            with urllib.request.urlopen(req, context=ctx) as resp:
                item_dto = json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            print(f"[{idx}/{len(targets)}] [ERROR] Failed to fetch item {item_id}: {e}")
            errors += 1
            continue

        current_ot = item_dto.get("OriginalTitle")
        if current_ot == target_ot:
            print(f"[{idx}/{len(targets)}] [SKIP] Already has OriginalTitle: '{name}'")
            continue

        print(f"[{idx}/{len(targets)}] [RESTORE] '{name}'")
        print(f"   Target OT : '{target_ot}' ({reason})")

        if dry_run:
            restored += 1
            continue

        item_dto["OriginalTitle"] = target_ot
        item_dto.pop("Trickplay", None)

        post_url = f"{JELLYFIN_URL}/Items/{clean_id}"
        post_req = urllib.request.Request(
            post_url,
            data=json.dumps(item_dto).encode('utf-8'),
            headers=headers,
            method="POST"
        )
        try:
            with urllib.request.urlopen(post_req, context=ctx) as post_resp:
                if post_resp.status in (200, 204):
                    print(f"   -> [SUCCESS]")
                    restored += 1
                else:
                    print(f"   -> [WARNING] Status: {post_resp.status}")
        except Exception as e:
            print(f"   -> [FAILED] {e}")
            errors += 1

    print("-" * 50)
    print(f"Done! Restored: {restored}, Errors: {errors}")

if __name__ == "__main__":
    main()
