#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能去码字幕工具箱 v1.2
功能：视频去码（LADA / JASNA）、字幕生成（Faster-Whisper）、字幕合成（FFmpeg / MKVToolNix）
"""

import sys
import os
import json
import subprocess
import threading
import time
import re
from datetime import datetime
from pathlib import Path

# ─── 控制台输出（强制 UTF-8 代码页，统一输出）───
def _force_console_utf8():
    """将 Windows 控制台设为 UTF-8 代码页，避免中文乱码"""
    if sys.platform == 'win32':
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.SetConsoleOutputCP(65001)
            k32.SetConsoleCP(65001)
        except:
            pass

_force_console_utf8()

# ─── 工具进程输出编码（LADA/JASNA 进度 = ANSI 代码页，中文 Win = GBK 936）───
def _get_process_encoding():
    """获取系统 ANSI 代码页，用于解码工具进程 stdout"""
    if sys.platform == 'win32':
        try:
            import ctypes
            acp = ctypes.windll.kernel32.GetACP()
            return f'cp{acp}'  # 中文系统 = cp936 (GBK)
        except:
            pass
    return 'utf-8'

_PROCESS_ENCODING = _get_process_encoding()

# ─── 文件日志模块（持久化到 logs/ 目录，按日期轮转）───
import logging
import logging.handlers

_log_file_handle = None  # 全局文件句柄，MainWindow 和 WorkerThread 共享

def setup_file_logging():
    """初始化文件日志：logs/toolbox_YYYYMMDD.log，UTF-8，自动轮转"""
    global _log_file_handle
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"toolbox_{datetime.now().strftime('%Y%m%d')}.log"
    _log_file_handle = open(str(log_path), 'a', encoding='utf-8', buffering=1)  # 行缓冲
    return str(log_path)

def file_log(msg: str):
    """同时写入 GUI（通过 log_signal）和文件"""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    if _log_file_handle:
        try:
            _log_file_handle.write(line)
            _log_file_handle.flush()
        except Exception:
            pass  # 文件写入失败不影响主流程

def close_file_logging():
    """关闭日志文件句柄"""
    global _log_file_handle
    if _log_file_handle:
        try:
            _log_file_handle.close()
        except Exception:
            pass
        _log_file_handle = None

def console_print(text="", end="\n", flush=False):
    """输出到控制台（控制台已强制 UTF-8，直接写 UTF-8 字节）"""
    try:
        encoded = (text + end).encode('utf-8', errors='replace')
        sys.stdout.buffer.write(encoded)
    except:
        pass

try:
    from PyQt6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QPushButton, QLabel, QTextEdit, QComboBox, QGroupBox,
        QFormLayout, QLineEdit, QFileDialog, QTableWidget, QTableWidgetItem,
        QHeaderView, QCheckBox, QProgressBar, QSpinBox, QSlider,
        QRadioButton, QButtonGroup, QSplitter, QFrame, QScrollArea,
        QTabWidget, QDialog, QDialogButtonBox
    )
    from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QEvent
    from PyQt6.QtGui import QFont, QColor, QPalette
except ImportError:
    print("错误：需要安装 PyQt6")
    print("请运行: pip install PyQt6")
    sys.exit(1)


# ==================== 路径配置（默认值，可从 config.json 覆盖） ====================
BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"

DEFAULT_TOOL_PATHS = {
    "lada": "D:/soft/lada-v0.11.0/lada-cli.exe",
    "jasna": "D:/soft/jasna-v0.7.2/jasna.exe",
    "ffmpeg": "D:/soft/ffmpeg-8.0-full_build/bin/ffmpeg.exe",
    "mkvmerge": "D:/soft/mkvtoolnix/mkvmerge.exe",
    "whisper": "E:/faster_whisper_transwithai_windows_cu122-chickenrice.zip/infer.exe"
}

# 默认工作目录
DEFAULT_INPUT_DIR = Path("C:/Users/Administrator/Desktop/work/input")
DEFAULT_OUTPUT_DIR = Path("C:/Users/Administrator/Desktop/work/output")


# ==================== 工具信息 ====================
STATUS_TOOLS = ["LADA", "JASNA", "Whisper", "FFmpeg", "MKVToolNix"]


# ==================== 工具检测函数 ====================
def check_tool_ready(exe_path_str):
    """检测单个工具是否就绪"""
    try:
        exe_path = Path(exe_path_str)
        if not exe_path.exists():
            return False, "文件不存在"
        # jasna v0.7.2+ 无命令行参数会启动 GUI，加 --version 避免弹窗
        cmd = [str(exe_path)]
        stem = exe_path.stem.lower()
        if stem == 'jasna':
            cmd.append('--version')
        r = subprocess.run(
            cmd,
            capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=5
        )
        return True, "就绪"
    except FileNotFoundError:
        return False, "未找到"
    except subprocess.TimeoutExpired:
        return True, "就绪"
    except Exception as e:
        return False, str(e)[:20]


# ==================== 工作线程 ====================
class WorkerThread(QThread):
    """后台工作线程 - 顺序执行：去码 → 字幕 → 合成"""
    # 工具级别互斥锁：确保同一个软件不会同时开2个
    _demark_lock = threading.Lock()
    _whisper_lock = threading.Lock()
    _compose_lock = threading.Lock()
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
    file_progress_signal = pyqtSignal(int, int)  # current, total
    file_status_signal = pyqtSignal(int, str)     # (file_index, status)
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.running = True
        self._file_results = []  # 记录每个文件的处理结果
        self.gpu_config = config.get("gpu_config", {})
        self.gpu_mode = self.gpu_config.get("mode", "8g_serial")
        self.gpu_safety = self.gpu_config.get("safety_margin_mb", 2048)
        self.gpu_temp_limit = self.gpu_config.get("temp_limit", 85)
        self.gpu_cooldown = self.gpu_config.get("temp_cooldown", 60)

    def log(self, msg, **kwargs):
        if self.running:
            self.log_signal.emit(msg)
            file_log(msg)  # 直接写文件，避免信号丢失

    def _wait_for_gpu(self, label="GPU 操作"):
        """等待显存足够，返回 True=可以继续, False=被停止"""
        if not self.running:
            return False
        while self.running:
            info = get_gpu_memory_info()
            if not info:
                return True  # 无法检测则放行
            total, available = info
            if available >= self.gpu_safety:
                return True
            mb_needed = self.gpu_safety - available
            self.log(f"⏳ {label}: 等待显存释放 (需要 {mb_needed}MB)...")
            time.sleep(5)
        return False

    def _cool_down(self):
        """温度过高时等待降温"""
        if not self.running:
            return False
        temp = get_gpu_temperature()
        if temp is None:
            return True
        waited = 0
        while self.running and temp is not None and temp >= self.gpu_temp_limit:
            if waited == 0:
                self.log(f"🌡️ GPU {temp}°C ≥ {self.gpu_temp_limit}°C，等待降温 (最多 {self.gpu_cooldown}s)...")
            time.sleep(5)
            waited += 5
            temp = get_gpu_temperature()
            if waited >= self.gpu_cooldown:
                self.log(f"⚠ 降温等待超时 ({self.gpu_cooldown}s)，强制继续")
                break
        if waited > 0 and self.running:
            self.log(f"✓ GPU 温度已降至 {temp}°C")
        return self.running

    def run(self):
        try:
            files = self.config.get('input_files', [])
            self._file_results = []  # 每次运行重置
            total_files = len(files)
            output_dir = Path(self.config.get('output_dir', ''))

            # 所有文件设为"准备中"
            for i in range(total_files):
                self.file_status_signal.emit(i, "ready")

            # 检查现有输出，记录哪些文件会被跳过
            do_demark = self.config.get('do_demark', False)
            do_subtitle = self.config.get('do_subtitle', False)
            do_compose = self.config.get('do_compose', False)
            for i, f in enumerate(files):
                name = Path(f).stem
                # 全局跳过：-UC 存在则整个文件已完成
                if do_compose and (output_dir / f"{name}-UC.mp4").exists():
                    self.log(f"  ↪ [{i+1}] {Path(f).name}: 合成已完成，跳过全部步骤")
                    continue
                skipped = []
                if do_demark and (output_dir / f"{name}-U.mp4").exists():
                    skipped.append("去码")
                srt_exists = (output_dir / f"{name}.srt").exists() or (output_dir / f"{name}-U.srt").exists()
                if do_subtitle and srt_exists:
                    skipped.append("字幕")
                if skipped:
                    self.log(f"  ↪ [{i+1}] {Path(f).name}: 跳过已有 {'+'.join(skipped)}")
                else:
                    self.log(f"  → [{i+1}] {Path(f).name}: 待处理")

            self._run_serial(files, do_demark, do_subtitle, do_compose)

            if not self.running:
                self.finished_signal.emit(True, "__STOPPED__")
            else:
                any_failed = any(not s for s in self._file_results)
                if any_failed:
                    self.progress_signal.emit(100)
                    self.finished_signal.emit(False, "部分文件处理失败（详见日志）")
                else:
                    self.progress_signal.emit(100)
                    self.finished_signal.emit(True, "所有文件处理完成！")

        except Exception as e:
            import traceback
            self.log(f"✗ 发生错误: {str(e)}")
            self.log(traceback.format_exc())
            self.finished_signal.emit(False, str(e))

    def _run_serial(self, files, do_demark, do_subtitle, do_compose):
        """串行模式：逐个文件处理"""
        total_files = len(files)
        for file_idx, input_file in enumerate(files):
            if not self.running:
                self.log("▸ 用户已停止处理")
                break
            output_dir = Path(self.config.get('output_dir', ''))
            output_dir.mkdir(parents=True, exist_ok=True)
            result = self._process_one_file(file_idx, total_files, input_file, output_dir,
                                            do_demark, do_subtitle, do_compose)
            self._file_results.append(bool(result))
            if not result:
                return
            progress_base = int((file_idx + 1) / total_files * 100)
            self.progress_signal.emit(min(progress_base, 100))
            self.log(f"✓ 文件处理完成: {Path(input_file).name}")

    def _run_pipeline(self, files, do_demark, do_subtitle, do_compose):
        """并行流水线模式
        8G串行 → _run_serial：完全串行，逐文件处理
        12G双卡并行 → _run_pipeline(max_workers=2)：多文件并发，工具锁避免同软件双开
        16G+全并行 → _run_pipeline(max_workers=3)：多文件并发，工具锁避免同软件双开
        """
        total_files = len(files)
        finished_count = [0]
        lock = threading.Lock()

        def process_worker(file_idx, input_file):
            if not self.running:
                return
            output_dir = Path(self.config.get('output_dir', ''))
            output_dir.mkdir(parents=True, exist_ok=True)
            ok = self._process_one_file(file_idx, total_files, input_file, output_dir,
                                        do_demark, do_subtitle, do_compose)
            with lock:
                self._file_results.append(bool(ok))
                finished_count[0] += 1
                progress_base = int(finished_count[0] / total_files * 100)
            self.progress_signal.emit(min(progress_base, 100))
            self.log(f"✓ 文件处理完成: {Path(input_file).name}")

        threads = []
        for idx, f in enumerate(files):
            t = threading.Thread(target=process_worker, args=(idx, f), daemon=True)
            threads.append(t)
            t.start()
            # 多文件并行，工具锁确保同一软件不会同时开2个
            max_workers = 2 if self.gpu_mode == "12g_dc_parallel" else 3
            while len([th for th in threads if th.is_alive()]) >= max_workers:
                time.sleep(1)
                if not self.running:
                    break

        for t in threads:
            t.join(timeout=10)

    def _process_one_file(self, file_idx, total_files, input_file, output_dir,
                          do_demark, do_subtitle, do_compose):
        """处理单个文件：去码 → 字幕 → 合成（含 GPU 等待/降温+断点续传）"""
        file_start = time.time()
        self.log(f"\n{'='*50}")
        self.log(f"处理文件 [{file_idx+1}/{total_files}]: {Path(input_file).name}")
        self.log(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.log(f"{'='*50}")

        name = Path(input_file).stem
        current_video = input_file
        demark_output = None

        # ⭐ 全局断点：-UC.mp4 存在则整个文件跳过（不管中间文件是否被删）
        if do_compose and (output_dir / f"{name}-UC.mp4").exists():
            self.log(f"  ↪ 合成输出 {name}-UC.mp4 已存在，跳过整个文件")
            self.file_status_signal.emit(file_idx, "done")
            return True

        # ── 步骤1：去码 ──
        if do_demark and self.running:
            # 断点：检查去码输出是否已存在
            demark_out = str(output_dir / f"{name}-U.mp4")
            if Path(demark_out).exists():
                self.log(f"  ↪ 去码输出已存在，跳过: {name}-U.mp4")
                current_video = demark_out
            else:
                step_start = time.time()
                self.log(f"\n▶ 步骤1/3: 去码处理")
                self._cool_down()
                if not self._wait_for_gpu("去码"):
                    return False
                with self._demark_lock:
                    self.file_status_signal.emit(file_idx, "demarking")
                    self.log("  🛠️ 开始去码（工具锁已获取）")
                    demark_output = self._run_demark(input_file, output_dir)
                step_elapsed = time.time() - step_start
                if not demark_output:
                    self.log(f"  ✗ 去码失败: {Path(input_file).name} (耗时 {step_elapsed:.1f}s)")
                    self.file_status_signal.emit(file_idx, "failed")
                    return False
                self.log(f"  ✓ 去码完成 (耗时 {step_elapsed:.1f}s)")
                current_video = demark_output

        # ── 步骤2：生成字幕 ──
        subtitle_file = None
        if do_subtitle and self.running:
            # 断点：检查字幕文件是否已存在
            srt_path = output_dir / f"{Path(current_video).stem}.srt"
            # 如果 current_video 是 -U.mp4 且 -U.srt 不存在，尝试用原始文件名查找
            if not srt_path.exists() and Path(current_video).stem.endswith('-U'):
                alt_srt = output_dir / f"{name}.srt"
                if alt_srt.exists():
                    srt_path = alt_srt
                    self.log(f"  ↪ 字幕已存在（原始文件名），跳过: {alt_srt.name}")
            if srt_path.exists():
                self.log(f"  ↪ 字幕已存在，跳过: {srt_path.name}")
                subtitle_file = str(srt_path)
            else:
                step_start = time.time()
                self.log(f"\n▶ 步骤2/3: 生成字幕")
                self._cool_down()
                if not self._wait_for_gpu("字幕生成"):
                    return False
                with self._whisper_lock:
                    self.file_status_signal.emit(file_idx, "subtitling")
                    self.log("  🛠️ 开始字幕生成（工具锁已获取）")
                    subtitle_file = self._run_whisper(current_video, output_dir)
                step_elapsed = time.time() - step_start
                if not subtitle_file:
                    self.log(f"  ✗ 字幕生成失败: {Path(input_file).name} (耗时 {step_elapsed:.1f}s)")
                    self.file_status_signal.emit(file_idx, "failed")
                    return False
                self.log(f"  ✓ 字幕生成完成 (耗时 {step_elapsed:.1f}s)")

        # ── 步骤3：合成字幕 ──
        if do_compose and self.running:
            out_name = f"{name}-UC.mp4"
            # 断点优先检查：-UC 已存在则直接跳过整个合成步骤
            if (output_dir / out_name).exists():
                self.log(f"  ↪ 合成输出已存在，跳过: {out_name}")
            else:
                step_start = time.time()
                self.log(f"\n▶ 步骤3/3: 合成字幕")
                if not subtitle_file:
                    subtitle_file = self._find_subtitle(output_dir, Path(current_video).stem)
                    # 也尝试用原始文件名查找（兼容不同命名历史）
                    if not subtitle_file and name != Path(current_video).stem:
                        subtitle_file = self._find_subtitle(output_dir, name)
                if subtitle_file:
                    with self._compose_lock:
                        self.file_status_signal.emit(file_idx, "composing")
                        self.log("  🛠️ 开始合成（工具锁已获取）")
                        result = self._run_compose(current_video, subtitle_file, output_dir)
                    step_elapsed = time.time() - step_start
                    if not result:
                        self.log(f"  ✗ 合成失败: {Path(input_file).name} (耗时 {step_elapsed:.1f}s)")
                        self.file_status_signal.emit(file_idx, "failed")
                        return False
                    self.log(f"  ✓ 合成完成 (耗时 {step_elapsed:.1f}s)")
                else:
                    self.log("⚠ 未找到字幕文件，跳过合成")

        file_elapsed = time.time() - file_start
        self.log(f"  📊 文件总耗时: {file_elapsed:.1f}s ({file_elapsed/60:.1f}min)")
        self.file_status_signal.emit(file_idx, "done")
        return True

    # ─── 去码 ───
    def _run_demark(self, input_file, output_dir):
        """执行去码处理"""
        engine = self.config.get('demark_engine', 'lada')
        self.log(f"  引擎: {engine.upper()}")
        input_path = Path(input_file)
        output_path = Path(output_dir)
        tp = self.config.get("tool_paths", DEFAULT_TOOL_PATHS)

        cwd = None
        if engine == 'lada':
            lada_exe = tp.get("lada", DEFAULT_TOOL_PATHS["lada"])
            output_file = str(output_path / f"{input_path.stem}-U.mp4")
            cmd = [lada_exe, "--input", str(input_path), "--output", output_file, "--fp16"]
            extra = self.config.get('lada_params', {})
            if extra.get('device'): cmd.extend(["--device", extra['device']])
            # 检测模型 --mosaic-detection-model (default: v4-fast)
            det = extra.get('detection_model', 'v4-fast')
            if det and det.lower() != 'none': cmd.extend(["--mosaic-detection-model", det])
            # 修复模型 --mosaic-restoration-model
            if extra.get('model'): cmd.extend(["--mosaic-restoration-model", extra['model']])
            # 面部检测 --detect-face-mosaics
            if extra.get('face_detection'): cmd.append("--detect-face-mosaics")
            # 切片帧数 --max-clip-length (default: 180)
            tile = extra.get('tile_size', 180)
            if tile: cmd.extend(["--max-clip-length", str(tile)])
            # 编码预设 --encoding-preset
            if extra.get('preset'): cmd.extend(["--encoding-preset", extra['preset']])
            # 自定义参数
            if extra.get('extra_args'): cmd.extend(extra['extra_args'].split())
        elif engine == 'jasna':
            jasna_exe = tp.get("jasna", DEFAULT_TOOL_PATHS["jasna"])
            jasna_dir = str(Path(jasna_exe).parent)
            cwd = jasna_dir
            output_file = str(output_path / f"{input_path.stem}-U.mp4")
            cmd = [jasna_exe, "--input", str(input_path), "--output", output_file, "--fp16"]
            jp = self.config.get('jasna_params', {})
            if jp.get('device'): cmd.extend(["--device", jp['device']])
            # 检测模型 --detection-model (default: rfdetr-v5)
            det = jp.get('detection_model', 'rfdetr-v5')
            if det and det.lower() != 'none': cmd.extend(["--detection-model", det])
            # 检测阈值 --detection-score-threshold
            thresh = jp.get('detection_threshold', 0.25)
            if thresh: cmd.extend(["--detection-score-threshold", str(thresh)])
            # 切片帧数 --max-clip-size
            cmd.extend(["--max-clip-size", str(jp.get('max_clip_size', 90))])
            # 时间重叠 --temporal-overlap
            if jp.get('temporal_overlap'): cmd.extend(["--temporal-overlap", str(jp['temporal_overlap'])])
            # 淡入淡出 --enable-crossfade
            if jp.get('fade', True): cmd.append("--enable-crossfade")
            else: cmd.append("--no-enable-crossfade")
            # 降噪强度 --denoise (none/low/medium/high)
            dn_map = {"无":"none","低":"low","中":"medium","高":"high"}
            dn_val = jp.get('denoise_strength', 'medium')
            if dn_val in dn_map: dn_val = dn_map[dn_val]
            if dn_val and dn_val != 'none': cmd.extend(["--denoise", dn_val])
            # 降噪时机 --denoise-step (after_primary / after_secondary)
            tm_map = {"主修复后":"after_primary","主修复前":"after_primary","同步":"after_secondary",
                      "post_main":"after_primary","pre_main":"after_primary","sync":"after_secondary"}
            tm_val = jp.get('denoise_timing', 'post_main')
            if tm_val in tm_map: tm_val = tm_map[tm_val]
            cmd.extend(["--denoise-step", tm_val])
            # 二次修复 --secondary-restoration + RTX/TVAI 子参数
            sec_map = {"无":"none","Unet-4x":"unet-4x","TVAI":"tvai","RTX Super RES":"rtx-super-res"}
            sec_val = jp.get('secondary_restoration', 'rtx-super-res')
            if sec_val in sec_map: sec_val = sec_map[sec_val]
            if sec_val != 'none':
                cmd.extend(["--secondary-restoration", sec_val])
                if sec_val == 'rtx-super-res':
                    cmd.extend(["--rtx-scale", str(jp.get('secondary_scale', 4))])
                    cmd.extend(["--rtx-quality", jp.get('secondary_quality', 'high')])
                    cmd.extend(["--rtx-denoise", jp.get('secondary_denoise', 'medium')])
                    cmd.extend(["--rtx-deblur", jp.get('secondary_deblur', 'medium')])
            # 编码设置 --codec
            cmd.extend(["--codec", jp.get('codec', 'hevc')])
            # 质量 CQ --encoder-settings
            cq = jp.get('cq', 22)
            if cq: cmd.extend(["--encoder-settings", f"cq={cq}"])
        else:
            self.log("  错误: 未知引擎")
            return None

        self.log(f"  完整命令: {' '.join(cmd)}")
        self.log(f"  输出: {output_file}")
        process = self._run_process(cmd, "去码", 0, capture_stdout=False, cwd=cwd)
        if process:
            rc = process.returncode
            self.log(f"  退出码: {rc}")
            if rc == 0:
                self.log("  ✓ 去码完成")
                if Path(output_file).exists():
                    return output_file
                self.log(f"  ⚠ 去码完成但输出文件不存在: {output_file}")
                return None
            else:
                self.log(f"  ✗ 去码异常退出 (退出码={rc})")
        else:
            self.log("  ✗ 去码进程启动失败或已停止")
        return None

    # ─── Whisper 字幕生成 ───
    def _run_whisper(self, input_file, output_dir):
        """使用 TransWithAI Whisper（海南鸡版）生成字幕"""
        tp = self.config.get("tool_paths", DEFAULT_TOOL_PATHS)
        whisper_exe = tp.get("whisper", DEFAULT_TOOL_PATHS["whisper"])
        wp = self.config.get('whisper_params', {})

        # 固定使用海南鸡版专用模型目录 models/（faster-whisper 需要目录而非 .bin 文件）
        whisper_dir = Path(whisper_exe).parent
        model_dir = whisper_dir / "models"
        if not model_dir.is_dir() or not (model_dir / "model.bin").exists():
            self.log(f"  ✗ 模型目录不存在: {model_dir}（缺少 model.bin）")
            self.log("  ✗ 请确认海南鸡版模型文件位置，或重新安装")
            return None
        model_path = str(model_dir)

        cmd = [
            whisper_exe,
            "--audio_suffixes=mp4,mkv,avi,mov,webm,flv,wmv",
            "--sub_formats=srt",
            f"--device={wp.get('device', 'cuda')}",
            f"--output_dir={output_dir}",
            f"--model_name_or_path={model_path}",
            str(input_file)
        ]
        self.log(f"  模型: {model_path}")
        self.log(f"  完整命令: {' '.join(cmd)}")
        process = self._run_process(cmd, "Whisper", 0)
        if process:
            rc = process.returncode
            self.log(f"  退出码: {rc}")
            if rc == 0:
                self.log("  ✓ 字幕生成完成")
                return self._find_subtitle(output_dir, Path(input_file).stem)
            else:
                self.log(f"  ✗ 字幕生成失败 (退出码={rc})")
        else:
            self.log("  ✗ 字幕进程启动失败或已停止")
        self.log("  ⚠ 将在输出目录查找字幕")
        return self._find_subtitle(output_dir, Path(input_file).stem)

    # ─── 合成 ───
    def _run_compose(self, video_file, subtitle_file, output_dir):
        """合成字幕到视频"""
        engine = self.config.get('compose_engine', 'ffmpeg')
        video_name = Path(video_file).stem
        # 去除去码阶段附加的 -U 后缀，避免生成 clip3-U-UC.mp4
        if video_name.endswith('-U'):
            video_name = video_name[:-2]
        self.log(f"  引擎: {engine.upper()}")
        tp = self.config.get("tool_paths", DEFAULT_TOOL_PATHS)

        if engine == 'ffmpeg':
            exe = tp.get("ffmpeg", DEFAULT_TOOL_PATHS["ffmpeg"])
            # 输出固定为 {name}-UC.mp4
            output_file = str(output_dir / f"{video_name}-UC.mp4")
            fp = self.config.get('ffmpeg_params', {})
            vcodec = fp.get('vcodec', 'copy')
            cmd = [
                exe, "-i", str(video_file), "-i", str(subtitle_file),
                "-c:v", vcodec,
                "-c:a", fp.get('acodec', 'copy'),
                "-c:s", fp.get('scodec', 'mov_text'), "-y", output_file
            ]
            # 如果重编码且不是 copy，添加字幕样式参数
            if vcodec != 'copy':
                # 构建字幕滤镜参数
                font = fp.get('subtitle_font', 'Microsoft YaHei')
                fontsize = fp.get('subtitle_fontsize', 18)
                color = fp.get('subtitle_color', 'white')
                pos = fp.get('subtitle_position', 'bottom')
                mv = fp.get('subtitle_margin_v', 30)
                border = fp.get('subtitle_border', 1)
                outline = fp.get('subtitle_outline', 1)
                style = f"FontName={font},FontSize={fontsize},PrimaryColour=&H00FFFFFF"
                if color == 'yellow': style += "&H0000FFFF"
                elif color == 'cyan': style += "&H00FFFF00"
                elif color == 'green': style += "&H0000FF00"
                elif color == 'red': style += "&H000000FF"
                elif color == 'black': style += "&H00000000"
                style += f",BorderStyle=1,Outline={outline},Border={border}"
                style += f",MarginV={mv}"
                if pos == 'top': style += ",Alignment=8"
                # 使用 subtitles 滤镜（Windows 路径冒号会被解析为滤镜参数分隔符，
                # 解决方案：复制 SRT 到输出目录，用相对路径 + cwd）
                srt_copy = output_dir / f"{video_name}-compose.srt"
                try:
                    import shutil
                    shutil.copy2(str(subtitle_file), str(srt_copy))
                except Exception as e:
                    self.log(f"  ⚠ 复制字幕文件失败: {e}", level='warning')
                    srt_copy = subtitle_file  # fallback
                sub_filter = f"subtitles={srt_copy.name}:force_style='{style}'"
                cmd = [
                    exe, "-i", str(video_file),
                    "-vf", sub_filter,
                    "-c:v", vcodec,
                    "-c:a", fp.get('acodec', 'copy'),
                    "-y", output_file
                ]
                compose_cwd = str(output_dir)
            if fp.get('pix_fmt'): cmd.extend(["-pix_fmt", fp['pix_fmt']])
            if fp.get('preset'): cmd.extend(["-preset", fp['preset']])
            if fp.get('crf'): cmd.extend(["-crf", fp['crf']])
            if fp.get('bitrate'): cmd.extend(["-b:v", fp['bitrate']])
            if fp.get('faststart'): cmd.extend(["-movflags", "+faststart"])
            if fp.get('extra_args'): cmd.extend(fp['extra_args'].split())
        elif engine == 'mkvtoolnix':
            exe = tp.get("mkvmerge", DEFAULT_TOOL_PATHS["mkvmerge"])
            output_file = str(output_dir / f"{video_name}-UC.mkv")
            mp = self.config.get('mkv_params', {})
            cmd = [exe, "-o", output_file, str(video_file),
                   "--language", f"0:{mp.get('track_lang', 'chi')}"]
            if mp.get('default_track', True): cmd.extend(["--default-track", "0:yes"])
            if mp.get('forced_track', False): cmd.extend(["--forced-track", "0:yes"])
            if mp.get('track_name'): cmd.extend(["--track-name", f"0:{mp['track_name']}"])
            cmd.append(str(subtitle_file))
            if mp.get('extra_args'): cmd.extend(mp['extra_args'].split())
        else:
            self.log("  错误: 未知合成引擎")
            return None

        self.log(f"  完整命令: {' '.join(cmd)}")
        self.log(f"  输出: {output_file}")
        cwd_param = locals().get('compose_cwd') if engine == 'ffmpeg' else None
        process = self._run_process(cmd, "合成", 0, cwd=cwd_param)
        if process:
            rc = process.returncode
            self.log(f"  退出码: {rc}")
            if rc == 0:
                self.log(f"  ✓ 合成完成: {output_file}")
                return output_file
            else:
                self.log(f"  ✗ 合成失败 (退出码={rc})")
        else:
            self.log("  ✗ 合成进程启动失败或已停止")
        return None

    # ─── 工具函数 ───
    def _run_process(self, cmd, label, timeout=300, capture_stdout=True, cwd=None):
        """运行进程并实时输出日志/进度
        timeout=0 表示无超时（适合长时间去码），靠进度条判断进程是否存活。
        """
        self.log(f"  ⏳ 正在{label}...")
        try:
            # 强制子进程使用 UTF-8，避免 GBK 编码报错（emoji 等）
            child_env = os.environ.copy()
            child_env['PYTHONIOENCODING'] = 'utf-8'
            child_env['PYTHONUTF8'] = '1'
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=cwd,
                env=child_env,
                text=False,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
            )
            progress_buf = []
            stop_reader = False
            last_progress_time = [time.time()]
            last_log_time = [time.time()]
            latest_progress = [""]

            def _decode_line(raw_bytes):
                """工具进度输出：先严格 UTF-8，失败时回退 ANSI 代码页（GBK）"""
                if not raw_bytes.rstrip(b'\r\n'):
                    return ""
                try:
                    return raw_bytes.decode('utf-8').rstrip('\r\n')
                except UnicodeDecodeError:
                    return raw_bytes.decode(_PROCESS_ENCODING, errors='replace').rstrip('\r\n')

            def reader_thread():
                """后台读取 stdout，处理 \r 进度条和 \n 行"""
                nonlocal progress_buf, stop_reader
                buf = b""
                while not stop_reader:
                    byte = process.stdout.read(1)
                    if not byte:
                        break
                    buf += byte
                    if byte in (b'\n', b'\r'):
                        line_text = _decode_line(buf)
                        buf = b""
                        if line_text:
                            progress_buf.append((line_text, byte))
                            last_progress_time[0] = time.time()
                            latest_progress[0] = line_text

            import threading as _th
            reader = _th.Thread(target=reader_thread, daemon=True)
            reader.start()

            start_time = time.time()

            while True:
                if not self.running:
                    stop_reader = True
                    process.terminate()
                    self.log(f"  ⛔ {label}已停止")
                    return None

                # 有超时设定时才检查绝对超时
                if timeout > 0 and time.time() - start_time > timeout:
                    stop_reader = True
                    process.terminate()
                    self.log(f"  ⚠ {label}超时（{timeout}s）")
                    return None

                # 无超时模式：若 120 秒无任何进度输出且进程已退出，报错
                if timeout == 0:
                    idle = time.time() - last_progress_time[0]
                    if idle > 120 and process.poll() is not None:
                        stop_reader = True
                        self.log(f"  ⚠ {label}进程已无响应（{idle:.0f}s 无进度）")
                        return None
                    # 每 60 秒在日志输出一次当前进度摘要，让用户看到还在跑
                    if time.time() - last_log_time[0] > 60:
                        last_log_time[0] = time.time()
                        if latest_progress[0]:
                            self.log(f"  📊 {latest_progress[0][:80]}")

                # 取出所有缓存的行
                while progress_buf:
                    line_text, terminator = progress_buf.pop(0)
                    if capture_stdout:
                        self.log(f"  [{label}] {line_text}")  # 完整输出，不截断
                        if terminator == b'\n':
                            console_print(line_text, flush=True)
                        else:
                            console_print("\r" + line_text, end='', flush=True)
                    else:
                        # LADA/JASNA：进度行在文件日志记录
                        file_log(f"  [{label}] {line_text}")
                        console_print("\r" + line_text, end='', flush=True)

                if process.poll() is not None:
                    break
                time.sleep(0.05)

            # 清除残留的 \r 进度条，留一个换行
            console_print("\r                                                                                                \r", end='')

            stop_reader = True
            return process
        except subprocess.TimeoutExpired:
            console_print("\r                                                                                                \r", end='')
            process.terminate()
            self.log(f"  ⚠ {label}超时（{timeout}s）")
            return None
        except FileNotFoundError:
            console_print("\r                                                                                                \r", end='')
            self.log(f"  ✗ 找不到可执行文件: {cmd[0]}")
            return None
        except Exception as e:
            console_print("\r                                                                                                \r", end='')
            import traceback
            tb = traceback.format_exc()
            self.log(f"  ✗ {label}异常: {str(e)}")
            self.log(f"  调用栈:\n{tb}")
            return None

    def _find_subtitle(self, output_dir, video_stem):
        """在输出目录中查找 SRT 字幕文件"""
        # 常见的字幕命名模式
        patterns = [
            f"{video_stem}.srt",
            f"{video_stem}_*.srt",
            f"*{video_stem}*.srt",
        ]
        # 先精确匹配
        exact = output_dir / f"{video_stem}.srt"
        if exact.exists():
            return str(exact)
        # 再模糊匹配
        for p in output_dir.glob(f"{video_stem}*.srt"):
            return str(p)
        for p in output_dir.glob(f"*{video_stem}*.srt"):
            return str(p)
        # 列出所有 SRT
        srts = list(output_dir.glob("*.srt"))
        if srts:
            return str(srts[0])
        return None


# ==================== GPU 监控工具 ====================
def query_nvidia_smi(fields="memory.used,memory.total,temperature.gpu,utilization.gpu"):
    """查询 nvidia-smi 返回 GPU 状态，失败返回 None"""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu="+fields, "--format=csv,noheader,nounits"],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=5
        )
        if r.returncode != 0:
            return None
        lines = [line.strip() for line in r.stdout.strip().split('\n') if line.strip()]
        results = []
        for line in lines:
            parts = [p.strip() for p in line.split(',')]
            if len(parts) >= 4:
                results.append({
                    "mem_used_mb": int(parts[0]),
                    "mem_total_mb": int(parts[1]),
                    "temp_c": int(parts[2]),
                    "util_pct": int(parts[3])
                })
        return results
    except:
        return None


def get_gpu_memory_info():
    """获取显存信息，返回 (total_mb, available_mb) 或 None"""
    data = query_nvidia_smi("memory.used,memory.total,temperature.gpu,utilization.gpu")
    if not data:
        return None
    g0 = data[0]
    total = g0["mem_total_mb"]
    available = total - g0["mem_used_mb"]
    return total, available


def get_gpu_temperature():
    """获取GPU温度，返回 int 摄氏度或 None"""
    data = query_nvidia_smi("temperature.gpu")
    if not data:
        return None
    return data[0]["temp_c"]


GPU_MODES = [
    ("8G 模式", 8*1024, "serial"),
    ("12G 模式", 12*1024, "parallel_dc"),
    ("16G+ 模式", 16*1024, "full_parallel"),
]


def suggest_gpu_mode(total_mb):
    """根据总显存推荐 GPU 模式 index"""
    if total_mb >= 16*1024:
        return 2  # 16G+ full_parallel
    elif total_mb >= 12*1024:
        return 1  # 12G parallel_dc
    else:
        return 0  # 8G serial


# ==================== 参数设置对话框 ====================
class ParamDialog(QDialog):
    """5个工具的详细参数设置（Tab页）"""
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("详细参数设置")
        self.setMinimumSize(550, 420)
        self.setMaximumHeight(600)
        self._config = config
        layout = QVBoxLayout(self)

        self.tabs = QTabWidget()
        self._build_lada_tab()
        self._build_jasna_tab()
        self._build_whisper_tab()
        self._build_ffmpeg_tab()
        self._build_mkv_tab()
        self._build_gpu_tab()
        layout.addWidget(self.tabs)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

        # 禁用所有 QComboBox 的滚轮切换，避免鼠标划过时误操作
        for combo in self.findChildren(QComboBox):
            combo.installEventFilter(self)

    def eventFilter(self, obj, event):
        """拦截 QComboBox 的滚轮事件，防止误触切换选项"""
        if event.type() == QEvent.Type.Wheel and isinstance(obj, QComboBox):
            return True  # 吞掉滚轮事件
        return super().eventFilter(obj, event)

    def get_params(self):
        """返回更新后的参数字典"""
        return {
            "lada_params": {
                "device": self.lada_device.text(),
                "detection_model": self.lada_det_model.currentText(),
                "model": self.lada_restore_model.currentText(),
                "face_detection": self.lada_face.isChecked(),
                "tile_size": self.lada_tile.value(),
                "fp16": self.lada_fp16.isChecked(),
                "preset": self.lada_preset.currentText(),
                "extra_args": self.lada_extra.text()
            },
            "jasna_params": {
                "device": self.jasna_device.text(),
                "detection_model": self.jasna_det_model.currentText(),
                "detection_threshold": float(self.jasna_det_thresh.text() or 0.25),
                "max_clip_size": self.jasna_clip.value(),
                "fp16": self.jasna_fp16.isChecked(),
                "temporal_overlap": self.jasna_overlap.value(),
                "fade": self.jasna_fade.isChecked(),
                "denoise_strength": {"无":"none","低":"low","中":"medium","高":"high"}.get(
                    self.jasna_denoise.currentText(), "medium"),
                "denoise_timing": {"主修复后":"after_primary","主修复前":"after_primary","同步":"after_secondary"}.get(
                    self.jasna_denoise_timing.currentText(), "after_primary"),
                "secondary_restoration": self.jasna_secondary.currentText(),
                "secondary_scale": int(self.jasna_sec_scale.currentText().replace("x","")),
                "secondary_quality": self.jasna_sec_quality.currentText().lower(),
                "secondary_denoise": self.jasna_sec_denoise.currentText().lower(),
                "secondary_deblur": self.jasna_sec_deblur.currentText().lower(),
                "codec": self.jasna_codec.currentText().lower(),
                "cq": self.jasna_cq.value()
            },
            "whisper_params": {
                "device": self.whisper_device.currentText(),
                "compute_type": self.whisper_compute.currentText(),
                "model": "models",
                "task": self.whisper_task.currentText(),
                "language": self.whisper_lang.currentText(),
                "vad": self.whisper_vad.isChecked(),
                "vad_threshold": float(self.whisper_vad_thresh.text() or 0.5),
                "merge_enabled": self.whisper_merge.isChecked(),
                "merge_max_gap_ms": self.whisper_merge_gap.value(),
                "merge_max_duration_ms": self.whisper_merge_dur.value(),
                "batch_size": self.whisper_batch.value(),
                "beam_size": self.whisper_beam.value(),
                "repetition_penalty": float(self.whisper_repeat.text() or 1.1),
                "log_level": self.whisper_log.currentText()
            },
            "ffmpeg_params": {
                "vcodec": self.ff_vcodec.currentText(),
                "acodec": self.ff_acodec.currentText(),
                "scodec": self.ff_scodec.currentText(),
                "pix_fmt": self.ff_pixfmt.currentText(),
                "preset": self.ff_preset.currentText(),
                "crf": self.ff_crf.text(),
                "bitrate": self.ff_bitrate.text(),
                "faststart": self.ff_faststart.isChecked(),
                "extra_args": self.ff_extra.text(),
                "subtitle_font": self.ff_sub_font.text(),
                "subtitle_fontsize": self.ff_sub_size.value(),
                "subtitle_color": self.ff_sub_color.currentText(),
                "subtitle_position": self.ff_sub_pos.currentText(),
                "subtitle_margin_v": self.ff_sub_mv.value(),
                "subtitle_margin_b": self.ff_sub_mb.value(),
                "subtitle_border": self.ff_sub_border.value(),
                "subtitle_outline": self.ff_sub_outline.value()
            },
            "mkv_params": {
                "track_lang": self.mkv_lang.currentText(),
                "default_track": self.mkv_default.isChecked(),
                "forced_track": self.mkv_forced.isChecked(),
                "compression": self.mkv_compress.currentText(),
                "track_name": self.mkv_track_name.text(),
                "extra_args": self.mkv_extra.text()
            },
            "gpu_config": {
                "mode": ["auto","8g_serial","12g_dc_parallel","16g_full_parallel"][self.gpu_tab_mode.currentIndex()],
                "mode_index": self.gpu_tab_mode.currentIndex() - 1,
                "temp_limit": self.gpu_tab_temp.value(),
                "safety_margin_mb": self.gpu_tab_margin.value(),
                "temp_cooldown": self.gpu_tab_cooldown.value()
            }
        }

    def _build_lada_tab(self):
        tab = QWidget()
        form = QFormLayout(tab); form.setSpacing(4)
        lp = self._config.get("lada_params", {})
        # 1. GPU 设备
        self.lada_device = QLineEdit(lp.get("device", "cuda:0")); form.addRow("GPU 设备:", self.lada_device)
        # 2. 检测模型 默认v4-fast
        self.lada_det_model = QComboBox()
        self.lada_det_model.addItems(["v4-fast","v4-accurate","v2","none"])
        self.lada_det_model.setCurrentText(lp.get("detection_model","v4-fast")); form.addRow("检测模型:", self.lada_det_model)
        # 3. 修复模型
        self.lada_restore_model = QComboBox()
        self.lada_restore_model.addItems(["basicvsrpp-v1.2","basicvsrpp-v1.1","nafnet-v1.0","vqfr-v1.0"])
        self.lada_restore_model.setCurrentText(lp.get("model","basicvsrpp-v1.2")); form.addRow("修复模型:", self.lada_restore_model)
        # 4. 面部检测
        self.lada_face = QCheckBox("启用面部检测增强")
        self.lada_face.setChecked(lp.get("face_detection", False)); form.addRow("面部检测:", self.lada_face)
        # 5. 切片帧数(默认180)
        self.lada_tile = QSpinBox(); self.lada_tile.setRange(1, 1024)
        self.lada_tile.setValue(lp.get("tile_size", 180)); self.lada_tile.setSuffix(" 帧"); form.addRow("切片帧数:", self.lada_tile)
        # 6. FP16 半精度开关
        self.lada_fp16 = QCheckBox("启用 FP16（降低显存占用）")
        self.lada_fp16.setChecked(lp.get("fp16", True)); form.addRow("FP16:", self.lada_fp16)
        # 7. 编码预设
        self.lada_preset = QComboBox()
        self.lada_preset.addItems(["hevc-nvidia-gpu-hq","hevc-nvidia-gpu-balanced","hevc-nvidia-gpu-uhq","h264-nvidia-gpu-fast","h264-cpu-fast","h264-cpu-uhq","av1-cpu-uhq"])
        self.lada_preset.setCurrentText(lp.get("preset","hevc-nvidia-gpu-hq")); form.addRow("编码预设:", self.lada_preset)
        # 8. 自定义参数
        self.lada_extra = QLineEdit(lp.get("extra_args",""))
        self.lada_extra.setPlaceholderText("例如: --encoder hevc_nvenc --encoder-options preset=p4:tune=hq")
        form.addRow("自定义参数:", self.lada_extra)
        # 9. 输出固定
        out_lbl = QLabel("{orig_file_name}-U.mp4")
        out_lbl.setStyleSheet("color: #7f8c8d; font-family: Consolas;")
        form.addRow("输出命名:", out_lbl)
        self.tabs.addTab(tab, "LADA")

    def _build_jasna_tab(self):
        tab = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        form = QFormLayout(inner); form.setSpacing(4)
        jp = self._config.get("jasna_params", {})

        # 1. GPU 设备
        self.jasna_device = QLineEdit(jp.get("device","cuda:0")); form.addRow("GPU 设备:", self.jasna_device)
        # 2. 检测模型 默认rfdetr-v5（v0.7.2 新增 lada-YOLO 支持）
        self.jasna_det_model = QComboBox()
        self.jasna_det_model.addItems(["rfdetr-v5","rfdetr-v4","rfdetr-v3","lada-YOLO-v4","lada-YOLO-v5","none"])
        self.jasna_det_model.setCurrentText(jp.get("detection_model","rfdetr-v5")); form.addRow("检测模型:", self.jasna_det_model)
        # 3. 检测阈值 默认0.25
        self.jasna_det_thresh = QLineEdit(str(jp.get("detection_threshold",0.25)))
        self.jasna_det_thresh.setFixedWidth(60); form.addRow("检测阈值:", self.jasna_det_thresh)
        # 4. 切片帧数(默认90)
        self.jasna_clip = QSpinBox(); self.jasna_clip.setRange(1,500)
        self.jasna_clip.setValue(jp.get("max_clip_size",90)); self.jasna_clip.setSuffix(" 帧"); form.addRow("切片帧数:", self.jasna_clip)
        # 5. FP16
        self.jasna_fp16 = QCheckBox("启用 FP16（降低显存占用）")
        self.jasna_fp16.setChecked(jp.get("fp16",True)); form.addRow("FP16:", self.jasna_fp16)
        # 6. 时间重叠 默认8
        self.jasna_overlap = QSpinBox(); self.jasna_overlap.setRange(0,48)
        self.jasna_overlap.setValue(jp.get("temporal_overlap",8)); self.jasna_overlap.setSuffix(" 帧"); form.addRow("时间重叠:", self.jasna_overlap)
        # 7. 淡入淡出 默认启用
        self.jasna_fade = QCheckBox("启用淡入淡出过渡")
        self.jasna_fade.setChecked(jp.get("fade",True)); form.addRow("淡入淡出:", self.jasna_fade)
        # 8. 降噪强度 默认中
        self.jasna_denoise = QComboBox()
        self.jasna_denoise.addItems(["无","低","中","高"])
        val = jp.get("denoise_strength","medium")
        idx = {"none":"无","low":"低","medium":"中","high":"高"}.get(val,"中")
        self.jasna_denoise.setCurrentText(idx); form.addRow("降噪强度:", self.jasna_denoise)
        # 9. 降噪应用时机 默认主修复后
        self.jasna_denoise_timing = QComboBox()
        self.jasna_denoise_timing.addItems(["主修复后","主修复前","同步"])
        timing = jp.get("denoise_timing","post_main")
        tidx = {"post_main":"主修复后","pre_main":"主修复前","sync":"同步"}.get(timing,"主修复后")
        self.jasna_denoise_timing.setCurrentText(tidx); form.addRow("降噪时机:", self.jasna_denoise_timing)
        # 10. 二次修复区 默认RTX Super RES + 子设置
        self.jasna_secondary = QComboBox()
        self.jasna_secondary.addItems(["无","Unet-4x","TVAI","RTX Super RES"])
        sec = jp.get("secondary_restoration","rtx-super-res")
        sidx = {"none":"无","unet-4x":"Unet-4x","tvai":"TVAI","rtx-super-res":"RTX Super RES"}.get(sec,"RTX Super RES")
        self.jasna_secondary.setCurrentText(sidx); form.addRow("二次修复:", self.jasna_secondary)

        # 子设置组
        sub_group = QGroupBox("二次修复子设置")
        sub_form = QFormLayout(sub_group); sub_form.setSpacing(3)
        # 缩放
        self.jasna_sec_scale = QComboBox()
        self.jasna_sec_scale.addItems(["2x","4x"])
        self.jasna_sec_scale.setCurrentText(f"{jp.get('secondary_scale',4)}x"); sub_form.addRow("缩放:", self.jasna_sec_scale)
        # 质量
        self.jasna_sec_quality = QComboBox()
        self.jasna_sec_quality.addItems(["High","Medium","Low"])
        q = jp.get("secondary_quality","high")
        qidx = {"high":"High","medium":"Medium","low":"Low"}.get(q,"High")
        self.jasna_sec_quality.setCurrentText(qidx); sub_form.addRow("质量:", self.jasna_sec_quality)
        # 降噪
        self.jasna_sec_denoise = QComboBox()
        self.jasna_sec_denoise.addItems(["Medium","Low","High","None"])
        dn = jp.get("secondary_denoise","medium")
        dnidx = {"medium":"Medium","low":"Low","high":"High","none":"None"}.get(dn,"Medium")
        self.jasna_sec_denoise.setCurrentText(dnidx); sub_form.addRow("降噪:", self.jasna_sec_denoise)
        # 去模糊
        self.jasna_sec_deblur = QComboBox()
        self.jasna_sec_deblur.addItems(["Medium","Low","High","None"])
        db = jp.get("secondary_deblur","medium")
        dbidx = {"medium":"Medium","low":"Low","high":"High","none":"None"}.get(db,"Medium")
        self.jasna_sec_deblur.setCurrentText(dbidx); sub_form.addRow("去模糊:", self.jasna_sec_deblur)
        form.addRow(sub_group)

        # 11. 编码设置 默认HEVC
        self.jasna_codec = QComboBox()
        self.jasna_codec.addItems(["HEVC","H264"])
        self.jasna_codec.setCurrentText(jp.get("codec","hevc").upper()); form.addRow("编码设置:", self.jasna_codec)
        # 12. 质量(CQ) 默认22
        self.jasna_cq = QSpinBox(); self.jasna_cq.setRange(10, 51)
        self.jasna_cq.setValue(jp.get("cq",22)); form.addRow("质量(CQ):", self.jasna_cq)
        # 13. 输出
        out_lbl = QLabel("{orig_file_name}-U.mp4")
        out_lbl.setStyleSheet("color: #7f8c8d; font-family: Consolas;")
        form.addRow("输出命名:", out_lbl)

        scroll.setWidget(inner)
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0,0,0,0)
        tab_layout.addWidget(scroll)
        self.tabs.addTab(tab, "JASNA")

    def _build_whisper_tab(self):
        tab = QWidget()
        form = QFormLayout(tab); form.setSpacing(4)
        wp = self._config.get("whisper_params", {})
        self.whisper_device = QComboBox()
        self.whisper_device.addItems(["cuda","cpu"])
        self.whisper_device.setCurrentText(wp.get("device","cuda")); form.addRow("运行设备:", self.whisper_device)
        self.whisper_compute = QComboBox()
        self.whisper_compute.addItems(["float16","float32","int8"])
        self.whisper_compute.setCurrentText(wp.get("compute_type","float16")); form.addRow("计算精度:", self.whisper_compute)
        # 模型固定为海南鸡版专用 models/ 目录，不可选择
        tp = self._config.get("tool_paths", {})
        whisper_exe = tp.get("whisper", "infer.exe")
        model_path = str(Path(whisper_exe).parent / "models")
        model_label = QLabel(model_path)
        model_label.setWordWrap(True)
        form.addRow("模型文件:", model_label)
        self.whisper_task = QComboBox()
        self.whisper_task.addItems(["translate","transcribe"])
        self.whisper_task.setCurrentText(wp.get("task","translate")); form.addRow("任务类型:", self.whisper_task)
        self.whisper_lang = QComboBox()
        self.whisper_lang.addItems(["auto","ja","zh","en","ko"])
        self.whisper_lang.setCurrentText(wp.get("language","ja")); form.addRow("源语言:", self.whisper_lang)
        self.whisper_vad = QCheckBox("启用 VAD（语音活动检测）")
        self.whisper_vad.setChecked(wp.get("vad",True)); form.addRow("",self.whisper_vad)
        vth = QHBoxLayout()
        self.whisper_vad_thresh = QLineEdit(str(wp.get("vad_threshold",0.5)))
        self.whisper_vad_thresh.setFixedWidth(60); vth.addWidget(self.whisper_vad_thresh)
        vth.addWidget(QLabel("(0.0~1.0)")); vth.addStretch(); form.addRow("VAD 阈值:", vth)
        self.whisper_merge = QCheckBox("合并短片段（减少重复字幕）")
        self.whisper_merge.setChecked(wp.get("merge_enabled",True)); form.addRow("",self.whisper_merge)
        mg = QHBoxLayout()
        self.whisper_merge_gap = QSpinBox(); self.whisper_merge_gap.setRange(100,10000)
        self.whisper_merge_gap.setValue(wp.get("merge_max_gap_ms",2000)); self.whisper_merge_gap.setSuffix(" ms")
        mg.addWidget(self.whisper_merge_gap); mg.addWidget(QLabel("最大间隔")); mg.addStretch(); form.addRow("合并参数:", mg)
        md = QHBoxLayout()
        self.whisper_merge_dur = QSpinBox(); self.whisper_merge_dur.setRange(1000,60000)
        self.whisper_merge_dur.setValue(wp.get("merge_max_duration_ms",20000)); self.whisper_merge_dur.setSuffix(" ms")
        md.addWidget(self.whisper_merge_dur); md.addWidget(QLabel("最大时长")); md.addStretch(); form.addRow("", md)
        self.whisper_batch = QSpinBox(); self.whisper_batch.setRange(1,32)
        self.whisper_batch.setValue(wp.get("batch_size",8)); form.addRow("Batch 大小:", self.whisper_batch)
        self.whisper_beam = QSpinBox(); self.whisper_beam.setRange(1,10)
        self.whisper_beam.setValue(wp.get("beam_size",5)); form.addRow("Beam 大小:", self.whisper_beam)
        rp = QHBoxLayout()
        self.whisper_repeat = QLineEdit(str(wp.get("repetition_penalty",1.1)))
        self.whisper_repeat.setFixedWidth(60); rp.addWidget(self.whisper_repeat); rp.addStretch()
        form.addRow("重复惩罚:", rp)
        self.whisper_log = QComboBox()
        self.whisper_log.addItems(["DEBUG","INFO","WARNING","ERROR"])
        self.whisper_log.setCurrentText(wp.get("log_level","DEBUG")); form.addRow("日志级别:", self.whisper_log)
        self.tabs.addTab(tab, "Whisper")

    def _build_ffmpeg_tab(self):
        tab = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        form = QFormLayout(inner); form.setSpacing(4)
        fp = self._config.get("ffmpeg_params", {})

        # ── 编码设置 ──
        enc_group = QGroupBox("编码设置")
        enc_form = QFormLayout(enc_group); enc_form.setSpacing(3)
        self.ff_vcodec = QComboBox()
        self.ff_vcodec.addItems(["copy","libx264","libx265","hevc_nvenc","h264_nvenc","libxvid"])
        self.ff_vcodec.setCurrentText(fp.get("vcodec","copy")); enc_form.addRow("视频编码:", self.ff_vcodec)
        self.ff_acodec = QComboBox()
        self.ff_acodec.addItems(["copy","aac","mp3","libopus","flac"])
        self.ff_acodec.setCurrentText(fp.get("acodec","copy")); enc_form.addRow("音频编码:", self.ff_acodec)
        self.ff_scodec = QComboBox()
        self.ff_scodec.addItems(["mov_text","srt","ass","copy"])
        self.ff_scodec.setCurrentText(fp.get("scodec","mov_text")); enc_form.addRow("字幕编码:", self.ff_scodec)
        self.ff_pixfmt = QComboBox()
        self.ff_pixfmt.addItems(["","yuv420p","yuv422p","yuv444p","yuv420p10le"])
        self.ff_pixfmt.setCurrentText(fp.get("pix_fmt","")); self.ff_pixfmt.setEditable(True); enc_form.addRow("像素格式:", self.ff_pixfmt)
        self.ff_preset = QComboBox()
        self.ff_preset.addItems(["","ultrafast","superfast","veryfast","faster","fast","medium","slow","slower","veryslow","placebo"])
        self.ff_preset.setCurrentText(fp.get("preset","")); enc_form.addRow("编码预设:", self.ff_preset)
        cr = QHBoxLayout()
        self.ff_crf = QLineEdit(fp.get("crf","")); self.ff_crf.setFixedWidth(60); self.ff_crf.setPlaceholderText("18-28")
        cr.addWidget(self.ff_crf); cr.addWidget(QLabel("(留空=自动)")); cr.addStretch(); enc_form.addRow("CRF 值:", cr)
        br = QHBoxLayout()
        self.ff_bitrate = QLineEdit(fp.get("bitrate","")); self.ff_bitrate.setFixedWidth(100)
        self.ff_bitrate.setPlaceholderText("例如 5M")
        br.addWidget(self.ff_bitrate); br.addWidget(QLabel("(留空=自动)")); br.addStretch(); enc_form.addRow("视频码率:", br)
        self.ff_faststart = QCheckBox("启用 Faststart（流式播放优化）")
        self.ff_faststart.setChecked(fp.get("faststart",True)); enc_form.addRow("",self.ff_faststart)
        form.addRow(enc_group)

        # ── 字幕样式 ──
        sub_group = QGroupBox("字幕样式（仅在重编码时生效）")
        sub_form = QFormLayout(sub_group); sub_form.setSpacing(3)
        self.ff_sub_font = QLineEdit(fp.get("subtitle_font","Microsoft YaHei"))
        self.ff_sub_font.setPlaceholderText("NoTo Sans CJK, Arial, ...")
        sub_form.addRow("字体:", self.ff_sub_font)
        self.ff_sub_size = QSpinBox(); self.ff_sub_size.setRange(8, 72)
        self.ff_sub_size.setValue(fp.get("subtitle_fontsize",18)); self.ff_sub_size.setSuffix(" px"); sub_form.addRow("字号:", self.ff_sub_size)
        self.ff_sub_color = QComboBox()
        self.ff_sub_color.addItems(["white","yellow","cyan","green","red","black"])
        self.ff_sub_color.setCurrentText(fp.get("subtitle_color","white")); sub_form.addRow("字体颜色:", self.ff_sub_color)
        self.ff_sub_pos = QComboBox()
        self.ff_sub_pos.addItems(["bottom","top"])
        self.ff_sub_pos.setCurrentText(fp.get("subtitle_position","bottom")); sub_form.addRow("位置:", self.ff_sub_pos)
        mv = QHBoxLayout()
        self.ff_sub_mv = QSpinBox(); self.ff_sub_mv.setRange(0,200)
        self.ff_sub_mv.setValue(fp.get("subtitle_margin_v",30)); self.ff_sub_mv.setSuffix(" px"); mv.addWidget(self.ff_sub_mv)
        mv.addWidget(QLabel("垂直边距")); mv.addStretch(); sub_form.addRow("", mv)
        mb = QHBoxLayout()
        self.ff_sub_mb = QSpinBox(); self.ff_sub_mb.setRange(0,200)
        self.ff_sub_mb.setValue(fp.get("subtitle_margin_b",10)); self.ff_sub_mb.setSuffix(" px"); mb.addWidget(self.ff_sub_mb)
        mb.addWidget(QLabel("底部边距")); mb.addStretch(); sub_form.addRow("", mb)
        self.ff_sub_border = QSpinBox(); self.ff_sub_border.setRange(0,10)
        self.ff_sub_border.setValue(fp.get("subtitle_border",1)); sub_form.addRow("边框宽度:", self.ff_sub_border)
        self.ff_sub_outline = QSpinBox(); self.ff_sub_outline.setRange(0,10)
        self.ff_sub_outline.setValue(fp.get("subtitle_outline",1)); sub_form.addRow("阴影宽度:", self.ff_sub_outline)
        form.addRow(sub_group)

        # ── 自定义参数 ──
        self.ff_extra = QLineEdit(fp.get("extra_args",""))
        self.ff_extra.setPlaceholderText("例如: -map 0 -max_muxing_queue_size 1024")
        form.addRow("额外参数:", self.ff_extra)

        # ── 输出 ──
        out_lbl = QLabel("{orig_file_name}-UC.mp4")
        out_lbl.setStyleSheet("color: #7f8c8d; font-family: Consolas;")
        form.addRow("输出命名:", out_lbl)

        scroll.setWidget(inner)
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(0,0,0,0)
        tab_layout.addWidget(scroll)
        self.tabs.addTab(tab, "FFmpeg")

    def _build_mkv_tab(self):
        tab = QWidget()
        form = QFormLayout(tab); form.setSpacing(4)
        mp = self._config.get("mkv_params", {})
        self.mkv_lang = QComboBox()
        self.mkv_lang.addItems(["chi","jpn","eng","kor","und"])
        self.mkv_lang.setCurrentText(mp.get("track_lang","chi")); self.mkv_lang.setEditable(True); form.addRow("轨道语言:", self.mkv_lang)
        self.mkv_default = QCheckBox("设为默认轨道"); self.mkv_default.setChecked(mp.get("default_track",True)); form.addRow("",self.mkv_default)
        self.mkv_forced = QCheckBox("设为强制轨道"); self.mkv_forced.setChecked(mp.get("forced_track",False)); form.addRow("",self.mkv_forced)
        self.mkv_compress = QComboBox()
        self.mkv_compress.addItems(["none","zlib","bz2","lzo","header"])
        self.mkv_compress.setCurrentText(mp.get("compression","none")); form.addRow("压缩方式:", self.mkv_compress)
        self.mkv_track_name = QLineEdit(mp.get("track_name",""))
        self.mkv_track_name.setPlaceholderText("例如: 中文翻译"); form.addRow("轨道名称:", self.mkv_track_name)
        self.mkv_extra = QLineEdit(mp.get("extra_args",""))
        self.mkv_extra.setPlaceholderText("例如: --track-order 0:0,0:1,1:0"); form.addRow("额外参数:", self.mkv_extra)
        self.tabs.addTab(tab, "MKVToolNix")

    def _build_gpu_tab(self):
        """GPU 全局设置 Tab"""
        tab = QWidget()
        form = QFormLayout(tab); form.setSpacing(4)
        gc = self._config.get("gpu_config", {})

        # 模式选择
        self.gpu_tab_mode = QComboBox()
        self.gpu_tab_mode.addItems(["自动检测", "8G 串行模式", "12G 去码+合成并行", "16G+ 全并行模式"])
        mi = gc.get("mode_index", -1)
        self.gpu_tab_mode.setCurrentIndex(mi + 1 if mi >= 0 else 0)
        form.addRow("运行模式:", self.gpu_tab_mode)

        # 温度上限
        self.gpu_tab_temp = QSpinBox(); self.gpu_tab_temp.setRange(60, 99)
        self.gpu_tab_temp.setValue(gc.get("temp_limit", 85))
        self.gpu_tab_temp.setSuffix(" °C")
        form.addRow("温度上限:", self.gpu_tab_temp)

        # 安全余量
        self.gpu_tab_margin = QSpinBox(); self.gpu_tab_margin.setRange(256, 8192)
        self.gpu_tab_margin.setValue(gc.get("safety_margin_mb", 2048))
        self.gpu_tab_margin.setSingleStep(256)
        self.gpu_tab_margin.setSuffix(" MB")
        form.addRow("显存安全余量:", self.gpu_tab_margin)

        # 降温等待
        self.gpu_tab_cooldown = QSpinBox(); self.gpu_tab_cooldown.setRange(10, 300)
        self.gpu_tab_cooldown.setValue(gc.get("temp_cooldown", 60))
        self.gpu_tab_cooldown.setSingleStep(10)
        self.gpu_tab_cooldown.setSuffix(" 秒")
        form.addRow("降温等待:", self.gpu_tab_cooldown)

        # 说明文字
        info = QLabel(
            "💡 模式说明：\n"
            "• 8G 串行：逐个文件处理，适合小显存\n"
            "• 12G 并行：去码与合成跨文件并行，适合中显存\n"
            "• 16G+ 全并行：多文件全流水线处理\n\n"
            "安全余量 = 执行任务前需保留的空闲显存\n"
            "温度上限 = 触发降温等待的阈值"
        )
        info.setStyleSheet("color: #7f8c8d; padding: 8px;")
        info.setWordWrap(True)
        form.addRow(info)

        self.tabs.addTab(tab, "GPU")


# ==================== 主窗口 ====================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # 初始化文件日志
        log_path = setup_file_logging()
        console_print("智能去码字幕工具箱 v1.2 - 处理进度将显示在此窗口")
        console_print(f"日志文件: {log_path}")
        console_print("=" * 55)
        self.config = self.load_config()
        self.tool_paths = self.config.get("tool_paths", dict(DEFAULT_TOOL_PATHS))
        self.worker = None

        # GPU 监控变量（必须在 init_ui 前初始化）
        self.gpu_mode_index = self.config["gpu_config"].get("mode_index", -1)
        self.gpu_temp_limit = self.config["gpu_config"].get("temp_limit", 85)
        self.gpu_safety_margin = self.config["gpu_config"].get("safety_margin_mb", 2048)
        self.gpu_cooldown_sec = self.config["gpu_config"].get("temp_cooldown", 60)
        self.gpu_overheated = False
        self._last_temp_warn = 0

        self.init_ui()

        # 启动后延迟检测工具 + 自动扫描输入目录
        QTimer.singleShot(500, self.initial_tool_check)
        QTimer.singleShot(600, self._auto_scan_input_dir)

        # 定时刷新工具状态（每60秒）
        self.timer = QTimer()
        self.timer.timeout.connect(self.check_all_tools)
        self.timer.start(60000)

        # GPU 监控定时器（每3秒刷新）— 在 init_ui 创建组件后启动

    def load_config(self):
        """加载/初始化配置"""
        default = {
            "input_dir": str(DEFAULT_INPUT_DIR),
            "output_dir": str(DEFAULT_OUTPUT_DIR),
            "demark_engine": "lada",
            "compose_engine": "ffmpeg",
            "language": "auto",
            "do_demark": False,
            "do_subtitle": True,
            "do_compose": True,
            # LADA 参数
            "lada_params": {
                "device": "cuda:0",
                "detection_model": "v4-fast",
                "model": "basicvsrpp-v1.2",
                "face_detection": False,
                "tile_size": 180,
                "fp16": True,
                "preset": "medium",
                "extra_args": ""
            },
            # JASNA 参数
            "jasna_params": {
                "device": "cuda:0",
                "detection_model": "rfdetr-v5",
                "detection_threshold": 0.25,
                "max_clip_size": 90,
                "fp16": True,
                "temporal_overlap": 8,
                "fade": True,
                "denoise_strength": "medium",
                "denoise_timing": "post_main",
                "secondary_restoration": "rtx-super-res",
                "secondary_scale": 4,
                "secondary_quality": "high",
                "secondary_denoise": "medium",
                "secondary_deblur": "medium",
                "codec": "hevc",
                "cq": 22
            },
            # Whisper 参数
            "whisper_params": {
                "device": "cuda",
                "compute_type": "float16",
                "task": "translate",
                "language": "ja",
                "vad": True,
                "vad_threshold": 0.5,
                "merge_enabled": True,
                "merge_max_gap_ms": 2000,
                "merge_max_duration_ms": 20000,
                "batch_size": 8,
                "beam_size": 5,
                "patience": 1.0,
                "repetition_penalty": 1.1,
                "log_level": "DEBUG"
            },
            # FFmpeg 参数
            "ffmpeg_params": {
                "vcodec": "copy",
                "acodec": "copy",
                "scodec": "mov_text",
                "pix_fmt": "",
                "preset": "",
                "crf": "",
                "bitrate": "",
                "extra_args": "",
                "faststart": True,
                "subtitle_font": "Microsoft YaHei",
                "subtitle_fontsize": 18,
                "subtitle_color": "white",
                "subtitle_position": "bottom",
                "subtitle_margin_v": 30,
                "subtitle_margin_b": 10,
                "subtitle_border": 1,
                "subtitle_outline": 1
            },
            # MKVToolNix 参数
            "mkv_params": {
                "track_lang": "chi",
                "default_track": True,
                "forced_track": False,
                "compression": "none",
                "extra_args": ""
            },
            # GPU 配置
            "gpu_config": {
                "mode": "auto",
                "mode_index": -1,
                "safety_margin_mb": 2048,
                "temp_limit": 85,
                "temp_cooldown": 60
            }
        }
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    saved = json.load(f)
                    default.update(saved)
            except:
                pass
        return default

    def save_config(self):
        """保存配置"""
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except:
            pass

    # ─── 界面构建 ───
    def init_ui(self):
        self.setWindowTitle("智能去码字幕工具箱 v1.2")
        self.setGeometry(200, 100, 1200, 800)
        self.setMinimumSize(1000, 650)

        # 主色调
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#f5f6fa"))
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#2c3e50"))
        self.setPalette(palette)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setSpacing(8)
        main_layout.setContentsMargins(12, 12, 12, 12)

        # ── 标题栏 + 工具状态 ──
        title_bar = self._build_title_bar()
        main_layout.addLayout(title_bar)

        # ── GPU 状态栏 ──
        gpu_bar = self._build_gpu_bar()
        main_layout.addWidget(gpu_bar)

        # ── 配置区 ──
        config_group = self._build_config_group()
        main_layout.addWidget(config_group)

        # ── 可拖拽分割：文件列表 + 日志 ──
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setHandleWidth(4)
        splitter.setStyleSheet("QSplitter::handle { background: #d5dbe5; }")

        # 文件列表
        file_group = self._build_file_group()
        file_group.setMinimumHeight(80)
        splitter.addWidget(file_group)

        # 日志
        log_group = QGroupBox("运行日志")
        log_layout = QVBoxLayout()
        self.log_text = QTextEdit()
        self.log_text.setFont(QFont("Consolas", 10))
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(120)
        self.log_text.setStyleSheet("""
            QTextEdit {
                background-color: #1a1a2e;
                color: #e0e0e0;
                border: 1px solid #2d3436;
                border-radius: 4px;
                padding: 6px;
            }
        """)
        log_layout.addWidget(self.log_text)
        log_group.setLayout(log_layout)
        splitter.addWidget(log_group)

        # 默认比例 3:7（文件列表小，日志大）
        splitter.setSizes([400, 300])
        main_layout.addWidget(splitter, 1)

        # ── 控制按钮 ──
        btn_layout = QHBoxLayout()
        self.start_btn = QPushButton("▶ 开始处理")
        self.start_btn.setFont(QFont("Microsoft YaHei", 14, QFont.Weight.Bold))
        self.start_btn.setMinimumHeight(45)
        self.start_btn.setStyleSheet("""
            QPushButton { background-color: #27ae60; color: white; border-radius: 6px; }
            QPushButton:hover { background-color: #2ecc71; }
            QPushButton:disabled { background-color: #95a5a6; }
        """)
        self.start_btn.clicked.connect(self.start_processing)
        btn_layout.addWidget(self.start_btn, 3)

        self.stop_btn = QPushButton("⏹ 停止")
        self.stop_btn.setFont(QFont("Microsoft YaHei", 14))
        self.stop_btn.setMinimumHeight(45)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("""
            QPushButton { background-color: #e74c3c; color: white; border-radius: 6px; }
            QPushButton:hover { background-color: #c0392b; }
            QPushButton:disabled { background-color: #95a5a6; }
        """)
        self.stop_btn.clicked.connect(self.stop_processing)
        btn_layout.addWidget(self.stop_btn, 1)

        btn_layout.addStretch()

        self.clear_log_btn = QPushButton("清除日志")
        self.clear_log_btn.setFont(QFont("Microsoft YaHei", 10))
        self.clear_log_btn.clicked.connect(self.clear_log)
        btn_layout.addWidget(self.clear_log_btn)

        main_layout.addLayout(btn_layout)

        # ── 进度条 ──
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumHeight(22)
        self.progress_bar.setTextVisible(True)
        main_layout.addWidget(self.progress_bar)

        self.save_config()

    def _build_title_bar(self):
        """构建标题栏 + 5个工具状态按钮"""
        layout = QHBoxLayout()
        title = QLabel("🎬 智能去码字幕工具箱 v1.2")
        title.setFont(QFont("Microsoft YaHei", 18, QFont.Weight.Bold))
        title.setStyleSheet("color: #2c3e50;")
        layout.addWidget(title)
        layout.addStretch()

        # 5个工具状态按钮（可点击修改路径）
        self.status_btns = {}
        for name in STATUS_TOOLS:
            btn = QPushButton(f"● {name}")
            btn.setFont(QFont("Microsoft YaHei", 9))
            btn.setFixedHeight(28)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    color: #95a5a6; background: transparent;
                    border: 1px solid #ddd; border-radius: 4px;
                    padding: 2px 8px; margin-left: 4px;
                }
                QPushButton:hover { background: #ecf0f1; border-color: #bdc3c7; }
            """)
            btn.clicked.connect(lambda checked, n=name: self._edit_tool_path(n))
            layout.addWidget(btn)
            self.status_btns[name] = btn

        return layout

    def _build_gpu_bar(self):
        """构建 GPU 状态栏：模式选择 + 显存条 + 温度 + 设置"""
        frame = QFrame()
        frame.setStyleSheet("""
            QFrame#GpuBar {
                background: #eef2f7; border: 1px solid #d5dbe5;
                border-radius: 6px; padding: 2px;
            }
        """)
        frame.setObjectName("GpuBar")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(6)

        # ── GPU 模式 ──
        layout.addWidget(QLabel("🎮 GPU:"))
        self.gpu_mode_combo = QComboBox()
        self.gpu_mode_combo.addItems(["自动检测", "8G 串行", "12G 去码+合成并行", "16G+ 全并行"])
        self.gpu_mode_combo.setFixedWidth(170)
        self.gpu_mode_combo.setToolTip("选择 GPU 运行模式·自动根据显存推荐")
        # 尝试自动检测
        if self.gpu_mode_index < 0:
            self.gpu_mode_combo.setCurrentIndex(0)
            QTimer.singleShot(200, self._auto_detect_gpu_mode)
        else:
            self.gpu_mode_combo.setCurrentIndex(self.gpu_mode_index + 1)
        self.gpu_mode_combo.currentIndexChanged.connect(self._on_gpu_mode_changed)
        layout.addWidget(self.gpu_mode_combo)

        # 分隔线
        sep1 = QFrame(); sep1.setFrameShape(QFrame.Shape.VLine); sep1.setStyleSheet("color:#ccc;")
        layout.addWidget(sep1)

        # ── 显存信息 ──
        layout.addWidget(QLabel("显存:"))
        self.gpu_mem_bar = QProgressBar()
        self.gpu_mem_bar.setFixedWidth(160)
        self.gpu_mem_bar.setFixedHeight(18)
        self.gpu_mem_bar.setTextVisible(True)
        self.gpu_mem_bar.setRange(0, 100)
        self.gpu_mem_bar.setValue(0)
        self.gpu_mem_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #bdc3c7; border-radius: 3px;
                background: #ecf0f1; text-align: center;
                font-size: 10px; color: #2c3e50;
            }
            QProgressBar::chunk { border-radius: 3px; }
        """)
        layout.addWidget(self.gpu_mem_bar)

        # ── 温度 ──
        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.VLine); sep2.setStyleSheet("color:#ccc;")
        layout.addWidget(sep2)
        layout.addWidget(QLabel("🌡️"))
        self.gpu_temp_label = QLabel("--°C")
        self.gpu_temp_label.setFixedWidth(45)
        self.gpu_temp_label.setStyleSheet("color: #2c3e50; font-weight: bold;")
        layout.addWidget(self.gpu_temp_label)

        # ── 安全信息 ──
        self.gpu_safety_label = QLabel("安全余量: 2.0GB")
        self.gpu_safety_label.setStyleSheet("color: #7f8c8d; font-size: 10px;")
        layout.addWidget(self.gpu_safety_label)

        # ── 冷却状态 ──
        self.gpu_cool_label = QLabel("")
        self.gpu_cool_label.setFixedWidth(100)
        layout.addWidget(self.gpu_cool_label)

        layout.addStretch()

        # ── 设置按钮 ──
        gpu_cfg_btn = QPushButton("⚙ GPU")
        gpu_cfg_btn.setFixedHeight(24)
        gpu_cfg_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        gpu_cfg_btn.setStyleSheet("""
            QPushButton {
                color: #2980b9; background: transparent;
                border: 1px solid #aab; border-radius: 4px;
                padding: 2px 10px; font-size:11px;
            }
            QPushButton:hover { background: #d6eaf8; }
        """)
        gpu_cfg_btn.setToolTip("GPU 设置：温控/显存安全余量")
        gpu_cfg_btn.clicked.connect(self._edit_gpu_config)
        layout.addWidget(gpu_cfg_btn)

        # 启动 GPU 监控定时器
        self.gpu_timer = QTimer()
        self.gpu_timer.timeout.connect(self._update_gpu_display)
        self.gpu_timer.start(3000)
        QTimer.singleShot(100, self._update_gpu_display)

        return frame

    def _update_gpu_display(self):
        """刷新 GPU 显存/温度显示"""
        data = query_nvidia_smi("memory.used,memory.total,temperature.gpu,utilization.gpu")
        if not data:
            self.gpu_mem_bar.setValue(0)
            self.gpu_mem_bar.setFormat("N/A")
            self.gpu_temp_label.setText("--°C")
            self.gpu_temp_label.setStyleSheet("color: #95a5a6; font-weight: bold;")
            return

        g0 = data[0]
        total = g0["mem_total_mb"]
        used = g0["mem_used_mb"]
        temp = g0["temp_c"]
        util = g0["util_pct"]
        pct = int(used / total * 100) if total > 0 else 0
        free = total - used
        safety = self.gpu_safety_margin

        # 显存条
        self.gpu_mem_bar.setValue(pct)
        self.gpu_mem_bar.setFormat(f"{used}MB / {total}MB ({pct}%)")
        if pct > 85:
            self.gpu_mem_bar.setStyleSheet("""
                QProgressBar { border: 1px solid #e74c3c; border-radius: 3px;
                    background: #ecf0f1; text-align: center; font-size: 10px; color: #2c3e50; }
                QProgressBar::chunk { background: #e74c3c; border-radius: 3px; }
            """)
        elif pct > 65:
            self.gpu_mem_bar.setStyleSheet("""
                QProgressBar { border: 1px solid #f39c12; border-radius: 3px;
                    background: #ecf0f1; text-align: center; font-size: 10px; color: #2c3e50; }
                QProgressBar::chunk { background: #f39c12; border-radius: 3px; }
            """)
        else:
            self.gpu_mem_bar.setStyleSheet("""
                QProgressBar { border: 1px solid #bdc3c7; border-radius: 3px;
                    background: #ecf0f1; text-align: center; font-size: 10px; color: #2c3e50; }
                QProgressBar::chunk { background: #27ae60; border-radius: 3px; }
            """)

        # 温度
        self.gpu_temp_label.setText(f"{temp}°C")
        if temp >= self.gpu_temp_limit:
            self.gpu_temp_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
            if time.time() - self._last_temp_warn > 30:
                self.log(f"🌡️ GPU 温度 {temp}°C 超过限制 {self.gpu_temp_limit}°C，将启动降温")
                self._last_temp_warn = time.time()
            self.gpu_overheated = True
            self.gpu_cool_label.setText("❄️ 降温中...")
            self.gpu_cool_label.setStyleSheet("color: #3498db; font-weight: bold;")
        elif temp >= self.gpu_temp_limit - 10:
            self.gpu_temp_label.setStyleSheet("color: #f39c12; font-weight: bold;")
            self.gpu_overheated = False
            if self.gpu_cool_label.text():
                self.gpu_cool_label.setText("⚡ 恢复中")
                self.gpu_cool_label.setStyleSheet("color: #27ae60;")
        else:
            self.gpu_temp_label.setStyleSheet("color: #27ae60; font-weight: bold;")
            self.gpu_overheated = False
            self.gpu_cool_label.setText("")
            self.gpu_cool_label.setStyleSheet("")

        # 安全信息
        margin_gb = free / 1024
        if margin_gb < (safety / 1024):
            self.gpu_safety_label.setText(f"⚠ 余量 {margin_gb:.1f}GB < {safety/1024:.0f}GB")
            self.gpu_safety_label.setStyleSheet("color: #e74c3c; font-size: 10px; font-weight: bold;")
        else:
            self.gpu_safety_label.setText(f"✓ 余量 {margin_gb:.1f}GB")
            self.gpu_safety_label.setStyleSheet("color: #27ae60; font-size: 10px;")

    def _on_gpu_mode_changed(self, idx):
        """GPU 模式切换"""
        mode_names = ["auto", "8g_serial", "12g_dc_parallel", "16g_full_parallel"]
        self.gpu_mode_index = idx - 1  # 0=auto, 1=8G, 2=12G, 3=16G+
        self.config["gpu_config"]["mode"] = mode_names[idx]
        self.config["gpu_config"]["mode_index"] = self.gpu_mode_index
        self.save_config()
        if idx == 0:
            self._auto_detect_gpu_mode()

    def _auto_detect_gpu_mode(self):
        """自动检测显存并推荐模式"""
        info = get_gpu_memory_info()
        if info:
            total_mb, _ = info
            idx = suggest_gpu_mode(total_mb)
            self.gpu_mode_combo.setCurrentIndex(idx + 1)  # +1 because index 0 is "自动"
            self.log(f"🎮 自动检测 GPU: {total_mb//1024}GB → {GPU_MODES[idx][0]}")

    def _edit_gpu_config(self):
        """GPU 设置对话框"""
        dlg = QDialog(self)
        dlg.setWindowTitle("GPU 配置")
        dlg.setMinimumWidth(420)
        layout = QFormLayout(dlg)

        # 温度限制
        temp_spin = QSpinBox(); temp_spin.setRange(60, 99)
        temp_spin.setValue(self.gpu_temp_limit)
        temp_spin.setSuffix(" °C")
        temp_spin.setToolTip("超过此温度将启动降温等待")
        layout.addRow("温度上限:", temp_spin)

        # 安全余量
        margin_spin = QSpinBox(); margin_spin.setRange(256, 8192)
        margin_spin.setValue(self.gpu_safety_margin)
        margin_spin.setSingleStep(256)
        margin_spin.setSuffix(" MB")
        margin_spin.setToolTip("处理时保留的空闲显存")
        layout.addRow("显存安全余量:", margin_spin)

        # 降温等待
        cool_spin = QSpinBox(); cool_spin.setRange(10, 300)
        cool_spin.setValue(self.gpu_cooldown_sec)
        cool_spin.setSuffix(" 秒")
        cool_spin.setSingleStep(10)
        layout.addRow("降温等待:", cool_spin)

        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        layout.addRow(btn_box)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.gpu_temp_limit = temp_spin.value()
            self.gpu_safety_margin = margin_spin.value()
            self.gpu_cooldown_sec = cool_spin.value()
            self.config["gpu_config"]["temp_limit"] = self.gpu_temp_limit
            self.config["gpu_config"]["safety_margin_mb"] = self.gpu_safety_margin
            self.config["gpu_config"]["temp_cooldown"] = self.gpu_cooldown_sec
            self.save_config()
            self.log(f"🎮 GPU 配置已更新: 温控 {self.gpu_temp_limit}°C, 余量 {self.gpu_safety_margin}MB")

    def _edit_tool_path(self, tool_name):
        """点击状态按钮 → 弹出路径修改对话框"""
        key_map = {"LADA": "lada", "JASNA": "jasna", "Whisper": "whisper",
                   "FFmpeg": "ffmpeg", "MKVToolNix": "mkvmerge"}
        key = key_map.get(tool_name, tool_name.lower())
        current_path = self.tool_paths.get(key, DEFAULT_TOOL_PATHS.get(key, ""))

        dlg = QDialog(self)
        dlg.setWindowTitle(f"设置 {tool_name} 路径")
        dlg.setMinimumWidth(500)
        layout = QVBoxLayout(dlg)

        layout.addWidget(QLabel(f"请指定 {tool_name} 的可执行文件路径："))
        path_layout = QHBoxLayout()
        path_edit = QLineEdit(current_path)
        path_layout.addWidget(path_edit, 1)
        browse_btn = QPushButton("浏览")
        browse_btn.clicked.connect(lambda: path_edit.setText(
            QFileDialog.getOpenFileName(dlg, f"选择 {tool_name}", str(Path(current_path).parent))[0] or path_edit.text()
        ))
        path_layout.addWidget(browse_btn)
        layout.addLayout(path_layout)

        # 状态显示
        status_label = QLabel("")
        layout.addWidget(status_label)

        # 检测按钮
        def check_now():
            ready, info = check_tool_ready(path_edit.text())
            if ready:
                status_label.setStyleSheet("color: #27ae60; font-weight: bold;")
                status_label.setText(f"✓ {tool_name} 就绪")
            else:
                status_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
                status_label.setText(f"✗ {tool_name} 不可用 — {info}")

        check_btn = QPushButton("检测路径")
        check_btn.clicked.connect(check_now)
        layout.addWidget(check_btn)

        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("确定")
        ok_btn.clicked.connect(lambda: [
            setattr(dlg, 'new_path', path_edit.text()),
            dlg.accept()
        ])
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(dlg.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_path = getattr(dlg, 'new_path', current_path)
            if new_path and Path(new_path).exists():
                self.tool_paths[key] = new_path
                self.config["tool_paths"] = self.tool_paths
                self.save_config()
                self.update_tool_status(tool_name, *check_tool_ready(new_path))
                self.log(f"🔧 {tool_name} 路径已更新: {new_path}")

    def initial_tool_check(self):
        """首次工具检测"""
        self.log("🔍 正在检测工具环境...")
        self.check_all_tools()

    def check_all_tools(self):
        """检测所有工具并更新状态按钮"""
        key_map = {"LADA": "lada", "JASNA": "jasna", "Whisper": "whisper",
                   "FFmpeg": "ffmpeg", "MKVToolNix": "mkvmerge"}
        for name in STATUS_TOOLS:
            key = key_map.get(name, name.lower())
            path = self.tool_paths.get(key, DEFAULT_TOOL_PATHS.get(key, ""))
            ready, info = check_tool_ready(path)
            self.update_tool_status(name, ready, info)

    def update_tool_status(self, name, ready, info=""):
        """更新单个工具状态按钮"""
        btn = self.status_btns.get(name)
        if not btn:
            return
        if ready:
            btn.setStyleSheet("""
                QPushButton {
                    color: #27ae60; background: #e8f8f0;
                    border: 1px solid #27ae60; border-radius: 4px;
                    padding: 2px 8px; margin-left: 4px; font-weight: bold;
                }
                QPushButton:hover { background: #d5f5e3; }
            """)
            btn.setText(f"● {name} ✓")
            btn.setToolTip(f"{name}: 就绪")
        else:
            btn.setStyleSheet("""
                QPushButton {
                    color: #e74c3c; background: #fdedec;
                    border: 1px solid #e74c3c; border-radius: 4px;
                    padding: 2px 8px; margin-left: 4px; font-weight: bold;
                }
                QPushButton:hover { background: #fadbd8; }
            """)
            btn.setText(f"● {name} ✗")
            btn.setToolTip(f"{name}: 不可用 — {info}")

    def _build_config_group(self):
        """构建配置区域 — 顶部通用选项 + 详细参数按钮"""
        group = QGroupBox("⚙ 配置")
        outer_layout = QVBoxLayout()
        outer_layout.setSpacing(4)

        # 第1行：输入输出目录
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("输入目录:"))
        self.input_edit = QLineEdit(self.config["input_dir"])
        self.input_edit.editingFinished.connect(self._auto_scan_input_dir)
        row1.addWidget(self.input_edit, 1)
        btn_in = QPushButton("📂")
        btn_in.setFixedWidth(30)
        btn_in.clicked.connect(lambda: self._browse_dir(self.input_edit))
        row1.addWidget(btn_in)
        row1.addWidget(QLabel("  输出目录:"))
        self.output_edit = QLineEdit(self.config["output_dir"])
        row1.addWidget(self.output_edit, 1)
        btn_out = QPushButton("📂")
        btn_out.setFixedWidth(30)
        btn_out.clicked.connect(lambda: self._browse_dir(self.output_edit))
        row1.addWidget(btn_out)
        outer_layout.addLayout(row1)

        # 第2行：引擎选择 + 功能勾选 + 详细参数按钮
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("去码引擎:"))
        self.demark_combo = QComboBox()
        self.demark_combo.addItems(["LADA", "JASNA"])
        self.demark_combo.setCurrentText(self.config["demark_engine"].upper())
        row2.addWidget(self.demark_combo)
        row2.addSpacing(8)
        row2.addWidget(QLabel("合成引擎:"))
        self.compose_combo = QComboBox()
        self.compose_combo.addItems(["FFmpeg", "MKVToolNix"])
        self.compose_combo.setCurrentText(self.config["compose_engine"].upper())
        row2.addWidget(self.compose_combo)
        row2.addSpacing(12)

        self.cb_demark = QCheckBox("去码")
        self.cb_demark.setChecked(self.config.get("do_demark", False))
        row2.addWidget(self.cb_demark)
        self.cb_subtitle = QCheckBox("字幕")
        self.cb_subtitle.setChecked(self.config.get("do_subtitle", True))
        row2.addWidget(self.cb_subtitle)
        self.cb_compose = QCheckBox("合成")
        self.cb_compose.setChecked(self.config.get("do_compose", True))
        row2.addWidget(self.cb_compose)
        row2.addStretch()

        # 详细参数按钮
        param_btn = QPushButton("⚙ 详细参数设置")
        param_btn.setFont(QFont("Microsoft YaHei", 10))
        param_btn.setStyleSheet("""
            QPushButton {
                background: #3498db; color: white;
                border-radius: 4px; padding: 6px 16px;
            }
            QPushButton:hover { background: #2980b9; }
        """)
        param_btn.clicked.connect(self._open_param_dialog)
        row2.addWidget(param_btn)
        outer_layout.addLayout(row2)

        group.setLayout(outer_layout)
        return group

    def _open_param_dialog(self):
        """打开详细参数设置对话框"""
        dlg = ParamDialog(self.config, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            updated = dlg.get_params()
            self.config.update(updated)
            # 同步 GPU 配置到主界面
            gc = updated.get("gpu_config", {})
            if gc:
                mi = gc.get("mode_index", -1)
                if mi >= 0:
                    self.gpu_mode_combo.setCurrentIndex(mi + 1)
                self.gpu_temp_limit = gc.get("temp_limit", self.gpu_temp_limit)
                self.gpu_safety_margin = gc.get("safety_margin_mb", self.gpu_safety_margin)
                self.gpu_cooldown_sec = gc.get("temp_cooldown", self.gpu_cooldown_sec)
            self.save_config()
            self.log("✓ 参数配置已更新")

    def _build_file_group(self):
        """构建文件列表（QTableWidget：文件名 | 状态）"""
        group = QGroupBox("📋 文件列表")
        layout = QVBoxLayout()
        layout.setContentsMargins(6, 8, 6, 8)

        btn_row = QHBoxLayout()
        self.add_btn = QPushButton("+ 添加文件")
        self.add_btn.setStyleSheet("QPushButton { padding: 4px 16px; }")
        self.add_btn.clicked.connect(self.add_files)
        btn_row.addWidget(self.add_btn)

        self.remove_btn = QPushButton("- 移除选中")
        self.remove_btn.setStyleSheet("QPushButton { padding: 4px 16px; }")
        self.remove_btn.clicked.connect(self.remove_files)
        btn_row.addWidget(self.remove_btn)

        self.clear_btn = QPushButton("清空列表")
        self.clear_btn.setStyleSheet("QPushButton { padding: 4px 16px; }")
        self.clear_btn.clicked.connect(self.clear_files)
        btn_row.addWidget(self.clear_btn)

        self.count_label = QLabel("共 0 个文件")
        self.count_label.setStyleSheet("color: #7f8c8d;")
        btn_row.addWidget(self.count_label)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # 表格：文件名 | 状态
        self.file_table = QTableWidget(0, 2)
        self.file_table.setHorizontalHeaderLabels(["文件名", "状态"])
        self.file_table.setAlternatingRowColors(True)
        self.file_table.setMinimumHeight(80)
        self.file_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.file_table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.file_table.horizontalHeader().setStretchLastSection(True)
        self.file_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.file_table.verticalHeader().setVisible(False)
        self.file_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.file_table, 1)

        group.setLayout(layout)
        return group

    # ─── 文件操作 ───
    def _browse_dir(self, line_edit):
        dir_path = QFileDialog.getExistingDirectory(self, "选择目录", line_edit.text())
        if dir_path:
            line_edit.setText(dir_path)
        if line_edit is self.input_edit:
            self._auto_scan_input_dir()

    def _auto_scan_input_dir(self):
        """自动扫描输入目录（含子目录）中的视频文件"""
        dir_str = self.input_edit.text().strip()
        if not dir_str or not Path(dir_str).is_dir():
            return
        video_exts = {'.mp4','.mkv','.avi','.mov','.flv','.wmv','.webm','.ts','.m2ts'}
        root = Path(dir_str)
        files = sorted(
            [f for f in root.rglob('*') if f.suffix.lower() in video_exts and f.is_file()],
            key=lambda x: x.relative_to(root).as_posix()
        )
        if not files:
            self.log(f"📂 {dir_str} 中未发现视频文件")
            return
        self.file_table.setRowCount(0)
        for f in files:
            self._add_file_to_table(str(f))
        self._update_file_count()
        self.log(f"📂 自动扫描: 找到 {len(files)} 个视频文件（含子目录）")

    def _add_file_to_table(self, file_path):
        """向表格添加一行"""
        row = self.file_table.rowCount()
        self.file_table.insertRow(row)
        # 显示相对路径（含子目录名），方便区分不同子目录的同名文件
        input_dir = self.input_edit.text().strip()
        fp = Path(file_path)
        try:
            display_name = str(fp.relative_to(input_dir)) if input_dir else fp.name
        except ValueError:
            display_name = fp.name
        name_item = QTableWidgetItem(display_name)
        name_item.setData(Qt.ItemDataRole.UserRole, file_path)
        name_item.setToolTip(file_path)
        self.file_table.setItem(row, 0, name_item)
        status_item = QTableWidgetItem("准备中")
        status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self.file_table.setItem(row, 1, status_item)
        # 默认颜色
        name_item.setForeground(QColor("#2c3e50"))
        status_item.setForeground(QColor("#7f8c8d"))

    def add_files(self):
        start_dir = self.input_edit.text()
        if not Path(start_dir).exists():
            start_dir = ""
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择视频文件", start_dir,
            "视频文件 (*.mp4 *.mkv *.avi *.mov *.flv *.wmv *.webm);;所有文件 (*.*)"
        )
        for f in files:
            self._add_file_to_table(f)
        self._update_file_count()

    def remove_files(self):
        for row in sorted(set(i.row() for i in self.file_table.selectedIndexes()), reverse=True):
            self.file_table.removeRow(row)
        self._update_file_count()

    def clear_files(self):
        self.file_table.setRowCount(0)
        self._update_file_count()

    def _update_file_count(self):
        self.count_label.setText(f"共 {self.file_table.rowCount()} 个文件")

    # ─── 日志 ───
    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self.log_text.append(line)
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        # 同时写入文件日志
        file_log(msg)

    def clear_log(self):
        self.log_text.clear()

    # ─── 处理控制 ───
    def start_processing(self):
        # 验证（无弹窗，仅日志）
        if self.file_table.rowCount() == 0:
            self.log("⚠ 请先添加要处理的文件")
            return
        if not (self.cb_demark.isChecked() or self.cb_subtitle.isChecked() or self.cb_compose.isChecked()):
            self.log("⚠ 请至少勾选一个功能")
            return

        # 收集文件
        files = []
        for i in range(self.file_table.rowCount()):
            files.append(self.file_table.item(i, 0).data(Qt.ItemDataRole.UserRole))

        # 重置所有状态为"准备中"
        for i in range(self.file_table.rowCount()):
            status_item = self.file_table.item(i, 1)
            if status_item:
                status_item.setText("准备中")
                status_item.setForeground(QColor("#7f8c8d"))
                self.file_table.item(i, 0).setForeground(QColor("#2c3e50"))

        # 更新 GPU 配置到 config
        mode_names = ["auto", "8g_serial", "12g_dc_parallel", "16g_full_parallel"]
        self.config["gpu_config"] = {
            "mode": mode_names[self.gpu_mode_combo.currentIndex()],
            "mode_index": self.gpu_mode_combo.currentIndex() - 1,
            "safety_margin_mb": self.gpu_safety_margin,
            "temp_limit": self.gpu_temp_limit,
            "temp_cooldown": self.gpu_cooldown_sec
        }

        # 使用当前 config 中的参数（由 ParamDialog 设置或默认值）
        self.config.update({
            "input_dir": self.input_edit.text(),
            "output_dir": self.output_edit.text(),
            "demark_engine": self.demark_combo.currentText().lower(),
            "compose_engine": self.compose_combo.currentText().lower(),
            "do_demark": self.cb_demark.isChecked(),
            "do_subtitle": self.cb_subtitle.isChecked(),
            "do_compose": self.cb_compose.isChecked(),
            "input_files": files,
            "tool_paths": self.tool_paths,
            "gpu_config": self.config["gpu_config"]
        })
        self.save_config()

        # 输出目录
        output_dir = Path(self.config["output_dir"])
        if not output_dir.exists():
            output_dir.mkdir(parents=True, exist_ok=True)

        gpu_mode_name = self.gpu_mode_combo.currentText()
        self.log(f"\n{'#'*60}")
        self.log(f"开始处理 {len(files)} 个文件")
        self.log(f"  · GPU 模式: {gpu_mode_name}")
        gpu_info = get_gpu_memory_info()
        if gpu_info:
            total_mb, avail_mb = gpu_info
            temp = get_gpu_temperature()
            temp_str = f", {temp}°C" if temp else ""
            self.log(f"  · GPU 显存: {avail_mb}MB/{total_mb}MB 可用{temp_str}")
        if self.cb_demark.isChecked():
            self.log(f"  · 去码引擎: {self.config['demark_engine'].upper()}")
            lp = self.config.get('lada_params', {})
            self.log(f"  · LADA: device={lp.get('device','cuda:0')}, model={lp.get('model','')}")
        if self.cb_subtitle.isChecked():
            wp = self.config.get('whisper_params', {})
            # 显示实际模型路径（海南鸡版固定模型）
            tp = self.config.get("tool_paths", DEFAULT_TOOL_PATHS)
            whisper_exe = tp.get("whisper", DEFAULT_TOOL_PATHS["whisper"])
            model_path = str(Path(whisper_exe).parent / "models")
            self.log(f"  · 字幕模型: {model_path}")
            self.log(f"  · 字幕任务: {wp.get('task','translate')}")
        if self.cb_compose.isChecked():
            self.log(f"  · 合成引擎: {self.config['compose_engine'].upper()}")
        self.log(f"  · 输出目录: {output_dir}")
        self.log(f"{'#'*60}")

        # 启动工作线程
        self.worker = WorkerThread(self.config)
        self.worker.log_signal.connect(self.log)
        self.worker.progress_signal.connect(self.progress_bar.setValue)
        self.worker.file_status_signal.connect(self._on_file_status)
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.start()

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.add_btn.setEnabled(False)
        self.remove_btn.setEnabled(False)
        self.clear_btn.setEnabled(False)
        self.progress_bar.setValue(0)

    def _on_file_status(self, file_idx, status):
        """更新文件状态显示"""
        if file_idx < self.file_table.rowCount():
            status_item = self.file_table.item(file_idx, 1)
            name_item = self.file_table.item(file_idx, 0)
            if not status_item:
                return
            status_text = {"ready":"准备中","demarking":"去码中","subtitling":"字幕中",
                           "composing":"合成中","done":"已完成","failed":"失败"}
            status_item.setText(status_text.get(status, status))
            if status == "done":
                status_item.setForeground(QColor("#27ae60"))   # 绿色
                name_item.setForeground(QColor("#27ae60"))     # 绿色
            elif status == "failed":
                status_item.setForeground(QColor("#e74c3c"))   # 红色
                name_item.setForeground(QColor("#e74c3c"))     # 红色
            elif status in ("demarking", "subtitling", "composing"):
                status_item.setForeground(QColor("#2980b9"))   # 蓝色
            else:
                status_item.setForeground(QColor("#7f8c8d"))   # 灰色

    def stop_processing(self):
        if self.worker:
            self.worker.running = False
            self.log("⛔ 后台任务收尾中（有延迟），请观察硬件占用情况再关闭本软件")

    def on_finished(self, success, msg):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.add_btn.setEnabled(True)
        self.remove_btn.setEnabled(True)
        self.clear_btn.setEnabled(True)

        if msg == "__STOPPED__":
            self.log("⛔ 用户已停止处理 - 后台任务收尾有延迟，请观察硬件占用")
        elif success:
            self.progress_bar.setValue(100)
            self.log(f"✅ {msg}")
        else:
            self.log(f"⚠ {msg}")

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.worker.running = False
            self.worker.wait(3000)
        self.save_config()
        close_file_logging()
        event.accept()
        # 等 closeEvent 返回后再退出，避免 QThreadStorage 警告
        QTimer.singleShot(0, QApplication.quit)


# ==================== 入口 ====================
def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    font = QFont("Microsoft YaHei", 10)
    app.setFont(font)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
