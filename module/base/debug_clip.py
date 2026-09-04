"""侵蚀1漏猫复盘 debug 录屏（战后事件处理段）。

原理：ALAS 主线程每次 device.screenshot() 都会经过 module/device/screenshot.py
的截图中枢。本模块把“录屏开关打开期间、按固定最小间隔抽样”的帧转发给后台
编码线程，用 ffmpeg 直接写成 mp4。帧来自 ALAS 自己看到的画面，不额外增加
adb 截图，因此能精确复现“画面里明明有明石却没被识别”这类漏猫场景。

用法（由侵蚀1战后处理代码驱动）：
    clip = clip_start()              # 战后处理开始时打开
    ... 重扫地图 / 处理事件 / 强制移动 ...
    clip_end(keep=bool(事件已解决))  # 结束；keep 决定是否保留成文件

文件输出到 ./log/clips/，一个事件段一个 mp4（默认全部保留、不自动清理）。
"""

import os
import queue
import shutil
import subprocess
import threading
import time

import cv2
import numpy as np

from module.logger import logger

DEFAULT_OUTPUT_DIR = "./log/clips"
# 录屏开关关闭时截图中枢每帧都会调用 clip_feed()，为空对象时开销必须极小
_ACTIVE = None
_FFMPEG_WARNED = False


def _ffmpeg_path():
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


class _ClipRecorder:
    """单个 debug 视频段。

    Args:
        output_dir (str): 视频输出目录。
        fps (float): 编码帧率（也近似播放节奏）。
        min_interval (float): 抽帧最小间隔（秒），控制文件体积。
    """

    def __init__(self, output_dir=DEFAULT_OUTPUT_DIR, fps=2.0, min_interval=0.4):
        self.output_dir = output_dir
        self.fps = fps
        self.min_interval = min_interval
        self._queue = queue.Queue(maxsize=8)
        self._stop_event = threading.Event()
        self._proc = None
        self._writer = None
        self._size = None  # (width, height)，由首帧确定
        self._last_push = 0.0
        self.tmp_path = None

    # ------------------------------------------------------------ 生命周期
    def open(self):
        """启动 ffmpeg 并创建临时文件。成功返回 True。"""
        ffmpeg = _ffmpeg_path()
        if not ffmpeg:
            global _FFMPEG_WARNED
            if not _FFMPEG_WARNED:
                logger.warning("[录屏] 未找到 ffmpeg，录屏功能不可用")
                _FFMPEG_WARNED = True
            return False
        try:
            os.makedirs(self.output_dir, exist_ok=True)
        except OSError as e:
            logger.warning(f"[录屏] 创建输出目录失败: {e}")
            return False

        ts = time.strftime("%Y%m%d_%H%M%S")
        self.tmp_path = os.path.join(self.output_dir, f"_tmp_eh1_{ts}.mp4")
        cmd = [
            ffmpeg, "-y",
            "-f", "rawvideo",
            "-vcodec", "rawvideo",
            "-pix_fmt", "bgr24",
            "-s", "1280x720",
            "-r", str(self.fps),
            "-i", "-",
            "-an",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "28",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            self.tmp_path,
        ]
        try:
            self._proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, bufsize=0,
            )
        except OSError as e:
            logger.warning(f"[录屏] 启动 ffmpeg 失败: {e}")
            return False
        self._writer = threading.Thread(target=self._encode_loop, daemon=True)
        self._writer.start()
        logger.info(f"[录屏] 开始录制临时文件: {self.tmp_path}")
        return True

    def _encode_loop(self):
        """从队列取原始帧写进 ffmpeg，None 表示结束。"""
        while True:
            frame = self._queue.get()
            if frame is None:
                break
            if self._proc is None or self._proc.poll() is not None:
                break
            try:
                data = frame.tobytes()
                self._proc.stdin.write(data)
                self._proc.stdin.flush()
            except Exception:
                break
        try:
            if self._proc is not None and self._proc.stdin:
                self._proc.stdin.close()
        except Exception:
            pass

    def push(self, image):
        """主线程每帧调用；按最小间隔抽帧后交给编码线程。"""
        now = time.perf_counter()
        if now - self._last_push < self.min_interval:
            return
        if self._queue.full():
            return
        self._last_push = now
        try:
            self._queue.put_nowait(np.ascontiguousarray(image).copy())
        except Exception:
            pass

    def finalize(self, keep):
        """正常收尾录制，再决定保留还是删除临时文件。

        Returns:
            str: 保留时返回最终 mp4 路径，丢弃或失败返回 None。
        """
        # 一律先让 ffmpeg 正常写完（发送结束标记并等编码线程排空队列），
        # 避免直接 terminate 导致文件被占用而无法删除。
        if not self._stop_event.is_set():
            self._stop_event.set()
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass
        if self._writer is not None:
            self._writer.join(timeout=8)
        if self._proc is not None:
            try:
                self._proc.wait(timeout=8)
            except Exception:
                try:
                    self._proc.terminate()
                except Exception:
                    pass
                try:
                    self._proc.wait(timeout=3)
                except Exception:
                    pass

        if not keep or not self.tmp_path or not os.path.exists(self.tmp_path):
            if self.tmp_path and os.path.exists(self.tmp_path):
                for _ in range(3):
                    try:
                        os.remove(self.tmp_path)
                        break
                    except OSError:
                        time.sleep(0.2)
            return None

        final_name = f"eh1_clip_{time.strftime('%Y%m%d_%H%M%S')}.mp4"
        final_path = os.path.join(self.output_dir, final_name)
        try:
            os.replace(self.tmp_path, final_path)
        except OSError as e:
            logger.warning(f"[录屏] 保存视频失败: {e}")
            try:
                os.remove(self.tmp_path)
            except OSError:
                pass
            return None
        size_mb = os.path.getsize(final_path) / 1024 / 1024
        logger.info(f"[录屏] 已保存: {final_path} ({size_mb:.1f} MB)")
        return final_path


def clip_start(output_dir=DEFAULT_OUTPUT_DIR, fps=2.0, min_interval=0.4):
    """打开录屏（线程安全：重复调用返回 None）。

    Returns:
        _ClipRecorder: 录制句柄；失败（ffmpeg 缺失/已开启）返回 None。
    """
    global _ACTIVE
    if _ACTIVE is not None:
        return None
    rec = _ClipRecorder(output_dir=output_dir, fps=fps, min_interval=min_interval)
    if not rec.open():
        return None
    _ACTIVE = rec
    return rec


def clip_end(keep=True):
    """结束当前录屏。

    Args:
        keep (bool): 是否把该段保存为 mp4（无事件轮传 False 会自动丢弃）。

    Returns:
        str: 保留时的视频路径；无录制或已丢弃返回 None。
    """
    global _ACTIVE
    rec = _ACTIVE
    _ACTIVE = None
    if rec is None:
        return None
    return rec.finalize(keep=keep)


def clip_feed(image):
    """截图中枢每截一帧调用一次；录屏关闭时近乎零开销。"""
    rec = _ACTIVE
    if rec is None:
        return
    rec.push(image)
