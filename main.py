from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import yt_dlp
import os
import uuid
import time
from pathlib import Path
from datetime import datetime
from urllib.parse import unquote, quote

app = FastAPI()

# CORS設定（GitHub Pagesからのアクセスを許可）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ダウンロードフォルダ
DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

# 起動時に古いファイルをクリーンアップ
@app.on_event("startup")
async def startup_cleanup():
    """10分以上前のファイルを削除"""
    cleanup_old_files_sync(600)

def cleanup_old_files_sync(max_age_seconds: int = 600):
    """指定秒数より古いファイルを削除"""
    try:
        cutoff_time = time.time() - max_age_seconds
        deleted_count = 0
        for file in DOWNLOAD_DIR.glob("*"):
            if file.is_file() and file.stat().st_mtime < cutoff_time:
                file.unlink()
                deleted_count += 1
                print(f"🗑️ 削除: {file.name}")
        if deleted_count > 0:
            print(f"✅ クリーンアップ完了: {deleted_count}ファイル削除")
    except Exception as e:
        print(f"❌ クリーンアップエラー: {e}")

class DownloadRequest(BaseModel):
    url: str

@app.get("/")
def read_root():
    return {"message": "SoundCloud Downloader API", "status": "running"}

@app.post("/download")
async def download_track(request: DownloadRequest):
    try:
        # 古いファイルをクリーンアップ
        cleanup_old_files_sync(600)
        
        # 一意のファイル名を生成
        file_id = str(uuid.uuid4())
        output_template = str(DOWNLOAD_DIR / f"{file_id}.%(ext)s")
        
        print(f"📥 ダウンロード開始: {request.url}")
        
        # yt-dlpの設定
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': output_template,
            'quiet': False,
            'no_warnings': False,
            'extract_flat': False,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            # エラーを無視しない
            'ignoreerrors': False,
        }
        
        # ダウンロード実行
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(request.url, download=True)
                if info is None:
                    raise HTTPException(status_code=400, detail="楽曲情報を取得できませんでした")
                
                title = info.get('title', 'unknown')
                print(f"✅ 取得成功: {title}")
                
                # ファイル名に使えない文字を削除
                safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_', '.')).strip()
                if not safe_title:
                    safe_title = "download"
                    
            except Exception as e:
                print(f"❌ yt-dlpエラー: {str(e)}")
                raise HTTPException(status_code=400, detail=f"ダウンロードエラー: {str(e)}")
        
        # ダウンロードされたファイルを検索
        downloaded_file = None
        for file in DOWNLOAD_DIR.glob(f"{file_id}.*"):
            downloaded_file = file
            break
        
        if not downloaded_file or not downloaded_file.exists():
            raise HTTPException(status_code=500, detail="ファイルのダウンロードに失敗しました。楽曲が存在しないか、ダウンロードが許可されていない可能性があります。")
        
        print(f"💾 ファイル保存完了: {downloaded_file.name}")
        
        return {
            "success": True,
            "title": title,
            "safe_filename": f"{safe_title}.mp3",
            "download_url": f"/file/{downloaded_file.name}"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ 予期しないエラー: {str(e)}")
        raise HTTPException(status_code=500, detail=f"サーバーエラー: {str(e)}")

@app.get("/file/{filename}")
async def get_file(filename: str, download_name: str = None):
    file_path = DOWNLOAD_DIR / filename
    
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="ファイルが見つかりません")
    
    # デバッグ用ログ
    print(f"📥 ファイルリクエスト: {filename}")
    print(f"📝 download_name パラメータ: {download_name}")
    
    # ファイルを返して、バックグラウンドで削除
    async def delete_file(path: Path):
        try:
            if path.exists():
                path.unlink()
                print(f"🗑️ 削除完了: {path}")
        except Exception as e:
            print(f"❌ 削除エラー: {e}")
    
    background_tasks = BackgroundTasks()
    background_tasks.add_task(delete_file, file_path)
    
    # download_nameが指定されていればそれを使用
    if download_name:
        final_filename = unquote(download_name)
        print(f"✅ 使用するファイル名: {final_filename}")
    else:
        final_filename = filename
        print(f"⚠️ download_nameがないため、UUIDを使用: {final_filename}")
    
    # Content-Dispositionヘッダーを設定
    encoded_filename = quote(final_filename)
    headers = {
        'Content-Disposition': f'attachment; filename="{encoded_filename}"; filename*=UTF-8\'\'{encoded_filename}'
    }
    
    print(f"📤 送信ヘッダー: {headers}")
    
    return FileResponse(
        path=file_path,
        media_type='audio/mpeg',
        headers=headers,
        background=background_tasks
    )

@app.delete("/cleanup")
async def cleanup_old_files():
    """手動クリーンアップエンドポイント（全ファイル削除）"""
    try:
        deleted_count = 0
        for file in DOWNLOAD_DIR.glob("*"):
            if file.is_file():
                file.unlink()
                deleted_count += 1
        return {"message": f"クリーンアップ完了: {deleted_count}ファイル削除"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
