import os
import re
import urllib.parse
import base64
import asyncio
import random
import time
from typing import Optional, List, Dict, Any, Set
from contextlib import asynccontextmanager

import httpx
from pydantic import BaseModel
from fastapi import FastAPI, Query, HTTPException, Response
from fastapi.responses import HTMLResponse, StreamingResponse

JELLYFIN_URL = os.getenv("JELLYFIN_URL", "http://localhost:8096").rstrip("/")
JELLYFIN_API_KEY = os.getenv("JELLYFIN_API_KEY", "").strip()

# HTTPクライアントの寿命管理
http_client: Optional[httpx.AsyncClient] = None

# タグキャッシュ管理
cached_tags: List[str] = []
last_tags_fetch: float = 0.0

@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    http_client = httpx.AsyncClient(timeout=15.0)
    yield
    await http_client.aclose()

app = FastAPI(title="jelly2iina", version="1.0.0", lifespan=lifespan)

def get_auth_headers() -> Dict[str, str]:
    headers = {
        "Accept": "application/json",
    }
    if JELLYFIN_API_KEY:
        headers["Authorization"] = f'MediaBrowser Token="{JELLYFIN_API_KEY}"'
        headers["X-Emby-Token"] = JELLYFIN_API_KEY
    return headers

def format_runtime(ticks: Optional[int]) -> str:
    if not ticks:
        return ""
    total_seconds = ticks // 10_000_000
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    if hours > 0:
        return f"{hours}時間{minutes}分"
    return f"{minutes}分"

def to_url_safe_b64(val: str) -> str:
    return base64.urlsafe_b64encode(val.encode("utf-8")).decode("utf-8").rstrip("=")

def is_windows_path(p: str) -> bool:
    if not p:
        return False
    if len(p) >= 2 and p[1] == ":" and (len(p) == 2 or p[2] in ("\\", "/")):
        return True
    if p.startswith("\\\\"):
        return True
    return False

def build_stream_url(item_id: str) -> str:
    if not JELLYFIN_URL or not JELLYFIN_API_KEY:
        return ""
    return f"{JELLYFIN_URL}/Videos/{item_id}/stream?static=true&api_key={JELLYFIN_API_KEY}"

def build_iina_file_url(path: str) -> str:
    if not path:
        return ""
    norm = path.replace("\\", "/")
    if not norm.startswith("/"):
        norm = "/" + norm
    file_url = f"file://{norm}"
    encoded = urllib.parse.quote(file_url, safe="/:@=+$,?#")
    return f"iina://weblink?url={encoded}"

def build_iina_stream_url(stream_url: str) -> str:
    if not stream_url:
        return ""
    encoded = urllib.parse.quote(stream_url, safe="/:@=+$,?#")
    return f"iina://weblink?url={encoded}"

def build_mpv_file_url(path: str) -> str:
    if not path:
        return ""
    return f"mpv-handler://play/{to_url_safe_b64(path)}"

def build_mpv_stream_url(stream_url: str) -> str:
    if not stream_url:
        return ""
    return f"mpv-handler://play/{to_url_safe_b64(stream_url)}"

# 互換用ショートカット
def build_iina_url(path: str) -> str:
    return build_iina_file_url(path)

def build_mpv_url(item_id: str) -> str:
    return build_mpv_stream_url(build_stream_url(item_id))

# サーバーOS自動判定管理 ("linux" または "windows")
detected_server_os: str = "linux"
last_server_os_fetch: float = 0.0

# ユーザーID管理 (環境変数指定または自動検出)
JELLYFIN_USER_ID_ENV = os.getenv("JELLYFIN_USER_ID", "").strip()
cached_user_id: Optional[str] = None
last_user_id_fetch: float = 0.0

async def get_current_user_id() -> Optional[str]:
    global cached_user_id, last_user_id_fetch
    if JELLYFIN_USER_ID_ENV:
        return JELLYFIN_USER_ID_ENV
    now = time.time()
    if cached_user_id and (now - last_user_id_fetch < 600):
        return cached_user_id

    headers = get_auth_headers()
    try:
        res = await http_client.get(f"{JELLYFIN_URL}/Users", headers=headers)
        if res.status_code == 200:
            users = res.json()
            for u in users:
                if u.get("Policy", {}).get("IsAdministrator"):
                    cached_user_id = u.get("Id")
                    last_user_id_fetch = now
                    return cached_user_id
            if users:
                cached_user_id = users[0].get("Id")
                last_user_id_fetch = now
                return cached_user_id
    except Exception:
        pass

    return None

async def detect_server_os() -> str:
    global detected_server_os, last_server_os_fetch
    now = time.time()
    if detected_server_os and (now - last_server_os_fetch < 600):
        return detected_server_os

    headers = get_auth_headers()
    try:
        res = await http_client.get(f"{JELLYFIN_URL}/System/Info", headers=headers)
        if res.status_code == 200:
            data = res.json()
            data_path = data.get("ProgramDataPath", "") or data.get("LogPath", "") or data.get("WebPath", "")
            if is_windows_path(data_path) or ":\\" in data_path:
                detected_server_os = "windows"
                last_server_os_fetch = now
                return detected_server_os
            elif data_path.startswith("/"):
                detected_server_os = "linux"
                last_server_os_fetch = now
                return detected_server_os
    except Exception:
        pass

    last_server_os_fetch = now
    return detected_server_os

async def get_all_tags() -> List[str]:
    global cached_tags, last_tags_fetch
    now = time.time()
    if cached_tags and (now - last_tags_fetch < 600):
        return cached_tags
    
    headers = get_auth_headers()
    try:
        res = await http_client.get(
            f"{JELLYFIN_URL}/Items/Filters2",
            params={"recursive": "true", "includeItemTypes": "Movie,Episode,Video"},
            headers=headers
        )
        if res.status_code == 200:
            data = res.json()
            tags = data.get("Tags", [])
            if tags:
                cached_tags = tags
                last_tags_fetch = now
                return cached_tags
    except Exception:
        pass
    return cached_tags

@app.get("/health")
async def health_check():
    srv_os = await detect_server_os()
    status = {
        "status": "ok",
        "jellyfin_url": JELLYFIN_URL,
        "api_key_configured": bool(JELLYFIN_API_KEY),
        "jellyfin_connected": False,
        "server_os": srv_os,
    }
    try:
        res = await http_client.get(f"{JELLYFIN_URL}/System/Info/Public")
        if res.status_code == 200:
            status["jellyfin_connected"] = True
            info = res.json()
            status["server_name"] = info.get("ServerName", "Unknown")
            status["version"] = info.get("Version", "Unknown")
    except Exception as e:
        status["jellyfin_error"] = str(e)
    return status

@app.get("/api/image/{item_id}")
async def get_item_image(item_id: str, max_width: int = 400):
    url = f"{JELLYFIN_URL}/Items/{item_id}/Images/Primary"
    params = {"maxWidth": max_width, "quality": 85}
    headers = get_auth_headers()
    try:
        req = http_client.build_request("GET", url, params=params, headers=headers)
        res = await http_client.send(req, stream=True)
        if res.status_code != 200:
            await res.aclose()
            return Response(content=SVG_PLACEHOLDER, media_type="image/svg+xml")
        
        content_type = res.headers.get("content-type", "image/jpeg")
        return StreamingResponse(
            res.aiter_bytes(),
            media_type=content_type,
            headers={"Cache-Control": "public, max-age=86400"},
            background=res.aclose
        )
    except Exception:
        return Response(content=SVG_PLACEHOLDER, media_type="image/svg+xml")

SORT_CONFIG = {
    "user_rating_desc": ("Rating", "Descending"),
    "user_rating_asc": ("Rating", "Ascending"),
    "premiere_desc": ("PremiereDate,DateCreated", "Descending"),
    "premiere_asc": ("PremiereDate,DateCreated", "Ascending"),
    "date_desc": ("DateCreated", "Descending"),
    "date_asc": ("DateCreated", "Ascending"),
    "rating_desc": ("CommunityRating", "Descending"),
    "rating_asc": ("CommunityRating", "Ascending"),
    "filename_asc": ("SortName", "Ascending"),
    "filename_desc": ("SortName", "Descending"),
    "name_asc": ("SortName", "Ascending"),
    "name_desc": ("SortName", "Descending"),
    "random": ("Random", "Ascending"),
}

