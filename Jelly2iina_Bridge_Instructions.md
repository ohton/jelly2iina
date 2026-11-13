# jelly2iina (Jellyfin to IINA Bridge) 開発・運用指示書

## 1. プロジェクト概要

Linux（Ubuntu / ホスト例: `192.168.1.100`）のネイティブ環境で稼働している Jellyfin サーバーと同一ネットワーク上の Mac クライアントから、ブラウザ経由でメディアを高速検索し、**IINA（`iina://` URIスキーム）を介してローカルマウントパス（`/Volumes/...`）から直接再生**するための軽量 Web アプリケーション（Docker コンテナ）です。

Mac 上の IINA がローカルストレージ（SMBマウント等）のファイルを直接再生するため、トランスコード負荷ゼロ・高画質・低遅延での動画再生を実現します。

---

## 2. 実装要件・アーキテクチャ

- **技術スタック**: Python (FastAPI), Uvicorn, httpx, HTML5/CSS3/JavaScript (単一ファイル構成、レスポンシブなダークテーマUI)
- **稼働環境**: Linux (Ubuntu) 上の Docker / Docker Compose
  - Jellyfin 本体は Linux ホスト上でネイティブ稼働 (`http://localhost:8096` または `http://host.docker.internal:8096`)
- **メディアパスの整合性**:
  - ホスト Linux 上の `/Volumes` に SMB 共有ストレージ（例: `/Volumes/ERVIDEOS`）がマウントされています。
  - Mac クライアント側でも同じパス名（`/Volumes/...`）でマウントされているため、Jellyfin API から返される `Path` をそのまま `file://` プロトコルとして IINA に渡します。
  - 生成URIの形式: `iina://weblink?url=file:///Volumes/...` (特殊文字や空白は URL エンコード処理)
- **コンテナ設計**:
  - コンテナ内ポート: `80`
  - ホスト公開ポート: `8095` (Web UI: `http://<ホストIP>:8095/`)
  - ボリュームマウント: `/Volumes:/Volumes:ro`
  - ホスト間通信: `extra_hosts` による `host.docker.internal:host-gateway` サポート

---

## 3. ファイル構成

本リポジトリで管理・構築するファイル構成は以下の通りです。

```
/home/ohton/work/jelly2iina/
├── requirements.txt              # Python依存パッケージ
├── Dockerfile                    # アプリケーションコンテナ定義
├── app.py                        # FastAPIサーバー & 組み込みWeb UI
├── docker-compose.yml            # Docker Compose構成定義
├── .env.example                  # 環境変数テンプレート
├── .env                          # 環境変数設定 (APIキー等)
├── .gitignore                    # Git除外設定 (.env等)
└── Jelly2iina_Bridge_Instructions.md # 本開発指示書
```

---

## 4. 各コンポーネント詳細仕様

### ① `requirements.txt`

```
fastapi>=0.100.0
uvicorn[standard]>=0.22.0
httpx>=0.24.0
pydantic>=2.0.0
```

### ② `Dockerfile`

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# 依存パッケージインストール
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# アプリケーションソースのコピー
COPY app.py .

EXPOSE 80

# uvicorn起動 (app.py の app インスタンスを指定)
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "80"]
```

### ③ `app.py`

FastAPI 単一ファイルで API エンドポイントと洗練されたダークテーマ UI を提供します。

#### 主要機能:
1. **設定値 (環境変数)**:
   - `JELLYFIN_URL`: Jellyfin サーバーの URL（デフォルト: `http://host.docker.internal:8096`）
   - `JELLYFIN_API_KEY`: Jellyfin 管理者画面で発行した API キー
2. **API エンドポイント**:
   - `GET /`: レスポンシブなモダン Web UI (HTML+CSS+JS)
   - `GET /api/search`: Jellyfin REST API (`/Items`) からメディアアイテムを検索・一覧取得。各アイテムに `iina_url`（`iina://weblink?url=file://...`）を付与して返却
   - `GET /api/image/{item_id}`: Jellyfin のサムネイル画像 (`/Items/{item_id}/Images/Primary`) のプロキシ
   - `POST /api/items/{item_id}/metadata`: コミュニティ評価、タイトル、タグ、概要のメタデータ更新
   - `GET /health`: サーバー状態および Jellyfin との疎通確認
3. **UI 要件**:
   - Jellyfin / Netflix ライクな視認性の高いダークテーマ
   - リアルタイム入力検索 (インクリメンタルサーチ対応) & クリア機能
   - 初回ロード時は最新追加アイテム（最新順）を自動一覧表示
   - カード表示: サムネイル（スクエア比率・縦長/横長全体収容）、評価バッジ（⭐）、編集ボタン（✏️）、タイトル、シリーズ/メーカー名、タグ一覧（インタラクティブ絞り込み対応）、再生時間、ファイルパス
   - ワンクリック「IINA で再生」ボタン (`iina://` スキームの直接起動)
   - クリップボードへの「パスをコピー」ボタン（LAN内HTTP環境・Vivaldi/Chrome/Safari等の非セキュア環境向けフォールバック対応）
   - メタデータ編集モーダル: コミュニティ評価スライダー/プリセット、タイトル、タグ、概要の直接変更とリアルタイムDOM反映
   - 無限スクロール（自動ページネーション）および柔軟なソート（追加順・評価順・タイトル順・ランダム）
   - 統合メタデータ検索（タイトル・概要・出演者・ジャンル・タグ・スタジオを並行検索して重複排除）
   - API キー未設定時やエラー時のインラインガイド通知

### ④ `docker-compose.yml`

```yaml
services:
  jellyfin-iina-bridge:
    build: .
    container_name: jellyfin-iina-bridge
    restart: unless-stopped
    env_file:
      - .env
    environment:
      - JELLYFIN_URL=${JELLYFIN_URL:-http://localhost:8096}
      - JELLYFIN_API_KEY=${JELLYFIN_API_KEY:-}
    ports:
      - "8095:80"
    extra_hosts:
      - "host.docker.internal:host-gateway"
    volumes:
      - ./app.py:/app/app.py
      - /Volumes:/Volumes:ro
```

### ⑤ `.env` / `.env.example`

```bash
# Jellyfin サーバー接続先 (例: ホスト側 Jellyfin ポート 8096)
JELLYFIN_URL=http://192.168.1.100:8096

# Jellyfin 管理者画面 > ダッシュボード > APIキー で作成したトークン
JELLYFIN_API_KEY=your_jellyfin_api_key_here
```

---

## 5. デプロイおよび運用手順

1. **Jellyfin API キーの発行**:
   - ブラウザで Jellyfin（例: `http://192.168.1.100:8096`）に管理者アカウントでログイン。
   - 「ダッシュボード」→「詳細」→「APIキー」を選択。
   - 新規キーを作成（例: 名前「jelly2iina」）し、トークンをコピー。
2. **設定**:
   - `.env` ファイルに `JELLYFIN_API_KEY` を設定。
3. **起動**:
   ```bash
   docker compose up -d --build
   ```
4. **利用**:
   - クライアントのブラウザから `http://<ホストIP>:8095` にアクセス。
   - 検索または一覧から目的の動画を見つけ、「IINAで再生」または「MPVで再生」をクリック。
   - クライアント上のプレイヤーが起動し、動画が直接再生されます。
