#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能去码字幕工具箱 v1.3
功能：视频去码（LADA / JASNA）、字幕生成（Faster-Whisper）、字幕合成（FFmpeg / MKVToolNix）
"""

import sys
import os
import shutil
import json
import subprocess
import threading
import time
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

# _force_console_utf8() called from main.py

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

# ─── PyQt6 imports (needed for QThread, pyqtSignal) ───
try:
    from PyQt6.QtCore import QThread, pyqtSignal
except ImportError:
    QThread = object  # fallback for syntax check
    pyqtSignal = lambda *a, **kw: None

class WorkerThread(QThread):
    """后台工作线程 - 顺序执行：去码 → 字幕 → 合成"""
    # 工具级别互斥锁：确保同一个软件不会同时开2个
    _demark_lock = threading.Lock()
    _whisper_lock = threading.Lock()
    _compose_lock = threading.Lock()
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(int)
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

            # 根据 GPU 模式选择串行/并行
            if self.gpu_mode in ("12g_dc_parallel", "16g_full_parallel"):
                self._run_pipeline(files, do_demark, do_subtitle, do_compose)
            else:
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
        ]
        # 可选参数
        if wp.get('patience'):
            cmd.append(f"--patience={wp['patience']}")
        cmd.append(str(input_file))
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

        compose_cwd = None
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