@app.get("/api/search")
async def search_items(
    q: Optional[str] = Query(None, description="Search term"),
    sort: str = Query("premiere_desc", description="Sort option"),
    limit: int = Query(40, ge=1, le=100),
    startIndex: int = Query(0, ge=0)
):
    if not JELLYFIN_API_KEY:
        raise HTTPException(
            status_code=401,
            detail="JELLYFIN_API_KEY is not configured. Please set it in .env file."
        )

    sort_field, sort_order = SORT_CONFIG.get(sort, ("PremiereDate,DateCreated", "Descending"))
    headers = get_auth_headers()
    server_os = await detect_server_os()
    user_id = await get_current_user_id()

    base_fields = "Path,Overview,MediaSources,PrimaryImageAspectRatio,DateCreated,PremiereDate,ProductionYear,RunTimeTicks,ParentIndexNumber,IndexNumber,SeriesName,SeriesId,CommunityRating,Tags,Genres,Studios,People,UserData"

    try:
        if q and q.strip():
            search_term = q.strip()
            search_lower = search_term.lower()
            base_params = {
                "recursive": "true",
                "includeItemTypes": "Movie,Episode,Video",
                "fields": base_fields,
            }
            if user_id:
                base_params["userId"] = user_id

            tasks = [
                # 1. 通常のアイテム検索 (上限を大きく拡大)
                http_client.get(
                    f"{JELLYFIN_URL}/Items",
                    params={**base_params, "searchTerm": search_term, "limit": 2000},
                    headers=headers
                ),
                # 2. シリーズ (Series) 自体の検索
                http_client.get(
                    f"{JELLYFIN_URL}/Items",
                    params={"recursive": "true", "includeItemTypes": "Series", "searchTerm": search_term, "limit": 50},
                    headers=headers
                ),
                # 3. 出演者検索
                http_client.get(
                    f"{JELLYFIN_URL}/Persons",
                    params={"searchTerm": search_term, "limit": 30},
                    headers=headers
                ),
                # 4. ジャンル検索
                http_client.get(
                    f"{JELLYFIN_URL}/Genres",
                    params={"searchTerm": search_term, "limit": 20},
                    headers=headers
                ),
                # 5. スタジオ検索
                http_client.get(
                    f"{JELLYFIN_URL}/Studios",
                    params={"searchTerm": search_term, "limit": 30},
                    headers=headers
                ),
                # 6. タグ一覧取得
                get_all_tags(),
            ]

            r1, r_series, r_person, r_genre, r_studio, all_tags = await asyncio.gather(*tasks, return_exceptions=True)

            items_map: Dict[str, Any] = {}
            if not isinstance(r1, Exception) and r1.status_code == 200:
                for it in r1.json().get("Items", []):
                    items_map[it.get("Id")] = it

            series_ids: List[str] = []
            if not isinstance(r_series, Exception) and r_series.status_code == 200:
                for s in r_series.json().get("Items", []):
                    if s.get("Id"):
                        series_ids.append(s.get("Id"))

            person_ids: List[str] = []
            if not isinstance(r_person, Exception) and r_person.status_code == 200:
                for p in r_person.json().get("Items", []):
                    if p.get("Id"):
                        person_ids.append(p.get("Id"))

            genre_ids: List[str] = []
            if not isinstance(r_genre, Exception) and r_genre.status_code == 200:
                for g in r_genre.json().get("Items", []):
                    if g.get("Id"):
                        genre_ids.append(g.get("Id"))

            studio_ids: List[str] = []
            if not isinstance(r_studio, Exception) and r_studio.status_code == 200:
                for s in r_studio.json().get("Items", []):
                    if s.get("Id"):
                        studio_ids.append(s.get("Id"))

            matched_tags: List[str] = []
            if isinstance(all_tags, list):
                for tag in all_tags:
                    if search_lower in tag.lower() or tag.lower() in search_lower:
                        matched_tags.append(tag)

            extra_tasks = []
            # シリーズエピソードは話数上限を設けない（全エピソード漏れなく取得）
            episode_params = {"fields": base_fields}
            if user_id:
                episode_params["userId"] = user_id

            for sid in series_ids:
                extra_tasks.append(
                    http_client.get(
                        f"{JELLYFIN_URL}/Shows/{sid}/Episodes",
                        params=episode_params,
                        headers=headers
                    )
                )

            if person_ids:
                extra_tasks.append(
                    http_client.get(
                        f"{JELLYFIN_URL}/Items",
                        params={**base_params, "personIds": ",".join(person_ids), "limit": 2000},
                        headers=headers
                    )
                )
            if genre_ids:
                extra_tasks.append(
                    http_client.get(
                        f"{JELLYFIN_URL}/Items",
                        params={**base_params, "genreIds": ",".join(genre_ids), "limit": 2000},
                        headers=headers
                    )
                )
            if studio_ids:
                extra_tasks.append(
                    http_client.get(
                        f"{JELLYFIN_URL}/Items",
                        params={**base_params, "studioIds": ",".join(studio_ids), "limit": 2000},
                        headers=headers
                    )
                )
            if matched_tags:
                extra_tasks.append(
                    http_client.get(
                        f"{JELLYFIN_URL}/Items",
                        params={**base_params, "tags": "|".join(matched_tags[:15]), "limit": 2000},
                        headers=headers
                    )
                )

            if extra_tasks:
                extra_res = await asyncio.gather(*extra_tasks, return_exceptions=True)
                for res in extra_res:
                    if not isinstance(res, Exception) and res.status_code == 200:
                        for it in res.json().get("Items", []):
                            if it.get("Type") in ("Episode", "Movie", "Video"):
                                items_map[it.get("Id")] = it

            all_items = list(items_map.values())

            def natural_sort_key(s: str):
                return [int(text) if text.isdigit() else text.lower() for text in re.split(r'(\d+)', s or "")]

            def get_name_sort_key(x):
                # エピソードでも単体動画でも、動画のタイトル名そのもので自然順ソート
                return natural_sort_key(x.get("Name") or x.get("SortName") or "")

            def get_filename_sort_key(x):
                path = x.get("Path", "")
                if not path:
                    media_sources = x.get("MediaSources", [])
                    if media_sources and "Path" in media_sources[0]:
                        path = media_sources[0]["Path"]
                fname = path.replace("\\", "/").split("/")[-1] if path else ""
                if not fname:
                    fname = x.get("Name") or x.get("SortName") or ""
                return natural_sort_key(fname)

            if sort == "user_rating_desc":
                all_items.sort(
                    key=lambda x: (
                        (x.get("UserData") or {}).get("Rating")
                        if (x.get("UserData") or {}).get("Rating") is not None
                        else -1.0
                    ),
                    reverse=True
                )
            elif sort == "user_rating_asc":
                all_items.sort(
                    key=lambda x: (
                        (x.get("UserData") or {}).get("Rating")
                        if (x.get("UserData") or {}).get("Rating") is not None
                        else 999.0
                    )
                )
            elif sort == "premiere_desc":
                all_items.sort(
                    key=lambda x: (
                        1 if x.get("PremiereDate") else 0,
                        x.get("PremiereDate") or "",
                        x.get("DateCreated") or ""
                    ),
                    reverse=True
                )
            elif sort == "premiere_asc":
                all_items.sort(
                    key=lambda x: (
                        0 if x.get("PremiereDate") else 1,
                        x.get("PremiereDate") or "9999",
                        x.get("DateCreated") or ""
                    )
                )
            elif sort == "date_desc":
                all_items.sort(key=lambda x: x.get("DateCreated") or "", reverse=True)
            elif sort == "date_asc":
                all_items.sort(key=lambda x: x.get("DateCreated") or "")
            elif sort == "rating_desc":
                all_items.sort(key=lambda x: (x.get("CommunityRating") if x.get("CommunityRating") is not None else -1.0), reverse=True)
            elif sort == "rating_asc":
                all_items.sort(key=lambda x: (x.get("CommunityRating") if x.get("CommunityRating") is not None else 999.0))
            elif sort == "filename_asc":
                all_items.sort(key=get_filename_sort_key)
            elif sort == "filename_desc":
                all_items.sort(key=get_filename_sort_key, reverse=True)
            elif sort == "name_asc":
                all_items.sort(key=get_name_sort_key)
            elif sort == "name_desc":
                all_items.sort(key=get_name_sort_key, reverse=True)
            elif sort == "random":
                rng = random.Random(search_term)
                rng.shuffle(all_items)

            total_record_count = len(all_items)
            items = all_items[startIndex: startIndex + limit]

        else:
            params: Dict[str, Any] = {
                "recursive": "true",
                "includeItemTypes": "Movie,Episode,Video",
                "fields": base_fields,
                "sortBy": sort_field,
                "sortOrder": sort_order,
                "limit": limit,
                "startIndex": startIndex,
            }
            if user_id:
                params["userId"] = user_id
            res = await http_client.get(f"{JELLYFIN_URL}/Items", params=params, headers=headers)
            if res.status_code == 401:
                raise HTTPException(status_code=401, detail="Invalid Jellyfin API Key")
            if res.status_code != 200:
                raise HTTPException(status_code=res.status_code, detail=f"Jellyfin API error: {res.text}")

            data = res.json()
            items = data.get("Items", [])
            total_record_count = data.get("TotalRecordCount", len(items))

        results = []
        for it in items:
            path = it.get("Path", "")
            if not path:
                media_sources = it.get("MediaSources", [])
                if media_sources and "Path" in media_sources[0]:
                    path = media_sources[0]["Path"]

            item_id = it.get("Id", "")
            name = it.get("Name", "Unknown")
            series_name = it.get("SeriesName", "")
            runtime_ticks = it.get("RunTimeTicks")
            has_primary_image = bool(it.get("ImageTags", {}).get("Primary"))
            rating = it.get("CommunityRating")
            user_data = it.get("UserData") or {}
            user_rating = user_data.get("Rating")
            tags = it.get("Tags", []) or []
            studios = [s.get("Name") for s in it.get("Studios", []) if s.get("Name")]

            people = it.get("People", []) or []
            actors = []
            for p in people:
                p_type = p.get("Type", "")
                p_role = p.get("Role", "")
                if p_type == "Actor" or (p_role and p_role.lower() == "actor"):
                    p_name = p.get("Name")
                    if p_name and p_name not in actors:
                        actors.append(p_name)

            parent_idx = it.get("ParentIndexNumber")
            idx = it.get("IndexNumber")
            episode_badge = ""
            if parent_idx is not None and idx is not None:
                episode_badge = f"S{parent_idx}:E{idx}"
            elif idx is not None:
                episode_badge = f"E{idx}"

            srv_os = "windows" if is_windows_path(path) else server_os

            stream_url = build_stream_url(item_id)
            iina_file = build_iina_file_url(path)
            iina_stream = build_iina_stream_url(stream_url)
            mpv_file = build_mpv_file_url(path)
            mpv_stream = build_mpv_stream_url(stream_url)

            results.append({
                "id": item_id,
                "name": name,
                "series_name": series_name,
                "episode_badge": episode_badge,
                "type": it.get("Type", "Video"),
                "path": path,
                "server_os": srv_os,
                "stream_url": stream_url,
                "iina_file_url": iina_file,
                "iina_stream_url": iina_stream,
                "mpv_file_url": mpv_file,
                "mpv_stream_url": mpv_stream,
                "iina_url": iina_file,
                "mpv_url": mpv_stream,
                "has_image": has_primary_image,
                "runtime": format_runtime(runtime_ticks),
                "rating": round(rating, 1) if rating is not None else None,
                "user_rating": round(user_rating, 1) if user_rating is not None else None,
                "premiere_date": it.get("PremiereDate", "")[:10] if it.get("PremiereDate") else "",
                "tags": tags[:12],
                "actors": actors[:8],
                "studios": studios[:2],
                "year": it.get("ProductionYear"),
                "overview": it.get("Overview", ""),
            })

        return {
            "total": total_record_count,
            "items": results,
            "startIndex": startIndex,
            "limit": limit,
        }
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Failed to connect to Jellyfin server: {str(e)}")

