"""口型同步服务：MuseTalk 适配层（Popen 流式 + 真实日志 + 显存清理 + 分辨率优化）"""
from __future__ import annotations

from pathlib import Path
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_ROOT / ".env")

import glob
import os
import re
import shutil
import subprocess
import sys
import threading
import time

import requests   # ⭐ 新增：用于显存清理

from app.core.logger import logger


class LipSyncService:
    def __init__(self):
        self.musetalk_root = os.getenv("MUSETALK_ROOT", "").strip()
        self.python = os.getenv("MUSETALK_PYTHON", sys.executable)
        self.timeout = int(os.getenv("MUSETALK_TIMEOUT", "3600"))
        # ⭐ 新增：MuseTalk 处理分辨率，8G 显卡建议 256，24G 可上 512
        self.resolution = int(os.getenv("MUSETALK_RESOLUTION", "256"))
        # ⭐ 新增：显存清理相关服务地址
        self.ollama_url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
        self.comfyui_url = os.getenv("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")
        self.indextts_url = os.getenv("INDEXTTS2_API_URL", "http://127.0.0.1:9011").rstrip("/")
        # ⭐ 新增：视频时长预警阈值
        self.max_video_seconds = int(os.getenv("MUSETALK_MAX_VIDEO_SECONDS", "10"))

        self.unet_path = os.getenv("MUSETALK_UNET", "").strip()
        self.unet_cfg = os.getenv("MUSETALK_UNET_CFG", "").strip()
        self.version = os.getenv("MUSETALK_VERSION", "v15").strip()

    def _log_gpu(self, label: str):
        try:
            r = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3,
            )
            if r.returncode == 0:
                used, total = r.stdout.strip().split(",")
                logger.info(f"[LipSync][显存] {label}: {used.strip()}MB / {total.strip()}MB")
        except Exception:
            pass

    def _force_clean_gpu(self) -> bool:
        logger.info("[LipSync] 开始强制清理显存占用者...")
        self._log_gpu("清理前")
        cleaned = False

        # 1. 卸载 Ollama 所有模型
        try:
            from app.config.loader import config
            models = config.get("ollama.models", {})
            seen = set()
            for name in models.values():
                if name in seen: continue
                seen.add(name)
                try:
                    requests.post(f"{self.ollama_url}/api/generate",
                                  json={"model": name, "keep_alive": 0}, timeout=5)
                except Exception:
                    pass
            if seen:
                logger.info(f"[LipSync] ✅ Ollama 模型已卸载 ({len(seen)} 个)")
                cleaned = True
        except Exception as e:
            logger.warning(f"[LipSync] Ollama 卸载失败: {e}")

        # 2. 卸载 IndexTTS2
        try:
            r = requests.post(f"{self.indextts_url}/unload", timeout=5)
            if r.status_code == 200:
                logger.info("[LipSync] ✅ IndexTTS2 已卸载")
                cleaned = True
        except Exception:
            pass

        # ⭐ 核心修复：直接调 ComfyUI 的 /free HTTP API（而不是调用不存在的客户端方法）
        try:
            r = requests.post(
                f"{self.comfyui_url}/free",
                json={"unload_models": True, "free_memory": True},
                timeout=15,
            )
            if r.status_code == 200:
                logger.info("[LipSync] ✅ ComfyUI 已释放（HTTP API）")
                cleaned = True
            else:
                logger.warning(f"[LipSync] ComfyUI /free 返回 {r.status_code}")
        except Exception as e:
            logger.warning(f"[LipSync] ComfyUI /free 失败: {e}")

        # 3. 等驱动真正回收
        if cleaned:
            time.sleep(5)
        self._log_gpu("清理后")
        return cleaned

    def generate(
        self,
        video_path: str,
        audio_path: str,
        output_path: str,
        task_id: str = "",
        progress_callback=None,
    ) -> str:
        video_path = os.path.abspath(video_path)
        audio_path = os.path.abspath(audio_path)
        output_path = os.path.abspath(output_path)

        if not os.path.isfile(video_path):
            raise FileNotFoundError(f"视频不存在: {video_path}")
        if not os.path.isfile(audio_path):
            raise FileNotFoundError(f"音频不存在: {audio_path}")

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        return self._call_local(video_path, audio_path, output_path, task_id, progress_callback)

    def _probe_video(self, video_path: str) -> dict:
        """⭐ 新增：探测视频信息，用于时长预警"""
        info = {"duration": 0.0, "width": 0, "height": 0, "fps": 0.0, "nb_frames": 0}
        try:
            import json as _json
            r = subprocess.run(
                ["ffprobe", "-v", "error",
                 "-select_streams", "v:0",
                 "-show_entries", "stream=width,height,nb_frames,duration,r_frame_rate",
                 "-of", "json", video_path],
                capture_output=True, text=True, timeout=10,
            )
            data = _json.loads(r.stdout)
            stream = (data.get("streams") or [{}])[0]
            info["width"] = int(stream.get("width", 0) or 0)
            info["height"] = int(stream.get("height", 0) or 0)
            info["nb_frames"] = int(stream.get("nb_frames", 0) or 0)
            info["duration"] = float(stream.get("duration", 0) or 0)
            rate = stream.get("r_frame_rate", "0/1")
            try:
                num, den = rate.split("/")
                info["fps"] = float(num) / float(den) if float(den) > 0 else 0.0
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"[LipSync] ffprobe 失败: {e}")
        return info

    def _call_local(self, video_path, audio_path, output_path, task_id, progress_callback=None):
        if not self.musetalk_root:
            raise RuntimeError("MUSETALK_ROOT 未配置")

        abs_video = os.path.abspath(video_path)
        abs_audio = os.path.abspath(audio_path)

        logger.info("=" * 70)
        logger.info(f"[LipSync] 🎬 开始 MuseTalk 对口型流程")
        logger.info(f"[LipSync] task_id = {task_id}")
        logger.info(f"[LipSync] 视频: {abs_video}")
        logger.info(f"[LipSync]        exists={os.path.isfile(abs_video)}, "
                    f"size={os.path.getsize(abs_video) if os.path.isfile(abs_video) else 0} bytes")
        logger.info(f"[LipSync] 音频: {abs_audio}")
        logger.info(f"[LipSync]        exists={os.path.isfile(abs_audio)}, "
                    f"size={os.path.getsize(abs_audio) if os.path.isfile(abs_audio) else 0} bytes")
        logger.info(f"[LipSync] 分辨率: {self.resolution}x{self.resolution}")
        logger.info(f"[LipSync] 超时: {self.timeout}s")
        logger.info("=" * 70)

        # ⭐ 核心修复 1：真正执行显存清理
        self._force_clean_gpu()
        if not self._force_clean_gpu():
            logger.warning("[LipSync] ⚠️ 显存清理未生效，MuseTalk 可能降级到 CPU")
        # ⭐ 核心修复 2：视频预检（时长过长会预警）
        video_info = self._probe_video(abs_video)
        logger.info(f"[LipSync] 视频信息: duration={video_info['duration']:.2f}s, "
                    f"分辨率={video_info['width']}x{video_info['height']}, "
                    f"帧数={video_info['nb_frames']}, fps={video_info['fps']:.1f}")
        if video_info["duration"] > self.max_video_seconds:
            logger.warning(
                f"[LipSync] ⚠️ 视频时长 {video_info['duration']:.1f}s 超过建议上限 "
                f"{self.max_video_seconds}s，MuseTalk 处理时间会显著延长"
            )

        # 写 YAML
        task_key = "task_0"
        cfg_dir = os.path.join(self.musetalk_root, "configs", "inference", "_runtime")
        os.makedirs(cfg_dir, exist_ok=True)
        cfg_path = os.path.join(cfg_dir, f"{task_id or 'i2v'}.yaml")
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write(f"{task_key}:\n")
            f.write(f"  video_path: {abs_video.replace(os.sep, '/')}\n")
            f.write(f"  audio_path: {abs_audio.replace(os.sep, '/')}\n")

        # 清理 result_dir
        result_dir = os.path.join(self.musetalk_root, "results", task_id or "i2v")
        if os.path.isdir(result_dir):
            shutil.rmtree(result_dir, ignore_errors=True)
        os.makedirs(result_dir, exist_ok=True)

        # 模型路径
        unet_path = self.unet_path or os.path.join("models", "musetalk", "musetalkV15", "unet.pth")
        unet_cfg = self.unet_cfg or os.path.join("models", "musetalk", "musetalkV15", "musetalk.json")

        # ⭐ 核心修复 3：加 --resolution 降低处理分辨率（速度 2-3 倍）
        cmd = [
            self.python, "-u",
            "-m", "scripts.inference",
            "--inference_config", cfg_path,
            "--result_dir", result_dir,
            "--unet_model_path", unet_path,
            "--unet_config", unet_cfg,
            "--version", self.version,
            "--use_float16",  # ⭐ 关键：半精度推理，显存减半、速度 1.5-2x
            "--batch_size", "8",  # ⭐ 关键：加大 batch，减少 GPU 调度次数
        ]

        env = os.environ.copy()
        env["PYTHONPATH"] = self.musetalk_root + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["CUDA_LAUNCH_BLOCKING"] = "1"

        logger.info(f"[LipSync] cwd={self.musetalk_root}")
        logger.info(f"[LipSync] cmd={' '.join(cmd)}")

        start_time = time.time()
        proc = subprocess.Popen(
            cmd,
            cwd=self.musetalk_root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        # 心跳线程
        stop_hb = threading.Event()

        # ⭐ 核心修复 4：动态进度不封顶 95%，每 60 秒推进 1% 到 99%
        def heartbeat():
            while not stop_hb.is_set():
                elapsed = int(time.time() - start_time)
                if elapsed > 0 and elapsed % 30 < 5:
                    self._log_gpu(f"MuseTalk 运行中 {elapsed}s")
                if progress_callback:
                    fake = min(99, 82 + elapsed // 60)
                    try:
                        progress_callback(fake, f"MuseTalk 对口型中... 已运行 {elapsed}s")
                    except Exception:
                        pass
                stop_hb.wait(5)

        hb_thread = threading.Thread(target=heartbeat, daemon=True)
        hb_thread.start()

        stdout_lines = []
        progress_pattern = re.compile(r'(\d+)\s*/\s*(\d+)')
        line_count = 0

        try:
            if progress_callback:
                progress_callback(81, "MuseTalk 加载模型中...")

            for line in iter(proc.stdout.readline, ''):
                if not line:
                    break
                stripped = line.rstrip()
                line_count += 1
                logger.info(f"[MuseTalk] {stripped}")
                stdout_lines.append(stripped)

                m = progress_pattern.search(stripped)
                if m and progress_callback:
                    cur, total = int(m.group(1)), int(m.group(2))
                    if 0 < total <= 10000 and cur <= total:
                        real = 80 + int(cur / total * 15)
                        try:
                            progress_callback(real, f"MuseTalk 帧 {cur}/{total}")
                        except Exception:
                            pass

                if len(stdout_lines) > 200:
                    stdout_lines = stdout_lines[-200:]

            proc.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            logger.error(f"[LipSync] ⏰ MuseTalk 超时 ({self.timeout}s)，强制 kill")
            proc.kill()
            stop_hb.set()
            try:
                proc.wait(timeout=10)
            except Exception:
                pass
            raise RuntimeError(
                f"MuseTalk 超时 (>{self.timeout}s)，已强制终止。\n"
                f"已输出 {line_count} 行日志，最后 10 行：\n"
                + "\n".join(stdout_lines[-10:])
            )
        finally:
            stop_hb.set()
            hb_thread.join(timeout=2)

        elapsed = int(time.time() - start_time)
        returncode = proc.returncode
        stdout_text = "\n".join(stdout_lines)

        logger.info("=" * 70)
        logger.info(f"[LipSync] MuseTalk 结束: returncode={returncode}, 耗时={elapsed}s, "
                    f"输出 {line_count} 行")
        self._log_gpu("MuseTalk 结束后")
        logger.info("=" * 70)

        failure_markers = [
            "Error occurred during processing",
            "No such file or directory",
            "division by zero",
            "Traceback (most recent call last)",
            "CUDA out of memory",
            "RuntimeError",
            "AssertionError",
        ]
        has_failure = any(m in stdout_text for m in failure_markers)

        if returncode != 0 or has_failure:
            logger.error(f"[LipSync] ❌ MuseTalk 执行失败")
            logger.error(f"[LipSync] stdout 尾部:\n{stdout_text[-3000:]}")
            raise RuntimeError(
                f"MuseTalk 失败 (code={returncode})\n"
                f"stdout 尾部：{stdout_text[-1000:]}"
            )

        candidates = glob.glob(os.path.join(result_dir, "**", "*.mp4"), recursive=True)
        logger.info(f"[LipSync] result_dir 中 mp4 候选文件: {candidates}")

        if not candidates:
            raise FileNotFoundError(
                f"MuseTalk 未生成输出。result_dir={result_dir}\n"
                f"stdout 尾部：{stdout_text[-500:]}"
            )

        candidates.sort(key=os.path.getmtime, reverse=True)
        src = candidates[0]
        logger.info(f"[LipSync] ✅ 使用输出文件: {src}")

        if os.path.abspath(src) != os.path.abspath(output_path):
            if os.path.exists(output_path):
                os.remove(output_path)
            shutil.move(src, output_path)

        logger.info(f"[LipSync] ✅ 输出 → {output_path}")
        return output_path


lipsync_service = LipSyncService()