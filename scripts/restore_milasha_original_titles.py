#!/usr/bin/env python3
"""
Restore OriginalTitle for milasha Jellyfin server items
"""
import json
import urllib.request
import ssl
import sys

JELLYFIN_URL = "https://jellyfine.olthon.com"
API_KEY = "9a2a1d5578344b0eaaaeb65fa9f2ff0f"

# Target restoration items: id -> (Name, RestoredOriginalTitle)
RESTORE_MAP = {
    # ER Videos (from .nfo or stripped 品番 prefix)
    "379EEBD2-30BE-69EB-9756-F72D0DCA73CA": (
        "DASS-438 このアバズレクソ女が私の愛する旦那を寝取りやがったから10日後に地獄へ堕ちるまで最狂復讐レ●プでお返し致しますね！ 松本いちか 新村あかり",
        "このアバズレクソ女が私の愛する旦那を寝取りやがったから10日後に地獄へ堕ちるまで最狂復讐レ●プでお返し致しますね！ 松本いちか 新村あかり"
    ),
    "7F52DC53-494D-624B-0315-7F21F6CC061C": (
        "HND-812 あの日、大学の飲み会が中出し輪●サークルに変わった。新歓コンパ編 久留木玲",
        "あの日、大学の飲み会が中出し輪●サークルに変わった。新歓コンパ編 久留木玲"
    ),
    "7B68722D-3EB0-77F4-C3E0-851E52C27CA7": (
        "HND-887 僕は最低だ。 あの娘を好きだったはずの僕は、ゲス友達の誘導に乗せられて今日、中出しレ×プ魔になってしまった。 久留木玲",
        "僕は最低だ。 あの娘を好きだったはずの僕は、ゲス友達の誘導に乗せられて今日、中出しレ×プ魔になってしまった。 久留木玲"
    ),
    "8EC012A4-EE79-0E69-7B7A-B6FF7F8489C2": (
        "IPZZ-924 セクハラ中年教師に弱みを握られた私は校内で寸止めアクメ調教を繰り返され所かまわず失禁しながら羞恥絶頂するはしたない変態女に成り下がってしまいました…。 さくらわかな",
        "セクハラ中年教師に弱みを握られた私は校内で寸止めアクメ調教を繰り返され所かまわず失禁しながら羞恥絶頂するはしたない変態女に成り下がってしまいました…。 さくらわかな"
    ),
    "CF2633FD-292F-B10A-0BB3-9E87259AC643": (
        "MIDA-823 「コスるだけなら浮気にならないっしょ」 彼女の妹の小悪魔素股にコスり堕ち！ 結局濡れ過ぎおま●こにヌルっとナマ挿入が気持ち良すぎて暴走ピストン24発寝取られ中出し 宮下玲奈",
        "「コスるだけなら浮気にならないっしょ」 彼女の妹の小悪魔素股にコスり堕ち！ 結局濡れ過ぎおま●こにヌルっとナマ挿入が気持ち良すぎて暴走ピストン24発寝取られ中出し 宮下玲奈"
    ),
    "517010A0-4B89-B56C-B58E-EE1FCE8A49D7": (
        "MMNM-035 信用ならない愛情より肉欲で満たされたいアンニュイ美少女：りんか(20)【女がハマる甘い沼】",
        "信用ならない愛情より肉欲で満たされたいアンニュイ美少女：りんか(20)【女がハマる甘い沼】"
    ),
    "395C6F25-CA06-5B56-6FBE-B483C7E3CF97": (
        "NNPJ-479 中出し6発敏感イキの天才 週末、会ってくれませんか？ホントに来た娘とホテルで生ハメ！ なつちゃん（大学3年生）",
        "中出し6発敏感イキの天才 週末、会ってくれませんか？ホントに来た娘とホテルで生ハメ！ なつちゃん（大学3年生）"
    ),

    # NERVIDEOS IV (from EPXE-5081.nfo)
    "27C29083-15A4-6733-802A-B0B53ECA99BA": (
        "工藤遥 ～春夏 -Haruka-～",
        "工藤遥 ～春夏 -Haruka-～"
    ),

    # Anime Episodes
    "7312E63A-8F4A-5946-877B-60D98C74F913": (
        "妹巫女ぷりん・ほのか〜拡げて弾く未開のレッスン♥",
        "妹巫女ぷりん・ほのか〜拡げて弾く未開のレッスン♥"
    ),
    "C00F391D-39FD-A1C0-DFF5-99B6F1A297DA": (
        "第2話 (イジラレ～復讐催眠～)",
        "第2話"
    ),
    "C87D42EB-A8A3-C477-974A-C4F81B9E992E": (
        "第3話 (家属 ~母と姉妹の嬌声~)",
        "第3話"
    ),
    "10F2D94D-F04B-4CD8-5663-4D7DF2C0E1D2": (
        "興味津々なお年頃 (思春期のお勉強 S01E01)",
        "興味津々なお年頃"
    ),
    "A420745F-4872-C6CC-ED05-3D50977DA55A": (
        "学ぶより経験がしたいお年頃 (思春期のお勉強 S01E02)",
        "学ぶより経験がしたいお年頃"
    ),
    "A5D709C2-8A3F-DBFF-69B0-718F531C091C": (
        "キスをしてみたいお年頃 (思春期のお勉強 S01E03)",
        "キスをしてみたいお年頃"
    ),
    "B9F6501E-3126-EE51-D768-C864F29DCD25": (
        "そしてわたしはおじさんに... concat E01",
        "そしてわたしはおじさんに... concat E01"
    ),
    "6E8D64B9-C6B0-DFFE-8124-AF0E8B38D9B1": (
        "Collaboration Works True Blue concat",
        "Collaboration Works True Blue concat"
    ),
    "4140661C-3787-1005-A1F5-10A3C88B96E3": (
        "メスのちトラレ_concat",
        "メスのちトラレ_concat"
    ),
    "820F5E0E-BDD7-2DBC-6FE6-D7FDE5BD42E9": (
        "ピンクパイナップル 下級生2 季花詩集 第一節「トードリリー～秘密～」 concat",
        "ピンクパイナップル 下級生2 季花詩集 第一節「トードリリー～秘密～」 concat"
    ),
    "7EDFA80D-EA66-6845-4C28-F129B2D5CE69": (
        "ピンクパイナップル 下級生2 季花詩集 第一節「トードリリー～秘密～」 concat",
        "ピンクパイナップル 下級生2 季花詩集 第一節「トードリリー～秘密～」 concat"
    ),
    "5EB1A82F-7EE7-0A84-35D1-DAAB5C64A81D": (
        "あかね色に染まる坂 Ver1.02 2023-07-26 17-56-35",
        "あかね色に染まる坂 Ver1.02 2023-07-26 17-56-35"
    ),
    "C4563FEF-6351-F2C6-BCF5-E4172D6C41D7": (
        "失われた未来を求めて 支倉愛理 03 シャワー室、二人の気持ち",
        "失われた未来を求めて 支倉愛理 03 シャワー室、二人の気持ち"
    )
}

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

