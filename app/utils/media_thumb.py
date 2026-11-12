"""
视频缩略图工具：用 ffmpeg 抽帧存 jpg
依赖：PATH 中可执行 ffmpeg / ffprobe
  - Ubuntu/Debian:  apt install -y ffmpeg
  - macOS:          brew install ffmpeg
"""
from __future__ import annotations

import os
import shutil
import subprocess

from app.core.logger import logger


_FFMPEG_OK: bool | None = None


def ffmpeg_available() -> bool:
    global _FFMPEG_OK
    if _FFMPEG_OK is None:
        _FFMPEG_OK = shutil.which("ffmpeg") is not None
        if not _FFMPEG_OK:
            logger.warning("[Thumb] 未找到 ffmpeg，视频缩略图功能将降级")
    return _FFMPEG_OK


def probe_duration(video_path: str) -> float:
    """返回视频时长（秒），失败返回 0.0"""
    if not shutil.which("ffprobe"):
        return 0.0
    try:
        r = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            capture_output=True, timeout=10,
        )
        if r.returncode == 0:
            s = r.stdout.decode("utf-8", errors="ignore").strip()
            return float(s) if s else 0.0
    except Exception as e:
        logger.warning(f"[Thumb] probe_duration 失败: {e}")
    return 0.0


def make_video_thumb(
    video_path: str,
    out_path: str,
    *,
    at_sec: float = 1.0,
    width: int = 320,
) -> bool:
    """
    从视频第 at_sec 秒抽一帧，缩放到 width 宽，存为 jpg。
    时长过短时自动重试 at=0。
    """
    if not ffmpeg_available():
        return False
    if not os.path.isfile(video_path):
        logger.warning(f"[Thumb] 视频不存在: {video_path}")
        return False

    out_dir = os.path.dirname(out_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    for t in (at_sec, 0.0):
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-ss", f"{t}",
            "-i", video_path,
            "-frames:v", "1",
            "-vf", f"scale={width}:-2",
            "-q:v", "4",
            out_path,
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=20)
        except subprocess.TimeoutExpired:
            logger.warning(f"[Thumb] ffmpeg 超时 at={t}s")
            continue
        except Exception as e:
            logger.error(f"[Thumb] ffmpeg 异常: {e}", exc_info=True)
            continue

        if r.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            logger.info(f"[Thumb] 抽帧成功 at={t}s → {out_path}")
            return True

        err = (r.stderr or b"").decode("utf-8", errors="ignore")[-300:]
        logger.warning(f"[Thumb] ffmpeg 失败 at={t}s rc={r.returncode} err={err}")

    return False