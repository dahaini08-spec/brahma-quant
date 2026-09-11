#!/usr/bin/env python3
"""
梵天视频生成器 — 全自动链路
文字稿 → Edge-TTS语音 → HeyGen API视频 → MP4

Usage:
    python3 scripts/video_generator.py --text "今日布局..." --output video.mp4
    python3 scripts/video_generator.py --text-file /path/to/script.txt --output video.mp4

Dependencies:
    - edge-tts (pip install edge-tts)
    - mutagen (pip install mutagen)
    - requests (pip install requests)
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import edge_tts
from mutagen.mp3 import MP3

# ── 配置 ──
AVATAR_PATH = Path(__file__).parent.parent / "avatar_zhaobuxuan.jpg"
TTS_VOICE = "zh-CN-YunyangNeural"  # 男声，专业可靠
TTS_RATE = "+0%"  # 语速，+10%加快
TTS_VOLUME = "+0%"  # 音量
OUTPUT_DIR = Path(__file__).parent.parent / "data" / "videos"
TEMP_DIR = Path("/tmp")

# HeyGen API
HEYGEN_API_BASE = "https://api.heygen.com"
HEYGEN_API_KEY = os.environ.get("HEYGEN_API_KEY", "")


async def generate_tts(text: str, output_path: str) -> dict:
    """Step 1: 文字稿 → 语音MP3"""
    print(f"[TTS] 生成语音 → {output_path}")
    communicate = edge_tts.Communicate(
        text=text,
        voice=TTS_VOICE,
        rate=TTS_RATE,
        volume=TTS_VOLUME,
    )
    await communicate.save(output_path)

    audio = MP3(output_path)
    duration = audio.info.length
    size = os.path.getsize(output_path)

    print(f"[TTS] ✅ {duration:.1f}s, {size/1024:.1f}KB")
    return {
        "path": output_path,
        "duration": duration,
        "size": size,
    }


def heygen_create_video(audio_path: str, avatar_path: str, api_key: str) -> dict:
    """Step 2: 语音 + 头像 → HeyGen视频

    HeyGen API流程:
    1. 上传音频文件
    2. 创建视频（选择avatar + 音频）
    3. 轮询视频状态
    4. 下载视频MP4
    """
    import requests

    if not api_key:
        return {"error": "HEYGEN_API_KEY not set"}

    headers = {
        "X-Api-Key": api_key,
        "Content-Type": "application/json",
    }

    # Step 2a: 上传音频
    print(f"[HeyGen] 上传音频...")
    with open(audio_path, "rb") as f:
        upload_resp = requests.post(
            f"{HEYGEN_API_BASE}/v1/audio",
            headers={"X-Api-Key": api_key},
            files={"file": f},
        )
    if upload_resp.status_code != 200:
        return {"error": f"Audio upload failed: {upload_resp.text}"}
    audio_url = upload_resp.json().get("data", {}).get("audio_url", "")
    print(f"[HeyGen] 音频URL: {audio_url}")

    # Step 2b: 创建视频
    print(f"[HeyGen] 创建视频...")
    # 使用photo avatar模式（照片驱动）
    payload = {
        "video_inputs": [
            {
                "character": {
                    "type": "photo",
                    "photo_url": "",  # 需要先上传头像
                },
                "voice": {
                    "type": "audio",
                    "audio_url": audio_url,
                },
            }
        ],
        "dimension": {
            "width": 1080,
            "height": 1920,  # 竖屏适合社交媒体
        },
    }

    # 先上传头像
    print(f"[HeyGen] 上传头像...")
    with open(avatar_path, "rb") as f:
        photo_resp = requests.post(
            f"{HEYGEN_API_BASE}/v1/photo",
            headers={"X-Api-Key": api_key},
            files={"file": f},
        )
    if photo_resp.status_code != 200:
        return {"error": f"Photo upload failed: {photo_resp.text}"}
    photo_url = photo_resp.json().get("data", {}).get("photo_url", "")
    payload["video_inputs"][0]["character"]["photo_url"] = photo_url
    print(f"[HeyGen] 头像URL: {photo_url}")

    create_resp = requests.post(
        f"{HEYGEN_API_BASE}/v1/video/generate",
        headers=headers,
        json=payload,
    )
    if create_resp.status_code != 200:
        return {"error": f"Video creation failed: {create_resp.text}"}

    video_id = create_resp.json().get("data", {}).get("video_id", "")
    print(f"[HeyGen] 视频ID: {video_id}")

    # Step 2c: 轮询视频状态
    print(f"[HeyGen] 等待视频生成...")
    max_wait = 600  # 10分钟超时
    while max_wait > 0:
        time.sleep(10)
        max_wait -= 10

        status_resp = requests.get(
            f"{HEYGEN_API_BASE}/v1/video/status?video_id={video_id}",
            headers={"X-Api-Key": api_key},
        )
        status_data = status_resp.json().get("data", {})
        status = status_data.get("status", "")
        print(f"[HeyGen] 状态: {status}")

        if status == "completed":
            video_url = status_data.get("video_url", "")
            print(f"[HeyGen] ✅ 视频URL: {video_url}")
            return {
                "video_id": video_id,
                "video_url": video_url,
            }
        elif status == "failed":
            return {"error": f"Video generation failed: {status_data}"}

    return {"error": "Video generation timeout"}


def download_video(video_url: str, output_path: str) -> str:
    """Step 3: 下载视频"""
    import requests

    print(f"[Download] 下载视频 → {output_path}")
    resp = requests.get(video_url, stream=True)
    with open(output_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)

    size = os.path.getsize(output_path)
    print(f"[Download] ✅ {size/1024/1024:.1f}MB")
    return output_path


def generate_video(text: str, output_path: str = None, api_key: str = None) -> dict:
    """完整链路：文字 → 语音 → 视频"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = int(time.time())
    audio_path = str(TEMP_DIR / f"tts_{timestamp}.mp3")
    if not output_path:
        output_path = str(OUTPUT_DIR / f"video_{timestamp}.mp4")

    # Step 1: TTS
    tts_result = asyncio.run(generate_tts(text, audio_path))
    if "error" in tts_result:
        return tts_result

    # Step 2: HeyGen
    key = api_key or HEYGEN_API_KEY
    if not key:
        return {
            "error": "HeyGen API Key未设置",
            "tts_path": audio_path,
            "tts_duration": tts_result["duration"],
            "message": "TTS已生成，请设置HEYGEN_API_KEY后重新运行以生成视频",
        }

    heygen_result = heygen_create_video(audio_path, str(AVATAR_PATH), key)
    if "error" in heygen_result:
        return heygen_result

    # Step 3: 下载
    download_video(heygen_result["video_url"], output_path)

    return {
        "success": True,
        "audio_path": audio_path,
        "audio_duration": tts_result["duration"],
        "video_path": output_path,
        "video_id": heygen_result.get("video_id"),
    }


