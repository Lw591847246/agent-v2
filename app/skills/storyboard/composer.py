# app/skills/storyboard/composer.py
"""FFmpeg 视频合成器。"""
from __future__ import annotations

import os
import subprocess
from typing import List

from app.core.logger import logger


DEFAULT_FPS = 30
DEFAULT_OUT_W = 720
DEFAULT_OUT_H = 1280


def _run_ffmpeg(cmd: list, timeout: int = 600) -> None:
    r = subprocess.run(
        cmd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    if r.returncode != 0:
        raise RuntimeError(f"FFmpeg 失败 (rc={r.returncode}): {r.stderr[-800:]}")


def _build_single_image_clip(
    image_path: str, duration: float, output_path: str,
    zoom_max: float = 1.15, fps: int = DEFAULT_FPS,
    out_w: int = DEFAULT_OUT_W, out_h: int = DEFAULT_OUT_H,
) -> str:
    total_frames = max(int(duration * fps), 30)
    pre_w = out_w * 2
    pre_h = out_h * 2
    zoom_step = (zoom_max - 1.0) / total_frames
    x_expr = f"iw/2-(iw/zoom/2)+{int(0.3 * pre_w / total_frames)}*on"
    y_expr = f"ih/2-(ih/zoom/2)+{int(0.3 * pre_h / total_frames)}*on"

    vf = (
        f"scale={pre_w}:{pre_h}:force_original_aspect_ratio=increase,"
        f"zoompan=z='min(zoom+{zoom_step:.6f},{zoom_max})':"
        f"x='{x_expr}':y='{y_expr}':"
        f"d={total_frames}:s={out_w}x{out_h}:fps={fps}"
    )

    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", image_path,
        "-t", f"{duration:.3f}", "-vf", vf,
        "-c:v", "libx264", "-crf", "23", "-pix_fmt", "yuv420p",
        output_path,
    ]
    _run_ffmpeg(cmd)
    logger.info(f"[Composer] 单图片段完成: {os.path.basename(output_path)} ({duration:.1f}s)")
    return output_path


def _build_multi_image_clips(
    images: List[str], total_duration: float, workdir: str,
    zoom_max: float = 1.15, fps: int = DEFAULT_FPS,
    out_w: int = DEFAULT_OUT_W, out_h: int = DEFAULT_OUT_H,
) -> List[str]:
    n = len(images)
    seg_dur = total_duration / n
    clips = []
    for i, img_path in enumerate(images):
        clip_path = os.path.join(workdir, f"clip_{i:03d}.mp4")
        total_frames = max(int(seg_dur * fps), 30)
        zoom_step = (zoom_max - 1.0) / total_frames

        zoom_type = i % 3
        if zoom_type == 0:
            vf = f"zoompan=z='min(zoom+{zoom_step:.6f},{zoom_max})':d={total_frames}:s={out_w}x{out_h}:fps={fps}"
        elif zoom_type == 1:
            vf = f"zoompan=z='if(lte(zoom,1.0),{zoom_max},max(1.001,zoom-{zoom_step:.6f}))':d={total_frames}:s={out_w}x{out_h}:fps={fps}"
        else:
            vf = (
                f"scale={out_w*2}:{out_h*2}:force_original_aspect_ratio=increase,"
                f"zoompan=z='{zoom_max}':"
                f"x='iw/2-(iw/zoom/2)':y='ih-(ih/zoom)-(ih/zoom)*0.001*on':"
                f"d={total_frames}:s={out_w}x{out_h}:fps={fps}"
            )

        cmd = [
            "ffmpeg", "-y", "-loop", "1", "-i", img_path,
            "-t", f"{seg_dur:.3f}", "-vf", vf,
            "-c:v", "libx264", "-crf", "23", "-pix_fmt", "yuv420p",
            clip_path,
        ]
        _run_ffmpeg(cmd)
        clips.append(clip_path)
    logger.info(f"[Composer] 多图片段完成: {n} 段，每段 {seg_dur:.1f}s")
    return clips


def _concat_and_mux(
    clips: List[str], audio_path: str, output_path: str, workdir: str,
    fps: int = DEFAULT_FPS, out_w: int = DEFAULT_OUT_W, out_h: int = DEFAULT_OUT_H,
) -> str:
    if not clips:
        raise RuntimeError("没有有效视频片段")

    normalized = []
    for i, clip in enumerate(clips):
        norm_path = os.path.join(workdir, f"norm_{i:03d}.mp4")
        vf_norm = (
            f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
            f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2,"
            f"fps={fps}"
        )
        _run_ffmpeg([
            "ffmpeg", "-y", "-i", clip, "-vf", vf_norm,
            "-c:v", "libx264", "-crf", "23", "-pix_fmt", "yuv420p", norm_path,
        ])
        normalized.append(norm_path)

    concat_list = os.path.join(workdir, "concat_list.txt")
    with open(concat_list, "w", encoding="utf-8") as f:
        for p in normalized:
            abs_p = os.path.abspath(p).replace("\\", "/")
            f.write(f"file '{abs_p}'\n")

    video_concat = os.path.join(workdir, "video_concat.mp4")
    _run_ffmpeg([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", concat_list, "-c", "copy", video_concat,
    ])

    _run_ffmpeg([
        "ffmpeg", "-y",
        "-i", video_concat, "-i", audio_path,
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-af", "aresample=async=1",
        "-shortest", "-movflags", "+faststart",
        output_path,
    ])
    logger.info(f"[Composer] ✅ 合成完成: {output_path}")
    return output_path


def build_storyboard_video(
    images: List[str], audio_path: str, audio_duration: float,
    output_path: str, workdir: str,
    zoom_max: float = 1.15, fps: int = DEFAULT_FPS,
    out_w: int = DEFAULT_OUT_W, out_h: int = DEFAULT_OUT_H,
) -> str:
    if not images:
        raise ValueError("images 不能为空")

    logger.info(f"[Composer] 开始合成，图片数 {len(images)}, 总时长 {audio_duration:.1f}s")

    if len(images) == 1:
        clip = os.path.join(workdir, "single_clip.mp4")
        _build_single_image_clip(images[0], audio_duration, clip, zoom_max, fps, out_w, out_h)
        return _concat_and_mux([clip], audio_path, output_path, workdir, fps, out_w, out_h)
    else:
        clips = _build_multi_image_clips(images, audio_duration, workdir, zoom_max, fps, out_w, out_h)
        return _concat_and_mux(clips, audio_path, output_path, workdir, fps, out_w, out_h)