class MetadataUpdateRequest(BaseModel):
    community_rating: Optional[float] = None
    clear_rating: Optional[bool] = False
    user_rating: Optional[float] = None
    clear_user_rating: Optional[bool] = False
    name: Optional[str] = None
    overview: Optional[str] = None
    tags: Optional[List[str]] = None
    premiere_date: Optional[str] = None
    clear_premiere_date: Optional[bool] = False

@app.post("/api/items/{item_id}/metadata")
async def update_item_metadata(item_id: str, payload: MetadataUpdateRequest):
    if not JELLYFIN_API_KEY:
        raise HTTPException(status_code=401, detail="API Key not configured")

    headers = get_auth_headers()
    user_id = await get_current_user_id()

    # 1. 現在のアイテムオブジェクトを取得
    try:
        fetch_params = {
            "ids": item_id,
            "fields": "CommunityRating,Tags,Overview,Genres,Studios,PremiereDate,ProductionYear,UserData",
        }
        if user_id:
            fetch_params["userId"] = user_id

        res = await http_client.get(
            f"{JELLYFIN_URL}/Items",
            params=fetch_params,
            headers=headers
        )
        if res.status_code != 200:
            raise HTTPException(status_code=res.status_code, detail="Failed to fetch item from Jellyfin")
        data = res.json()
        items = data.get("Items", [])
        if not items:
            raise HTTPException(status_code=404, detail="Item not found")
        item_dto = items[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # 2. フィールドの更新
    if payload.clear_rating:
        item_dto["CommunityRating"] = None
    elif payload.community_rating is not None:
        item_dto["CommunityRating"] = round(payload.community_rating, 1)

    if payload.name is not None and payload.name.strip():
        item_dto["Name"] = payload.name.strip()

    if payload.overview is not None:
        item_dto["Overview"] = payload.overview.strip()

    if payload.tags is not None:
        # 重複・空白を排除したタグリスト
        cleaned_tags = []
        for t in payload.tags:
            t = t.strip()
            if t and t not in cleaned_tags:
                cleaned_tags.append(t)
        item_dto["Tags"] = cleaned_tags

    if payload.clear_premiere_date:
        item_dto["PremiereDate"] = None
        item_dto["ProductionYear"] = None
    elif payload.premiere_date is not None:
        val = payload.premiere_date.strip()
        if val:
            try:
                year = int(val[:4])
                item_dto["PremiereDate"] = f"{val}T00:00:00.0000000Z"
                item_dto["ProductionYear"] = year
            except Exception:
                pass
        else:
            item_dto["PremiereDate"] = None
            item_dto["ProductionYear"] = None

    # 3. Jellyfin へメタデータを送信 (Item更新)
    try:
        post_headers = {**headers, "Content-Type": "application/json"}
        update_res = await http_client.post(
            f"{JELLYFIN_URL}/Items/{item_id}",
            json=item_dto,
            headers=post_headers
        )
        if update_res.status_code not in (200, 204):
            raise HTTPException(status_code=update_res.status_code, detail=f"Jellyfin update error: {update_res.text}")

        # 4. 個人評価 (UserData.Rating) の更新
        updated_user_rating = (item_dto.get("UserData") or {}).get("Rating")
        if user_id:
            if payload.clear_user_rating:
                try:
                    await http_client.delete(
                        f"{JELLYFIN_URL}/UserItems/{item_id}/Rating",
                        params={"userId": user_id},
                        headers=headers
                    )
                    updated_user_rating = None
                except Exception:
                    pass
            elif payload.user_rating is not None:
                val = round(payload.user_rating, 1)
                try:
                    user_key = (item_dto.get("UserData") or {}).get("Key") or item_id
                    await http_client.post(
                        f"{JELLYFIN_URL}/UserItems/{item_id}/UserData",
                        params={"userId": user_id},
                        json={"Rating": val, "Key": user_key},
                        headers=post_headers
                    )
                    updated_user_rating = val
                except Exception:
                    pass

        premiere_date_str = item_dto.get("PremiereDate", "")
        if premiere_date_str:
            premiere_date_str = str(premiere_date_str)[:10]
        else:
            premiere_date_str = ""

        return {
            "status": "ok",
            "id": item_id,
            "rating": item_dto.get("CommunityRating"),
            "user_rating": updated_user_rating,
            "name": item_dto.get("Name"),
            "tags": item_dto.get("Tags", []),
            "overview": item_dto.get("Overview", ""),
            "premiere_date": premiere_date_str,
            "year": item_dto.get("ProductionYear"),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update metadata: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update metadata: {str(e)}")

SVG_PLACEHOLDER = """<svg xmlns="http://www.w3.org/2000/svg" width="400" height="400" viewBox="0 0 400 400" fill="#1e222d">
  <rect width="400" height="400" fill="#181b24"/>
  <circle cx="200" cy="180" r="40" fill="#2b3142"/>
  <polygon points="192,165 215,180 192,195" fill="#5865f2"/>
  <text x="200" y="250" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="16" fill="#6c7993" text-anchor="middle">No Preview</text>
</svg>"""

HTML_CONTENT = r"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>jelly2iina - Jellyfin to IINA Launcher</title>
  <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🎬</text></svg>">
  <style>
    :root {
      --bg-main: #0e1117;
      --bg-card: #161b26;
      --bg-card-hover: #1d2433;
      --border-color: #273043;
      --primary: #6366f1;
      --primary-hover: #4f46e5;
      --primary-light: rgba(99, 102, 241, 0.15);
      --text-main: #f3f4f6;
      --text-muted: #9ca3af;
      --text-sub: #6b7280;
      --accent-green: #10b981;
      --accent-red: #ef4444;
      --accent-star: #fbbf24;
      --radius: 12px;
      --transition: all 0.2s ease;
    }

    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }

    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background-color: var(--bg-main);
      color: var(--text-main);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
    }

    header {
      background: rgba(22, 27, 38, 0.88);
      backdrop-filter: blur(12px);
      position: sticky;
      top: 0;
      z-index: 100;
      border-bottom: 1px solid var(--border-color);
      padding: 1rem 1.5rem;
    }

    .header-content {
      max-width: 1400px;
      margin: 0 auto;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 1rem;
      flex-wrap: wrap;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      text-decoration: none;
      color: var(--text-main);
      cursor: pointer;
      user-select: none;
      transition: opacity var(--transition), transform var(--transition);
      border-radius: 8px;
      padding: 0.25rem 0.5rem;
      margin: -0.25rem -0.5rem;
    }

    .brand:hover {
      opacity: 0.85;
      background: rgba(255, 255, 255, 0.04);
    }

    .brand:active {
      transform: scale(0.97);
    }

    .brand-icon {
      font-size: 1.8rem;
      line-height: 1;
    }

    .brand-title {
      font-size: 1.25rem;
      font-weight: 700;
      background: linear-gradient(135deg, #a5b4fc, #6366f1);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }

    .brand-subtitle {
      font-size: 0.8rem;
      color: var(--text-sub);
    }

    .controls-wrapper {
      display: flex;
      align-items: center;
      gap: 0.6rem;
      flex: 1;
      max-width: 1040px;
    }

    .search-bar {
      flex: 1;
      min-width: 240px;
      position: relative;
      display: flex;
      align-items: center;
    }

    .search-input {
      width: 100%;
      background: #0e1117;
      border: 1px solid var(--border-color);
      color: var(--text-main);
      padding: 0.6rem 0.9rem 0.6rem 2.3rem;
      border-radius: 9999px;
      font-size: 0.88rem;
      outline: none;
      transition: var(--transition);
    }

    .search-input:focus {
      border-color: var(--primary);
      box-shadow: 0 0 0 3px var(--primary-light);
    }

    .search-icon {
      position: absolute;
      left: 0.85rem;
      font-size: 0.88rem;
      color: var(--text-sub);
      pointer-events: none;
    }

    .clear-btn {
      position: absolute;
      right: 0.75rem;
      background: none;
      border: none;
      color: var(--text-sub);
      cursor: pointer;
      font-size: 1rem;
      padding: 0.25rem;
      display: none;
    }

    .sort-select {
      background-color: #0e1117;
      border: 1px solid var(--border-color);
      color: var(--text-main);
      padding: 0.6rem 2.2rem 0.6rem 0.85rem;
      border-radius: 9999px;
      font-size: 0.82rem;
      outline: none;
      cursor: pointer;
      transition: var(--transition);
      white-space: nowrap;
      appearance: none;
      -webkit-appearance: none;
      -moz-appearance: none;
      background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='%239ca3af' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");
      background-repeat: no-repeat;
      background-position: right 0.8rem center;
    }

    .sort-select:hover {
      border-color: #3b455b;
      background-color: #121620;
    }

    .sort-select:focus {
      border-color: var(--primary);
      box-shadow: 0 0 0 3px var(--primary-light);
    }

    .player-select {
      background-color: #0e1117;
      border: 1px solid var(--border-color);
      color: var(--text-main);
      padding: 0.6rem 2rem 0.6rem 0.85rem;
      border-radius: 9999px;
      font-size: 0.82rem;
      outline: none;
      cursor: pointer;
      transition: var(--transition);
      white-space: nowrap;
      appearance: none;
      -webkit-appearance: none;
      -moz-appearance: none;
      background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='%239ca3af' stroke-width='2.5' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath d='m6 9 6 6 6-6'/%3E%3C/svg%3E");
      background-repeat: no-repeat;
      background-position: right 0.75rem center;
    }

    .player-select:hover {
      border-color: #3b455b;
      background-color: #121620;
    }

    .player-select:focus {
      border-color: var(--primary);
      box-shadow: 0 0 0 3px var(--primary-light);
    }

    .refresh-btn {
      background-color: #0e1117;
      border: 1px solid var(--border-color);
      color: var(--text-muted);
      width: 38px;
      height: 38px;
      border-radius: 9999px;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      outline: none;
      transition: var(--transition);
      flex-shrink: 0;
    }

    .refresh-btn:hover {
      border-color: var(--primary);
      background-color: #161b26;
      color: #ffffff;
      box-shadow: 0 0 10px rgba(99, 102, 241, 0.25);
    }

    .refresh-btn:active {
      transform: scale(0.92);
    }

    .refresh-icon {
      transition: transform var(--transition);
    }

    .refresh-btn.spinning .refresh-icon {
      animation: spin 0.6s cubic-bezier(0.4, 0, 0.2, 1) infinite;
    }

    .server-status {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      font-size: 0.85rem;
      background: #0e1117;
      padding: 0.4rem 0.8rem;
      border-radius: 9999px;
      border: 1px solid var(--border-color);
      text-decoration: none;
      color: var(--text-main);
      cursor: pointer;
      transition: var(--transition);
      white-space: nowrap;
    }

    .server-status:hover {
      border-color: var(--primary);
      background: #181d2a;
      color: #ffffff;
      box-shadow: 0 0 10px rgba(99, 102, 241, 0.25);
    }

    .status-ext-icon {
      font-size: 0.75rem;
      opacity: 0.5;
      transition: transform var(--transition), opacity var(--transition);
    }

    .server-status:hover .status-ext-icon {
      opacity: 1;
      transform: translate(1px, -1px);
    }

    .status-dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background-color: var(--text-sub);
    }

    .status-dot.online {
      background-color: var(--accent-green);
      box-shadow: 0 0 8px var(--accent-green);
    }

    .status-dot.offline {
      background-color: var(--accent-red);
      box-shadow: 0 0 8px var(--accent-red);
    }

    main {
      flex: 1;
      max-width: 1400px;
      width: 100%;
      margin: 0 auto;
      padding: 1.5rem;
    }

    .notification-banner {
      background: rgba(239, 68, 68, 0.1);
      border: 1px solid var(--accent-red);
      border-radius: var(--radius);
      padding: 1rem 1.25rem;
      margin-bottom: 1.5rem;
      display: none;
      color: #fca5a5;
    }

    .notification-banner.active {
      display: block;
    }

    .notification-title {
      font-weight: 600;
      margin-bottom: 0.25rem;
    }

    .notification-desc {
      font-size: 0.9rem;
      color: var(--text-muted);
    }

    .notification-desc code {
      background: #000;
      padding: 0.2rem 0.4rem;
      border-radius: 4px;
      color: #cbd5e1;
    }

    .meta-bar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1.25rem;
      font-size: 0.9rem;
      color: var(--text-muted);
    }

    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      gap: 1.5rem;
    }

    .card {
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: var(--radius);
      overflow: hidden;
      display: flex;
      flex-direction: column;
      transition: var(--transition);
      position: relative;
    }

    .card:hover {
      background: var(--bg-card-hover);
      transform: translateY(-3px);
      box-shadow: 0 10px 20px -5px rgba(0, 0, 0, 0.5);
      border-color: #3b455b;
    }

    .card-thumb {
      width: 100%;
      aspect-ratio: 1 / 1;
      background: #0f131a;
      position: relative;
      overflow: hidden;
      display: flex;
      align-items: center;
      justify-content: center;
    }

    .card-thumb img {
      max-width: 100%;
      max-height: 100%;
      width: auto;
      height: auto;
      object-fit: contain;
      display: block;
    }

    .card-badge {
      position: absolute;
      bottom: 0.5rem;
      right: 0.5rem;
      background: rgba(0, 0, 0, 0.75);
      color: #fff;
      font-size: 0.75rem;
      padding: 0.2rem 0.5rem;
      border-radius: 4px;
      backdrop-filter: blur(4px);
    }

    .card-rating {
      position: absolute;
      top: 0.5rem;
      left: 0.5rem;
      background: rgba(10, 14, 23, 0.82);
      border: 1px solid rgba(255, 255, 255, 0.12);
      color: #fff;
      font-size: 0.72rem;
      font-weight: 700;
      padding: 0.22rem 0.45rem;
      border-radius: 6px;
      backdrop-filter: blur(6px);
      display: flex;
      align-items: center;
      gap: 0.35rem;
      cursor: pointer;
      transition: var(--transition);
      letter-spacing: 0.02em;
    }

    .card-rating:hover {
      background: rgba(10, 14, 23, 0.96);
      border-color: rgba(244, 114, 182, 0.5);
      transform: scale(1.04);
      box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5), 0 0 10px rgba(244, 114, 182, 0.25);
    }

    .card-edit-btn {
      position: absolute;
      top: 0.5rem;
      right: 0.5rem;
      background: rgba(0, 0, 0, 0.7);
      color: var(--text-muted);
      border: 1px solid rgba(255, 255, 255, 0.1);
      width: 28px;
      height: 28px;
      border-radius: 50%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 0.8rem;
      cursor: pointer;
      transition: var(--transition);
      opacity: 0.6;
    }

    .card:hover .card-edit-btn {
      opacity: 1;
    }

    .card-edit-btn:hover {
      background: var(--primary);
      color: white;
      border-color: var(--primary);
      transform: scale(1.1);
    }

    .card-body {
      padding: 1rem;
      display: flex;
      flex-direction: column;
      flex: 1;
    }

    .card-meta-line {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 0.5rem;
      margin-bottom: 0.3rem;
      font-size: 0.8rem;
    }

    .card-series {
      color: #818cf8;
      font-weight: 600;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      cursor: pointer;
    }

    .card-series:hover {
      text-decoration: underline;
    }

    .card-studio {
      color: var(--text-sub);
      font-size: 0.75rem;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .card-year {
      color: var(--text-sub);
      font-size: 0.75rem;
      background: rgba(255, 255, 255, 0.06);
      padding: 0.1rem 0.4rem;
      border-radius: 4px;
      font-weight: 500;
      white-space: nowrap;
    }

    .card-year.unconfigured {
      color: #f87171;
      background: rgba(239, 68, 68, 0.12);
      border: 1px dashed rgba(239, 68, 68, 0.35);
      cursor: pointer;
      font-size: 0.7rem;
      transition: var(--transition);
    }

    .card-year.unconfigured:hover {
      background: rgba(239, 68, 68, 0.25);
      color: #fca5a5;
      border-color: rgba(239, 68, 68, 0.6);
    }

    .card-title {
      font-size: 1rem;
      font-weight: 600;
      line-height: 1.4;
      margin-bottom: 0.5rem;
      color: var(--text-main);
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }

    .episode-tag {
      background: rgba(16, 185, 129, 0.15);
      color: #34d399;
      border: 1px solid rgba(16, 185, 129, 0.3);
      padding: 0.1rem 0.4rem;
      border-radius: 4px;
      font-size: 0.75rem;
      font-weight: 700;
      margin-right: 0.4rem;
      display: inline-block;
      vertical-align: middle;
    }

    .card-actors {
      display: flex;
      flex-wrap: wrap;
      gap: 0.35rem;
      margin-bottom: 0.4rem;
    }

    .actor-badge {
      background: rgba(16, 185, 129, 0.12);
      color: #6ee7b7;
      border: 1px solid rgba(16, 185, 129, 0.25);
      font-size: 0.72rem;
      padding: 0.15rem 0.45rem;
      border-radius: 4px;
      cursor: pointer;
      transition: var(--transition);
      white-space: nowrap;
      display: inline-flex;
      align-items: center;
      gap: 0.2rem;
    }

    .actor-badge:hover {
      background: #10b981;
      color: #ffffff;
      border-color: #10b981;
    }

    .card-tags {
      display: flex;
      flex-wrap: wrap;
      gap: 0.35rem;
      margin-bottom: 0.6rem;
    }

    .tag-badge {
      background: rgba(99, 102, 241, 0.12);
      color: #a5b4fc;
      border: 1px solid rgba(99, 102, 241, 0.25);
      font-size: 0.72rem;
      padding: 0.15rem 0.45rem;
      border-radius: 4px;
      cursor: pointer;
      transition: var(--transition);
      white-space: nowrap;
    }

    .tag-badge:hover {
      background: var(--primary);
      color: white;
      border-color: var(--primary);
    }

    .card-path {
      font-size: 0.75rem;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      color: var(--text-sub);
      background: rgba(0, 0, 0, 0.3);
      padding: 0.4rem 0.6rem;
      border-radius: 6px;
      margin-top: auto;
      margin-bottom: 0.75rem;
      word-break: break-all;
      display: -webkit-box;
      -webkit-line-clamp: 2;
      -webkit-box-orient: vertical;
      overflow: hidden;
    }

    .card-actions {
      display: flex;
      gap: 0.5rem;
    }

    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 0.4rem;
      padding: 0.6rem 1rem;
      border-radius: 8px;
      font-size: 0.88rem;
      font-weight: 600;
      text-decoration: none;
      cursor: pointer;
      border: none;
      transition: var(--transition);
      flex: 1;
    }

    .btn-primary {
      background: var(--primary);
      color: white;
    }

    .btn-primary:hover {
      background: var(--primary-hover);
    }

    .btn-mpv {
      background: linear-gradient(135deg, #4f46e5, #7c3aed);
      color: white;
    }

    .btn-mpv:hover {
      background: linear-gradient(135deg, #4338ca, #6d28d9);
      box-shadow: 0 0 12px rgba(124, 58, 237, 0.4);
    }

    .btn-iina {
      background: linear-gradient(135deg, #2563eb, #3b82f6);
      color: white;
    }

    .btn-iina:hover {
      background: linear-gradient(135deg, #1d4ed8, #2563eb);
      box-shadow: 0 0 12px rgba(59, 130, 246, 0.4);
    }

    .btn-secondary {
      background: #273043;
      color: var(--text-main);
      flex: 0 0 auto;
      padding: 0.6rem;
    }

    .btn-secondary:hover {
      background: #36425c;
    }

    .empty-state {
      text-align: center;
      padding: 4rem 1rem;
      color: var(--text-muted);
      grid-column: 1 / -1;
    }

    .empty-icon {
      font-size: 3rem;
      margin-bottom: 1rem;
    }

    /* Infinite Scroll Footer */
    .scroll-loader {
      text-align: center;
      padding: 2.5rem 1rem 4rem;
      color: var(--text-sub);
      font-size: 0.95rem;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 0.5rem;
    }

    /* Edit Modal */
    .modal-backdrop {
      position: fixed;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      background: rgba(0, 0, 0, 0.75);
      backdrop-filter: blur(8px);
      z-index: 200;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 1.5rem;
      opacity: 0;
      visibility: hidden;
      transition: all 0.25s ease;
    }

    .modal-backdrop.open {
      opacity: 1;
      visibility: visible;
    }

    .modal-content {
      background: var(--bg-card);
      border: 1px solid var(--border-color);
      border-radius: 16px;
      max-width: 660px;
      width: 100%;
      max-height: 92vh;
      overflow-y: auto;
      padding: 1.25rem 1.5rem;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.7);
      transform: translateY(20px);
      transition: all 0.25s ease;
      display: flex;
      flex-direction: column;
      gap: 0.85rem;
    }

    .modal-backdrop.open .modal-content {
      transform: translateY(0);
    }

    .modal-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 1px solid var(--border-color);
      padding-bottom: 0.6rem;
    }

    .modal-title {
      font-size: 1.15rem;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }

    .modal-close-btn {
      background: none;
      border: none;
      color: var(--text-muted);
      font-size: 1.3rem;
      cursor: pointer;
      padding: 0.15rem 0.45rem;
      border-radius: 6px;
      transition: var(--transition);
    }

    .modal-close-btn:hover {
      color: white;
      background: #273043;
    }

    .ratings-row {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 0.75rem;
    }

    @media (max-width: 620px) {
      .ratings-row {
        grid-template-columns: 1fr;
      }
    }

    .form-group {
      display: flex;
      flex-direction: column;
      gap: 0.35rem;
    }

    .form-label {
      font-size: 0.84rem;
      font-weight: 600;
      color: var(--text-muted);
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    .form-label-hint {
      font-size: 0.72rem;
      color: var(--text-sub);
      font-weight: normal;
    }

    .form-input, .form-textarea {
      background: #0e1117;
      border: 1px solid var(--border-color);
      color: var(--text-main);
      color-scheme: dark;
      padding: 0.55rem 0.8rem;
      border-radius: 8px;
      font-size: 0.9rem;
      outline: none;
      transition: var(--transition);
    }

    .form-input:focus, .form-textarea:focus {
      border-color: var(--primary);
      box-shadow: 0 0 0 3px var(--primary-light);
    }

    .form-textarea {
      resize: vertical;
      min-height: 60px;
      line-height: 1.4;
    }

    /* Rating Input Controls */
    .rating-control-box {
      background: #0e1117;
      border: 1px solid var(--border-color);
      border-radius: 10px;
      padding: 0.65rem 0.85rem;
      display: flex;
      flex-direction: column;
      gap: 0.45rem;
    }

    .rating-header-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    .rating-display-val {
      font-size: 1.25rem;
      font-weight: 800;
      color: var(--accent-star);
      display: flex;
      align-items: center;
      gap: 0.25rem;
    }

    .rating-presets {
      display: flex;
      gap: 0.25rem;
      flex-wrap: wrap;
    }

    .preset-btn {
      background: #1c2230;
      border: 1px solid var(--border-color);
      color: var(--text-main);
      padding: 0.18rem 0.45rem;
      border-radius: 5px;
      font-size: 0.75rem;
      font-weight: 600;
      cursor: pointer;
      transition: var(--transition);
    }

    .preset-btn:hover {
      background: #2b344a;
      border-color: var(--accent-star);
      color: var(--accent-star);
    }

    .rating-slider {
      width: 100%;
      height: 8px;
      border-radius: 4px;
      background: #273043;
      outline: none;
      cursor: pointer;
      accent-color: var(--accent-star);
    }

    .modal-actions {
      display: flex;
      gap: 0.75rem;
      justify-content: flex-end;
      margin-top: 0.5rem;
    }

    /* Toast */
    .toast {
      position: fixed;
      bottom: 2rem;
      right: 2rem;
      background: #1f2937;
      color: #fff;
      padding: 0.75rem 1.25rem;
      border-radius: 8px;
      box-shadow: 0 10px 25px rgba(0, 0, 0, 0.5);
      border: 1px solid var(--border-color);
      display: flex;
      align-items: center;
      gap: 0.5rem;
      z-index: 1000;
      transform: translateY(100px);
      opacity: 0;
      transition: all 0.3s ease;
      font-size: 0.9rem;
    }

    .toast.show {
      transform: translateY(0);
      opacity: 1;
    }

    /* Loading Spinner */
    .spinner {
      display: inline-block;
      width: 18px;
      height: 18px;
      border: 2px solid rgba(255, 255, 255, 0.2);
      border-radius: 50%;
      border-top-color: white;
      animation: spin 0.8s linear infinite;
      vertical-align: middle;
    }

    @keyframes spin {
      to { transform: rotate(360deg); }
    }

    @media (max-width: 768px) {
      .header-content {
        flex-direction: column;
        align-items: stretch;
      }
      .controls-wrapper {
        max-width: 100%;
        flex-wrap: wrap;
      }
      .search-bar {
        min-width: 100%;
      }
      .grid {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <header>
    <div class="header-content">
      <div class="brand" id="brandBtn" title="ホームに戻る（検索をクリア）" role="button" tabindex="0">
        <span class="brand-icon">🎬</span>
        <div>
          <div class="brand-title">jelly2iina</div>
          <div class="brand-subtitle">Jellyfin to IINA Direct Player</div>
        </div>
      </div>

      <div class="controls-wrapper">
        <div class="search-bar">
          <span class="search-icon">🔍</span>
          <input type="text" id="searchInput" class="search-input" placeholder="シリーズ・エピソード・出演者・タグ等 (空欄で全件)" autocomplete="off">
          <button id="clearBtn" class="clear-btn" title="クリア">✕</button>
        </div>

        <select id="playerSelect" class="player-select" title="再生プレイヤーの選択">
          <option value="auto">🎬 プレイヤー: 自動 (推奨)</option>
          <option value="mpv_stream">⚡ MPV (Direct Stream)</option>
          <option value="mpv_file">⚡ MPV (ローカルパス)</option>
          <option value="iina_file">🍎 IINA (ローカルパス)</option>
          <option value="iina_stream">🍎 IINA (Direct Stream)</option>
        </select>

        <select id="sortSelect" class="sort-select" title="並び順を選択">
          <option value="premiere_desc" selected>📅 公開日 (新しい順)</option>
          <option value="premiere_asc">📅 公開日 (古い順)</option>
          <option value="date_desc">🕒 追加日 (新しい順)</option>
          <option value="date_asc">⏳ 追加日 (古い順)</option>
          <option value="user_rating_desc">💕 個人評価 (高い順)</option>
          <option value="user_rating_asc">💕 個人評価 (低い順)</option>
          <option value="rating_desc">⭐ コミュニティ評価 (高い順)</option>
          <option value="rating_asc">⭐ コミュニティ評価 (低い順)</option>
          <option value="filename_asc">📁 ファイル名 (昇順)</option>
          <option value="filename_desc">📁 ファイル名 (降順)</option>
          <option value="name_asc">🔤 タイトル (A-Z)</option>
          <option value="name_desc">🔠 タイトル (Z-A)</option>
          <option value="random">🎲 ランダム</option>
        </select>

        <button type="button" id="refreshBtn" class="refresh-btn" title="再読み込み（検索・ソート条件を維持して更新）">
          <svg class="refresh-icon" xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/>
            <path d="M3 3v5h5"/>
            <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/>
            <path d="M16 16h5v5"/>
          </svg>
        </button>
      </div>

      <a href="#" target="_blank" rel="noopener noreferrer" class="server-status" id="serverStatus" title="Jellyfinホームを開く (新しいタブ)">
        <span class="status-dot" id="statusDot"></span>
        <span id="statusText">接続確認中...</span>
        <span class="status-ext-icon">↗</span>
      </a>
    </div>
  </header>

  <main>
    <div id="apiKeyAlert" class="notification-banner">
      <div class="notification-title">⚠️ Jellyfin APIキーが設定されていません</div>
      <div class="notification-desc">
        Jellyfin 管理画面（<code>ダッシュボード &gt; 詳細 &gt; APIキー</code>）でキーを発行し、
        プロジェクト直下の <code>.env</code> ファイルに <code>JELLYFIN_API_KEY=あなたのAPIキー</code> を設定してコンテナを再起動してください。
      </div>
    </div>

    <div class="meta-bar">
      <span id="resultCount">読み込み中...</span>
      <span id="currentFilter"></span>
    </div>

    <div id="videoGrid" class="grid">
      <!-- カードがここに動的挿入されます -->
    </div>

    <div id="scrollLoader" class="scroll-loader">
      <span class="spinner"></span> 続きを読み込み中...
    </div>
  </main>

  <!-- メタデータ編集モーダル -->
  <div id="editModal" class="modal-backdrop">
    <div class="modal-content">
      <div class="modal-header">
        <div class="modal-title">✏️ メタデータ編集</div>
        <button id="modalCloseBtn" class="modal-close-btn" title="閉じる">✕</button>
      </div>

      <input type="hidden" id="editItemId">

      <!-- 評価設定 (コミュニティ評価が左/上、個人評価が右) -->
      <div class="ratings-row">
        <!-- コミュニティ評価 -->
        <div class="form-group">
          <div class="form-label">
            <span>⭐ コミュニティ評価</span>
            <span class="form-label-hint">Jellyfin共通スコア</span>
          </div>
          <div class="rating-control-box">
            <div class="rating-header-row">
              <div id="ratingDisplayVal" class="rating-display-val" style="color: var(--accent-star);">⭐ 8.5</div>
              <div class="rating-presets">
                <button type="button" class="preset-btn" onclick="setPresetRating(10.0)">10</button>
                <button type="button" class="preset-btn" onclick="setPresetRating(9.0)">9.0</button>
                <button type="button" class="preset-btn" onclick="setPresetRating(8.0)">8.0</button>
                <button type="button" class="preset-btn" onclick="setPresetRating(7.0)">7.0</button>
                <button type="button" class="preset-btn" onclick="setPresetRating(null)">未設定</button>
              </div>
            </div>
            <input type="range" id="ratingSlider" class="rating-slider" min="0" max="10" step="0.1" value="8.0">
          </div>
        </div>

        <!-- 個人評価 -->
        <div class="form-group">
          <div class="form-label">
            <span>💕 個人評価 (User Rating)</span>
            <span class="form-label-hint">あなたのスコア</span>
          </div>
          <div class="rating-control-box">
            <div class="rating-header-row">
              <div id="userRatingDisplayVal" class="rating-display-val" style="color: #f472b6;">💕 8.5</div>
              <div class="rating-presets">
                <button type="button" class="preset-btn" onclick="setPresetUserRating(10.0)">10</button>
                <button type="button" class="preset-btn" onclick="setPresetUserRating(9.0)">9.0</button>
                <button type="button" class="preset-btn" onclick="setPresetUserRating(8.0)">8.0</button>
                <button type="button" class="preset-btn" onclick="setPresetUserRating(7.0)">7.0</button>
                <button type="button" class="preset-btn" onclick="setPresetUserRating(null)">未設定</button>
              </div>
            </div>
            <input type="range" id="userRatingSlider" class="rating-slider" min="0" max="10" step="0.1" value="8.0">
          </div>
        </div>
      </div>

      <!-- タイトル -->
      <div class="form-group">
        <div class="form-label">
          <span>動画タイトル</span>
        </div>
        <input type="text" id="editNameInput" class="form-input" placeholder="動画タイトル">
      </div>

      <!-- 公開日 -->
      <div class="form-group">
        <div class="form-label">
          <span>📅 公開日 (Premiere Date)</span>
          <span class="form-label-hint">年/月/日 (ソート基準・年号表示に使用)</span>
        </div>
        <div style="display:flex; gap:0.5rem; align-items:center;">
          <input type="date" id="editPremiereDateInput" class="form-input" style="flex:1;">
          <button type="button" id="clearDateBtn" class="btn btn-secondary" style="padding:0.65rem 0.9rem; font-size:0.8rem; white-space:nowrap;" title="公開日を未設定にする">未設定にする</button>
        </div>
      </div>

      <!-- タグ -->
      <div class="form-group">
        <div class="form-label">
          <span>タグ (Tags)</span>
          <span class="form-label-hint">カンマ区切りで複数指定</span>
        </div>
        <input type="text" id="editTagsInput" class="form-input" placeholder="例: アニメ, アクション, SF, ドラマ">
      </div>

      <!-- 概要 -->
      <div class="form-group">
        <div class="form-label">
          <span>概要 (Overview)</span>
        </div>
        <textarea id="editOverviewInput" class="form-textarea" placeholder="作品のあらすじやメモ..."></textarea>
      </div>

      <div class="modal-actions">
        <button type="button" id="modalCancelBtn" class="btn btn-secondary" style="padding:0.6rem 1.2rem;">キャンセル</button>
        <button type="button" id="modalSaveBtn" class="btn btn-primary" style="flex:0 0 auto; padding:0.6rem 1.8rem;">💾 Jellyfinへ保存</button>
      </div>
    </div>
  </div>

  <div id="toast" class="toast">
    <span id="toastMsg">通知メッセージ</span>
  </div>

  <script>
    const searchInput = document.getElementById('searchInput');
    const clearBtn = document.getElementById('clearBtn');
    const playerSelect = document.getElementById('playerSelect');
    const sortSelect = document.getElementById('sortSelect');
    const refreshBtn = document.getElementById('refreshBtn');
    const videoGrid = document.getElementById('videoGrid');
    const resultCount = document.getElementById('resultCount');
    const brandBtn = document.getElementById('brandBtn');
    const serverStatus = document.getElementById('serverStatus');
    const statusDot = document.getElementById('statusDot');
    const statusText = document.getElementById('statusText');
    const apiKeyAlert = document.getElementById('apiKeyAlert');
    const scrollLoader = document.getElementById('scrollLoader');
    const toast = document.getElementById('toast');
    const toastMsg = document.getElementById('toastMsg');

    // OS判定とプレイヤー設定
    const isMac = /Macintosh|Mac OS X/i.test(navigator.userAgent);
    let serverOs = 'linux';
    let playerMode = localStorage.getItem('jelly2iina_player') || 'auto';
    if (playerSelect) {
      playerSelect.value = playerMode;
      playerSelect.addEventListener('change', (e) => {
        playerMode = e.target.value;
        localStorage.setItem('jelly2iina_player', playerMode);
        const currentItems = Array.from(itemsCache.values());
        if (currentItems.length > 0) {
          renderVideos(currentItems, false);
        }
      });
    }

    function getPlayConfig(item) {
      const currentServerOs = item.server_os || serverOs;
      let mode = playerMode;

      if (mode === 'auto') {
        if (currentServerOs === 'windows') {
          mode = isMac ? 'iina_stream' : 'mpv_file';
        } else {
          mode = isMac ? 'iina_file' : 'mpv_stream';
        }
      }

      if (mode === 'mpv_file') {
        const url = item.mpv_file_url;
        return {
          type: 'mpv',
          url: url,
          label: '▶ MPVで再生',
          title: 'MPV (mpv.net) でローカルパス直接再生',
          cls: 'btn-mpv',
          disabled: !url
        };
      } else if (mode === 'mpv_stream') {
        const url = item.mpv_stream_url || item.mpv_url;
        return {
          type: 'mpv',
          url: url,
          label: '▶ MPVで再生',
          title: 'MPV (mpv.net) でダイレクトストリーム再生',
          cls: 'btn-mpv',
          disabled: !url
        };
      } else if (mode === 'iina_stream') {
        const url = item.iina_stream_url;
        return {
          type: 'iina',
          url: url,
          label: '▶ IINAで再生',
          title: 'MacのIINAでダイレクトストリーム再生',
          cls: 'btn-iina',
          disabled: !url
        };
      } else { // iina_file
        const url = item.iina_file_url || item.iina_url;
        return {
          type: 'iina',
          url: url,
          label: '▶ IINAで再生',
          title: 'MacのIINAでローカルファイル直接再生',
          cls: 'btn-iina',
          disabled: !url
        };
      }
    }

    // モーダル要素
    const editModal = document.getElementById('editModal');
    const modalCloseBtn = document.getElementById('modalCloseBtn');
    const modalCancelBtn = document.getElementById('modalCancelBtn');
    const modalSaveBtn = document.getElementById('modalSaveBtn');
    const editItemId = document.getElementById('editItemId');
    const userRatingDisplayVal = document.getElementById('userRatingDisplayVal');
    const userRatingSlider = document.getElementById('userRatingSlider');
    const ratingDisplayVal = document.getElementById('ratingDisplayVal');
    const ratingSlider = document.getElementById('ratingSlider');
    const editNameInput = document.getElementById('editNameInput');
    const editTagsInput = document.getElementById('editTagsInput');
    const editOverviewInput = document.getElementById('editOverviewInput');
    const editPremiereDateInput = document.getElementById('editPremiereDateInput');
    const clearDateBtn = document.getElementById('clearDateBtn');

    const PAGE_LIMIT = 40;
    let currentStartIndex = 0;
    let totalItems = 0;
    let loadedCount = 0;
    let isLoading = false;
    let hasMore = true;
    let currentQuery = '';
    let currentSort = 'premiere_desc';
    let debounceTimer = null;
    let observer = null;

    // 現在画面に表示されているアイテムのキャッシュ (Id -> Item)
    const itemsCache = new Map();

    function showToast(message) {
      toastMsg.textContent = message;
      toast.classList.add('show');
      setTimeout(() => {
        toast.classList.remove('show');
      }, 2500);
    }

    async function checkHealth() {
      try {
        const res = await fetch('/health');
        if (!res.ok) throw new Error('Health check failed');
        const data = await res.json();
        if (data.server_os) {
          serverOs = data.server_os;
        }
        if (data.jellyfin_url) {
          serverStatus.href = data.jellyfin_url;
        }
        if (data.jellyfin_connected) {
          statusDot.className = 'status-dot online';
          statusText.textContent = `${data.server_name || 'Jellyfin'} 接続済み`;
          serverStatus.title = `Jellyfinを開く (${data.jellyfin_url || ''})`;
        } else {
          statusDot.className = 'status-dot offline';
          statusText.textContent = 'Jellyfin 未接続';
          serverStatus.title = `Jellyfin 未接続 (${data.jellyfin_url || ''})`;
        }

        if (!data.api_key_configured) {
          apiKeyAlert.classList.add('active');
        } else {
          apiKeyAlert.classList.remove('active');
        }
      } catch (e) {
        statusDot.className = 'status-dot offline';
        statusText.textContent = 'オフライン';
      }
    }

    async function fetchVideos(query, sort, startIndex, append = false) {
      if (isLoading) return;
      isLoading = true;

      if (!append) {
        resultCount.innerHTML = '<span class="spinner"></span> 検索中...';
        scrollLoader.style.display = 'none';
        itemsCache.clear();
      } else {
        scrollLoader.style.display = 'flex';
        scrollLoader.innerHTML = '<span class="spinner"></span> 続きを読み込み中...';
      }

      try {
        const params = new URLSearchParams({
          limit: PAGE_LIMIT,
          startIndex: startIndex,
          sort: sort,
        });
        if (query) {
          params.append('q', query);
        }

        const res = await fetch(`/api/search?${params.toString()}`);
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          if (res.status === 401) {
            apiKeyAlert.classList.add('active');
            videoGrid.innerHTML = `
              <div class="empty-state">
                <div class="empty-icon">🔑</div>
                <h3>APIキーの設定が必要です</h3>
                <p>JellyfinダッシュボードからAPIキーを発行し、.envに設定してください。</p>
              </div>`;
            resultCount.textContent = '0 件';
            scrollLoader.style.display = 'none';
            return;
          }
          throw new Error(err.detail || '取得に失敗しました');
        }

        const data = await res.json();
        totalItems = data.total;
        const newItems = data.items || [];

        newItems.forEach(it => itemsCache.set(it.id, it));

        if (!append) {
          loadedCount = newItems.length;
          renderVideos(newItems, false);
        } else {
          loadedCount += newItems.length;
          renderVideos(newItems, true);
        }

        currentStartIndex = startIndex + newItems.length;
        hasMore = loadedCount < totalItems && newItems.length > 0;

        updateMetaBar();

        if (hasMore) {
          scrollLoader.style.display = 'flex';
          scrollLoader.innerHTML = '<span class="spinner"></span> 下にスクロールしてさらに読み込み...';
        } else {
          scrollLoader.style.display = loadedCount > 0 ? 'flex' : 'none';
          scrollLoader.innerHTML = `✓ 全 ${totalItems.toLocaleString()} 件をすべて読み込みました`;
        }

      } catch (err) {
        if (!append) {
          videoGrid.innerHTML = `
            <div class="empty-state">
              <div class="empty-icon">⚠️</div>
              <h3>エラーが発生しました</h3>
              <p>${escapeHtml(err.message)}</p>
            </div>`;
          resultCount.textContent = 'エラー';
        } else {
          showToast('追加読み込みに失敗しました');
        }
        scrollLoader.style.display = 'none';
      } finally {
        isLoading = false;
      }
    }

    function updateMetaBar() {
      const queryStr = currentQuery ? ` (検索: "${currentQuery}")` : '';
      if (totalItems === 0) {
        resultCount.textContent = '0 件';
      } else {
        resultCount.textContent = `${loadedCount.toLocaleString()} / ${totalItems.toLocaleString()} 件表示中${queryStr}`;
      }
    }

    function renderVideos(items, append) {
      if (!append && items.length === 0) {
        videoGrid.innerHTML = `
          <div class="empty-state">
            <div class="empty-icon">🎥</div>
            <h3>動画が見つかりませんでした</h3>
            <p>${currentQuery ? `"${escapeHtml(currentQuery)}" に一致する動画はありませんでした。` : '登録されている動画がありません。'}</p>
          </div>`;
        return;
      }

      const cardsHtml = items.map(item => createCardHtml(item)).join('');

      if (append) {
        videoGrid.insertAdjacentHTML('beforeend', cardsHtml);
      } else {
        videoGrid.innerHTML = cardsHtml;
      }
    }

    function createCardHtml(item) {
      const thumbUrl = `/api/image/${item.id}`;
      const seriesHtml = item.series_name 
        ? `<span class="card-series" data-series="${escapeHtml(item.series_name)}" title="このシリーズで検索">${escapeHtml(item.series_name)}</span>` 
        : '';
      const studioHtml = item.studios && item.studios.length > 0 ? `<span class="card-studio">${escapeHtml(item.studios[0])}</span>` : '';
      
      const hasDate = Boolean(item.premiere_date || item.year);
      const yearHtml = hasDate 
        ? `<span class="card-year" title="公開日: ${escapeHtml(item.premiere_date || String(item.year))}">${item.year || item.premiere_date.slice(0, 4)}</span>`
        : `<span class="card-year unconfigured" data-id="${item.id}" title="公開日未設定（クリックして設定）">公開日未設定</span>`;

      const runtimeHtml = item.runtime ? `<span class="card-badge">${item.runtime}</span>` : '';
      
      const hasUserRating = item.user_rating !== null && item.user_rating !== undefined;
      const hasCommRating = item.rating !== null && item.rating !== undefined;

      const commBadge = hasCommRating
        ? `<span class="rating-badge-item" style="color: var(--accent-star);">⭐ ${item.rating}</span>`
        : `<span class="rating-badge-item unrated" style="color: var(--accent-star); opacity: 0.55;">⭐ 未設定</span>`;

      const userBadge = hasUserRating
        ? `<span class="rating-badge-item" style="color: #f472b6;">💕 ${item.user_rating}</span>`
        : `<span class="rating-badge-item unrated" style="color: #f472b6; opacity: 0.55;">💕 未設定</span>`;

      const ratingHtml = `
        <span class="card-rating" data-id="${item.id}" title="コミュニティ評価: ${hasCommRating ? item.rating : '未設定'} / 個人評価: ${hasUserRating ? item.user_rating : '未設定'} (クリックして編集)">
          ${commBadge}
          <span style="opacity: 0.3; font-size: 0.7rem;">|</span>
          ${userBadge}
        </span>
      `;

      const epBadgeHtml = item.episode_badge ? `<span class="episode-tag">${item.episode_badge}</span>` : '';
      
      let actorsHtml = '';
      if (item.actors && item.actors.length > 0) {
        actorsHtml = `<div class="card-actors">` + 
          item.actors.map(a => `<span class="actor-badge" data-actor="${escapeHtml(a)}" title="この出演者で検索">👤 ${escapeHtml(a)}</span>`).join('') +
          `</div>`;
      }

      let tagsHtml = '';
      if (item.tags && item.tags.length > 0) {
        tagsHtml = `<div class="card-tags">` + 
          item.tags.map(t => `<span class="tag-badge" data-tag="${escapeHtml(t)}" title="このタグで検索">#${escapeHtml(t)}</span>`).join('') +
          `</div>`;
      }

      const playCfg = getPlayConfig(item);
      let playBtn = '';
      if (!playCfg.disabled && playCfg.url) {
        playBtn = `<a href="${playCfg.url}" class="btn btn-primary ${playCfg.cls}" title="${escapeHtml(playCfg.title)}">${playCfg.label}</a>`;
      } else {
        playBtn = `<button class="btn btn-primary ${playCfg.cls}" disabled style="opacity:0.5;">再生不可</button>`;
      }

      return `
        <div class="card" id="card-${item.id}">
          <div class="card-thumb">
            <img src="${thumbUrl}" alt="${escapeHtml(item.name)}" loading="lazy" onerror="this.src='data:image/svg+xml;utf8,<svg xmlns=\\'http://www.w3.org/2000/svg\\' width=\\'400\\' height=\\'400\\' fill=\\'%23181b24\\'><rect width=\\'100%\\' height=\\'100%\\'/><text x=\\'50%\\' y=\\'50%\\' fill=\\'%236c7993\\' text-anchor=\\'middle\\' font-size=\\'16\\'>No Image</text></svg>'">
            ${ratingHtml}
            <button class="card-edit-btn" data-id="${item.id}" title="メタデータ・評価を編集">✏️</button>
            ${runtimeHtml}
          </div>
          <div class="card-body">
            <div class="card-meta-line">
              <div style="display:flex; align-items:center; gap:0.4rem; overflow:hidden;">
                ${seriesHtml}
                ${studioHtml}
              </div>
              ${yearHtml}
            </div>
            <div class="card-title" title="${escapeHtml(item.name)}">${epBadgeHtml}${escapeHtml(item.name)}</div>
            ${actorsHtml}
            ${tagsHtml}
            <div class="card-path" title="${escapeHtml(item.path)}">${escapeHtml(item.path || 'パスなし')}</div>
            <div class="card-actions">
              ${playBtn}
              <button class="btn btn-secondary copy-btn" data-path="${escapeHtml(item.path)}" title="ファイルパスをコピー">📋</button>
              <button class="btn btn-secondary copy-stream-btn" data-stream="${escapeHtml(item.stream_url)}" title="Direct Stream URLをコピー">🔗</button>
            </div>
          </div>
        </div>
      `;
    }

    // モーダル操作
    function openEditModal(itemId) {
      const item = itemsCache.get(itemId);
      if (!item) return;

      editItemId.value = itemId;
      editNameInput.value = item.name || '';
      editPremiereDateInput.value = item.premiere_date || '';
      editTagsInput.value = (item.tags || []).join(', ');
      editOverviewInput.value = item.overview || '';

      setUserRatingUi(item.user_rating);
      setRatingUi(item.rating);

      editModal.classList.add('open');
    }

    function closeEditModal() {
      editModal.classList.remove('open');
    }

    function setUserRatingUi(val) {
      if (val === null || val === undefined || val === '') {
        userRatingDisplayVal.textContent = '💕 -';
        userRatingDisplayVal.style.color = 'var(--text-sub)';
        userRatingSlider.value = 0;
        userRatingSlider.dataset.cleared = 'true';
      } else {
        const num = parseFloat(val);
        userRatingDisplayVal.textContent = num == 10 ? '💕 10' : `💕 ${num.toFixed(1)}`;
        userRatingDisplayVal.style.color = '#f472b6';
        userRatingSlider.value = num;
        userRatingSlider.dataset.cleared = 'false';
      }
    }

    function setPresetUserRating(val) {
      setUserRatingUi(val);
    }

    userRatingSlider.addEventListener('input', (e) => {
      setUserRatingUi(e.target.value);
    });

    function setRatingUi(val) {
      if (val === null || val === undefined || val === '') {
        ratingDisplayVal.textContent = '⭐ -';
        ratingDisplayVal.style.color = 'var(--text-sub)';
        ratingSlider.value = 0;
        ratingSlider.dataset.cleared = 'true';
      } else {
        const num = parseFloat(val);
        ratingDisplayVal.textContent = num == 10 ? '⭐ 10' : `⭐ ${num.toFixed(1)}`;
        ratingDisplayVal.style.color = 'var(--accent-star)';
        ratingSlider.value = num;
        ratingSlider.dataset.cleared = 'false';
      }
    }

    function setPresetRating(val) {
      setRatingUi(val);
    }

    ratingSlider.addEventListener('input', (e) => {
      setRatingUi(e.target.value);
    });

    clearDateBtn.addEventListener('click', () => {
      editPremiereDateInput.value = '';
    });

    async function saveMetadata() {
      const itemId = editItemId.value;
      if (!itemId) return;

      const isUserCleared = userRatingSlider.dataset.cleared === 'true';
      const userRatingVal = isUserCleared ? null : parseFloat(userRatingSlider.value);
      const isCleared = ratingSlider.dataset.cleared === 'true';
      const ratingVal = isCleared ? null : parseFloat(ratingSlider.value);
      const nameVal = editNameInput.value.trim();
      const premiereDateVal = editPremiereDateInput.value;
      const isDateCleared = !premiereDateVal;
      const tagsVal = editTagsInput.value.split(',').map(s => s.trim()).filter(Boolean);
      const overviewVal = editOverviewInput.value.trim();

      modalSaveBtn.disabled = true;
      modalSaveBtn.innerHTML = '<span class="spinner"></span> 保存中...';

      try {
        const payload = {
          user_rating: userRatingVal,
          clear_user_rating: isUserCleared,
          community_rating: ratingVal,
          clear_rating: isCleared,
          name: nameVal,
          tags: tagsVal,
          overview: overviewVal,
          premiere_date: premiereDateVal,
          clear_premiere_date: isDateCleared
        };

        const res = await fetch(`/api/items/${itemId}/metadata`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });

        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error(err.detail || '保存に失敗しました');
        }

        const data = await res.json();
        showToast('メタデータを更新しました！');
        closeEditModal();

        // キャッシュとDOMのリアルタイム更新
        const item = itemsCache.get(itemId);
        if (item) {
          item.name = data.name;
          item.rating = data.rating !== null && data.rating !== undefined ? round(data.rating, 1) : null;
          item.user_rating = data.user_rating !== null && data.user_rating !== undefined ? round(data.user_rating, 1) : null;
          item.tags = data.tags || [];
          item.overview = data.overview || '';
          item.premiere_date = data.premiere_date || '';
          item.year = data.year;
          
          const cardEl = document.getElementById(`card-${itemId}`);
          if (cardEl) {
            const newHtml = createCardHtml(item);
            cardEl.outerHTML = newHtml;
          }
        }

      } catch (err) {
        showToast(`エラー: ${err.message}`);
      } finally {
        modalSaveBtn.disabled = false;
        modalSaveBtn.textContent = '💾 Jellyfinへ保存';
      }
    }

    function round(num, decimals) {
      return Number(Math.round(num + 'e' + decimals) + 'e-' + decimals);
    }

    modalCloseBtn.addEventListener('click', closeEditModal);
    modalCancelBtn.addEventListener('click', closeEditModal);
    modalSaveBtn.addEventListener('click', saveMetadata);
    editModal.addEventListener('click', (e) => {
      if (e.target === editModal) closeEditModal();
    });

    function resetAndReload() {
      currentStartIndex = 0;
      loadedCount = 0;
      hasMore = true;
      fetchVideos(currentQuery, currentSort, 0, false);
    }

    function escapeHtml(str) {
      if (!str) return '';
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    function copyToClipboard(text) {
      if (!text) {
        showToast('コピーするパスがありません');
        return;
      }
      if (window.isSecureContext && navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(() => {
          showToast('パスをクリップボードにコピーしました！');
        }).catch(() => {
          fallbackCopyText(text);
        });
      } else {
        fallbackCopyText(text);
      }
    }

    function fallbackCopyText(text) {
      try {
        const textArea = document.createElement('textarea');
        textArea.value = text;
        textArea.style.position = 'fixed';
        textArea.style.top = '-9999px';
        textArea.style.left = '-9999px';
        textArea.setAttribute('readonly', '');
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        textArea.setSelectionRange(0, 99999);
        const successful = document.execCommand('copy');
        document.body.removeChild(textArea);
        if (successful) {
          showToast('パスをクリップボードにコピーしました！');
        } else {
          showToast('コピーに失敗しました');
        }
      } catch (err) {
        showToast('コピーに失敗しました');
      }
    }

    // イベントリスナー
    videoGrid.addEventListener('click', (e) => {
      // 編集ボタンまたは評価バッジのクリック
      const editBtn = e.target.closest('.card-edit-btn') || e.target.closest('.card-rating');
      if (editBtn) {
        const id = editBtn.getAttribute('data-id');
        if (id) {
          openEditModal(id);
          return;
        }
      }

      // コピーボタン
      const copyBtn = e.target.closest('.copy-btn');
      if (copyBtn) {
        const path = copyBtn.getAttribute('data-path');
        copyToClipboard(path);
        return;
      }

      // ストリームURLコピーボタン
      const copyStreamBtn = e.target.closest('.copy-stream-btn');
      if (copyStreamBtn) {
        const stream = copyStreamBtn.getAttribute('data-stream');
        if (stream) {
          copyToClipboard(stream);
          showToast('ストリームURLをコピーしました！');
        }
        return;
      }

      // シリーズ名クリックによる絞り込み検索
      const seriesEl = e.target.closest('.card-series');
      if (seriesEl) {
        const series = seriesEl.getAttribute('data-series');
        if (series) {
          searchInput.value = series;
          clearBtn.style.display = 'block';
          currentQuery = series;
          window.scrollTo({ top: 0, behavior: 'smooth' });
          resetAndReload();
          return;
        }
      }

      // タグバッジクリックによる絞り込み検索
      const tagBadge = e.target.closest('.tag-badge');
      if (tagBadge) {
        const tag = tagBadge.getAttribute('data-tag');
        if (tag) {
          searchInput.value = tag;
          clearBtn.style.display = 'block';
          currentQuery = tag;
          window.scrollTo({ top: 0, behavior: 'smooth' });
          resetAndReload();
          return;
        }
      }

      // 出演者バッジクリックによる絞り込み検索
      const actorBadge = e.target.closest('.actor-badge');
      if (actorBadge) {
        const actor = actorBadge.getAttribute('data-actor');
        if (actor) {
          searchInput.value = actor;
          clearBtn.style.display = 'block';
          currentQuery = actor;
          window.scrollTo({ top: 0, behavior: 'smooth' });
          resetAndReload();
          return;
        }
      }

      // 公開日未設定バッジクリックによる編集モーダルオープン
      const unconfiguredYear = e.target.closest('.card-year.unconfigured');
      if (unconfiguredYear) {
        const itemId = unconfiguredYear.getAttribute('data-id');
        if (itemId) {
          openEditModal(itemId);
          return;
        }
      }
    });

    function goHome() {
      if (searchInput.value || currentQuery) {
        searchInput.value = '';
        clearBtn.style.display = 'none';
        currentQuery = '';
      }
      window.scrollTo({ top: 0, behavior: 'smooth' });
      resetAndReload();
    }

    brandBtn.addEventListener('click', goHome);
    brandBtn.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        goHome();
      }
    });

    function refreshView() {
      refreshBtn.classList.add('spinning');
      setTimeout(() => {
        refreshBtn.classList.remove('spinning');
      }, 700);
      window.scrollTo({ top: 0, behavior: 'smooth' });
      resetAndReload();
    }

    refreshBtn.addEventListener('click', refreshView);

    searchInput.addEventListener('input', (e) => {
      const val = e.target.value;
      clearBtn.style.display = val ? 'block' : 'none';
      currentQuery = val.trim();
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        resetAndReload();
      }, 350);
    });

    clearBtn.addEventListener('click', () => {
      searchInput.value = '';
      clearBtn.style.display = 'none';
      currentQuery = '';
      resetAndReload();
    });

    sortSelect.addEventListener('change', (e) => {
      currentSort = e.target.value;
      resetAndReload();
    });

    // 無限スクロールの監視 (IntersectionObserver)
    observer = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting && hasMore && !isLoading) {
        fetchVideos(currentQuery, currentSort, currentStartIndex, true);
      }
    }, {
      rootMargin: '300px 0px',
      threshold: 0.1
    });

    observer.observe(scrollLoader);

    // 初期実行
    checkHealth();
    resetAndReload();
  </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTMLResponse(content=HTML_CONTENT)