def main():
    parser = argparse.ArgumentParser(description="梵天视频生成器")
    parser.add_argument("--text", type=str, help="文字稿内容")
    parser.add_argument("--text-file", type=str, help="文字稿文件路径")
    parser.add_argument("--output", type=str, default=None, help="输出视频路径")
    parser.add_argument("--api-key", type=str, default=None, help="HeyGen API Key")
    parser.add_argument("--tts-only", action="store_true", help="仅生成TTS语音")
    args = parser.parse_args()

    # 获取文字稿
    if args.text_file:
        with open(args.text_file, "r", encoding="utf-8") as f:
            text = f.read().strip()
    elif args.text:
        text = args.text.strip()
    else:
        print("Error: 需要 --text 或 --text-file")
        sys.exit(1)

    if not text:
        print("Error: 文字稿为空")
        sys.exit(1)

    print(f"═══ 梵天视频生成器 ═══")
    print(f"文字稿长度: {len(text)}字")
    print(f"头像: {AVATAR_PATH}")
    print(f"语音: {TTS_VOICE}")
    print()

    if args.tts_only:
        timestamp = int(time.time())
        audio_path = args.output or f"/tmp/tts_{timestamp}.mp3"
        result = asyncio.run(generate_tts(text, audio_path))
        print(f"\n✅ TTS生成完成: {result}")
        return

    result = generate_video(text, args.output, args.api_key)
    print(f"\n{'═' * 40}")
    print(json.dumps(result, indent=2, ensure_ascii=False))

    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