def main():
    dry_run = "--dry-run" in sys.argv
    print(f"=== milasha OriginalTitle Restoration Script ===")
    print(f"Server: {JELLYFIN_URL}")
    print(f"Total target items: {len(RESTORE_MAP)}")
    if dry_run:
        print("[DRY-RUN MODE] No changes will be saved to Jellyfin.")
    print("-" * 50)

    user_id = get_user_id()
    print(f"Using User ID: {user_id}")

    restored = 0
    errors = 0

    for item_id, (name_hint, target_ot) in RESTORE_MAP.items():
        clean_id = item_id.replace("-", "").lower()
        get_url = f"{JELLYFIN_URL}/Users/{user_id}/Items/{clean_id}?fields={ALL_UPDATE_FIELDS}"
        req = urllib.request.Request(get_url, headers=headers)
        try:
            with urllib.request.urlopen(req, context=ctx) as resp:
                item_dto = json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            print(f"[ERROR] Failed to fetch item {item_id}: {e}")
            errors += 1
            continue

        current_name = item_dto.get("Name")
        current_ot = item_dto.get("OriginalTitle")

        if current_ot == target_ot:
            print(f"[SKIP] '{current_name}' already has OriginalTitle: '{current_ot}'")
            continue

        print(f"[RESTORE] '{current_name}'")
        print(f"   Current OT : {repr(current_ot)}")
        print(f"   Target OT  : '{target_ot}'")

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
                    print(f"   -> [WARNING] Unexpected status: {post_resp.status}")
        except Exception as e:
            print(f"   -> [FAILED] {e}")
            errors += 1

    print("-" * 50)
    print(f"Completed! Restored: {restored}, Errors: {errors}")

if __name__ == "__main__":
    main()
