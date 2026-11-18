#!/usr/bin/env python3
"""
Restore OriginalTitle for all affected items from jellyfin-backup.db
"""
import sqlite3
import json
import urllib.request
import os
import sys

def main():
    # Read .env
    env = {}
    env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip()

    url = env.get('JELLYFIN_URL')
    key = env.get('JELLYFIN_API_KEY')
    headers = {
        'X-Emby-Token': key,
        'Authorization': f'MediaBrowser Token="{key}"',
        'Content-Type': 'application/json'
    }

    all_fields = (
        'AirTime,CanDelete,CanDownload,ChannelInfo,Chapters,ChildCount,'
        'CumulativeRunTimeTicks,CustomRating,DateCreated,DateLastMediaAdded,DisplayPreferencesId,'
        'Etag,ExternalUrls,Genres,ItemCounts,MediaSourceCount,MediaSources,OriginalTitle,Overview,'
        'ParentId,Path,People,PlayAccess,ProductionLocations,ProviderIds,PrimaryImageAspectRatio,'
        'RecursiveItemCount,Settings,SeriesStudio,SortName,SpecialEpisodeNumbers,Studios,Taglines,Tags,'
        'RemoteTrailers,MediaStreams,SeasonUserData,DateLastRefreshed,DateLastSaved,RefreshState,'
        'ChannelImage,EnableMediaSourceDisplay,Width,Height,ExtraIds,LocalTrailerCount,IsHD,SpecialFeatureCount,'
        'CommunityRating,PremiereDate,ProductionYear'
    )

    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'var', 'jellyfin-backup.db')
    if not os.path.exists(db_path):
        print(f"Error: Database not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Load edited IDs from /tmp/edited_ids.json
    ids_file = '/tmp/edited_ids.json'
    with open(ids_file) as f:
        edited_ids = json.load(f)

    placeholders = ','.join(['?'] * len(edited_ids))
    cur.execute(f'''
        SELECT LOWER(REPLACE(Id, "-", "")), Name, OriginalTitle
        FROM BaseItems
        WHERE LOWER(REPLACE(Id, "-", "")) IN ({placeholders})
          AND OriginalTitle IS NOT NULL AND OriginalTitle != ""
    ''', edited_ids)
    backup_data = {row[0]: (row[1], row[2]) for row in cur.fetchall()}

    print(f"Total candidate items in backup: {len(backup_data)}")

    # Check live status in Jellyfin
    batch_ids = list(backup_data.keys())
    targets = []
    for i in range(0, len(batch_ids), 50):
        b = batch_ids[i:i+50]
        req = urllib.request.Request(f'{url}/Items?ids={",".join(b)}&fields=OriginalTitle,Name', headers=headers)
        with urllib.request.urlopen(req) as resp:
            d = json.loads(resp.read().decode('utf-8'))
            for item in d['Items']:
                if not item.get('OriginalTitle') and item['Id'] in backup_data:
                    targets.append((item['Id'], item.get('Name'), backup_data[item['Id']][1]))

    print(f"Items currently needing OriginalTitle restore: {len(targets)}")

    restored_count = 0
    error_count = 0

    for idx, (iid, name, orig) in enumerate(targets, 1):
        try:
            fetch_req = urllib.request.Request(f'{url}/Items?ids={iid}&fields={all_fields}', headers=headers)
            with urllib.request.urlopen(fetch_req) as resp:
                dto = json.loads(resp.read().decode('utf-8'))['Items'][0]

            dto['OriginalTitle'] = orig
            dto.pop('Trickplay', None)

            post_req = urllib.request.Request(f'{url}/Items/{iid}', data=json.dumps(dto).encode('utf-8'), headers=headers, method='POST')
            with urllib.request.urlopen(post_req) as resp:
                if resp.status in (200, 204):
                    restored_count += 1
                    print(f"[{idx}/{len(targets)}] Restored: {name[:35]} -> {orig[:35]}")
                else:
                    print(f"[{idx}/{len(targets)}] Unexpected status {resp.status} for {name}")
                    error_count += 1
        except Exception as e:
            print(f"[{idx}/{len(targets)}] Error restoring {name}: {e}")
            error_count += 1

    print(f"\nFinished! Restored: {restored_count}, Errors: {error_count}")

if __name__ == '__main__':
    main()
