#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能去码字幕工具箱 v1.3
功能：视频去码（LADA / JASNA）、字幕生成（Faster-Whisper）、字幕合成（FFmpeg / MKVToolNix）
"""

import sys
import os
import json
import time
from datetime import datetime
from pathlib import Path

# ─── 控制台 UTF-8 ───
def _force_console_utf8():
    if sys.platform == 'win32':
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            k32.SetConsoleOutputCP(65001)
            k32.SetConsoleCP(65001)
        except:
            pass

_force_console_utf8()

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTextEdit, QComboBox, QGroupBox,
    QFormLayout, QLineEdit, QFileDialog, QTableWidget, QTableWidgetItem,
    QHeaderView, QCheckBox, QProgressBar, QSpinBox,
    QSplitter, QFrame, QDialog, QDialogButtonBox
)
from PyQt6.QtCore import Qt, QTimer, QEvent
from PyQt6.QtGui import QFont, QColor, QPalette

# ─── 子模块 ───
from worker import (
    WorkerThread, check_tool_ready, get_gpu_memory_info, get_gpu_temperature,
    suggest_gpu_mode, query_nvidia_smi, setup_file_logging, file_log,
    close_file_logging, console_print, DEFAULT_TOOL_PATHS, STATUS_TOOLS,
    DEFAULT_INPUT_DIR, DEFAULT_OUTPUT_DIR, CONFIG_FILE, GPU_MODES,
)
from dialogs import ParamDialog


# ==================== 主窗口 ====================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # 初始化文件日志
        log_path = setup_file_logging()
        console_print("智能去码字幕工具箱 v1.3 - 处理进度将显示在此窗口")
        console_print(f"日志文件: {log_path}")
        console_print("=" * 55)
        self.config = self.load_config()
        self.tool_paths = self.config.get("tool_paths", dict(DEFAULT_TOOL_PATHS))
        self.worker = None
        self._log_max_lines = 5000  # 日志行数上限

        # GPU 监控变量（必须在 init_ui 前初始化）
        gc = self.config.get("gpu_config", {})
        self.gpu_mode_index = gc.get("mode_index", -1)
        self.gpu_temp_limit = gc.get("temp_limit", 85)
        self.gpu_safety_margin = gc.get("safety_margin_mb", 2048)
        self.gpu_cooldown_sec = gc.get("temp_cooldown", 60)
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
            "mkv_params": {
                "track_lang": "chi",
                "default_track": True,
                "forced_track": False,
                "compression": "none",
                "extra_args": ""
            },
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

    # ─── 日志 ───
    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self.log_text.append(line)
        # 限制日志行数，防止内存无限增长
        if self.log_text.document().blockCount() > self._log_max_lines:
            cursor = self.log_text.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            cursor.movePosition(cursor.MoveOperation.Down, cursor.MoveMode.KeepAnchor, 1000)
            cursor.removeSelectedText()
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        # 同时写入文件日志
        file_log(msg)

    def clear_log(self):
        self.log_text.clear()

    # ─── 界面构建 ───
    def init_ui(self):
        self.setWindowTitle("智能去码字幕工具箱 v1.3")
        self.setGeometry(200, 100, 1200, 800)
        self.setMinimumSize(1000, 650)

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

        file_group = self._build_file_group()
        file_group.setMinimumHeight(80)
        splitter.addWidget(file_group)

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
        title = QLabel("🎬 智能去码字幕工具箱 v1.3")
        title.setFont(QFont("Microsoft YaHei", 18, QFont.Weight.Bold))
        title.setStyleSheet("color: #2c3e50;")
        layout.addWidget(title)
        layout.addStretch()

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
        """构建 GPU 状态栏"""
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

        layout.addWidget(QLabel("🎮 GPU:"))
        self.gpu_mode_combo = QComboBox()
        self.gpu_mode_combo.addItems(["自动检测", "8G 串行模式", "12G 去码+合成并行", "16G+ 全并行模式"])
        self.gpu_mode_combo.setFixedWidth(170)
        self.gpu_mode_combo.setToolTip("选择 GPU 运行模式·自动根据显存推荐")
        if self.gpu_mode_index < 0:
            self.gpu_mode_combo.setCurrentIndex(0)
            QTimer.singleShot(200, self._auto_detect_gpu_mode)
        else:
            self.gpu_mode_combo.setCurrentIndex(self.gpu_mode_index + 1)
        self.gpu_mode_combo.currentIndexChanged.connect(self._on_gpu_mode_changed)
        layout.addWidget(self.gpu_mode_combo)

        sep1 = QFrame(); sep1.setFrameShape(QFrame.Shape.VLine); sep1.setStyleSheet("color:#ccc;")
        layout.addWidget(sep1)

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

        sep2 = QFrame(); sep2.setFrameShape(QFrame.Shape.VLine); sep2.setStyleSheet("color:#ccc;")
        layout.addWidget(sep2)
        layout.addWidget(QLabel("🌡️"))
        self.gpu_temp_label = QLabel("--°C")
        self.gpu_temp_label.setFixedWidth(45)
        self.gpu_temp_label.setStyleSheet("color: #2c3e50; font-weight: bold;")
        layout.addWidget(self.gpu_temp_label)

        self.gpu_safety_label = QLabel("安全余量: 2.0GB")
        self.gpu_safety_label.setStyleSheet("color: #7f8c8d; font-size: 10px;")
        layout.addWidget(self.gpu_safety_label)

        self.gpu_cool_label = QLabel("")
        self.gpu_cool_label.setFixedWidth(100)
        layout.addWidget(self.gpu_cool_label)

        layout.addStretch()

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

        # GPU 监控定时器
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
        pct = int(used / total * 100) if total > 0 else 0
        free = total - used
        safety = self.gpu_safety_margin

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

        margin_gb = free / 1024
        if margin_gb < (safety / 1024):
            self.gpu_safety_label.setText(f"⚠ 余量 {margin_gb:.1f}GB < {safety/1024:.0f}GB")
            self.gpu_safety_label.setStyleSheet("color: #e74c3c; font-size: 10px; font-weight: bold;")
        else:
            self.gpu_safety_label.setText(f"✓ 余量 {margin_gb:.1f}GB")
            self.gpu_safety_label.setStyleSheet("color: #27ae60; font-size: 10px;")

    def _on_gpu_mode_changed(self, idx):
        mode_names = ["auto", "8g_serial", "12g_dc_parallel", "16g_full_parallel"]
        self.gpu_mode_index = idx - 1
        self.config["gpu_config"]["mode"] = mode_names[idx]
        self.config["gpu_config"]["mode_index"] = self.gpu_mode_index
        self.save_config()
        if idx == 0:
            self._auto_detect_gpu_mode()

    def _auto_detect_gpu_mode(self):
        info = get_gpu_memory_info()
        if info:
            total_mb, _ = info
            idx = suggest_gpu_mode(total_mb)
            self.gpu_mode_combo.setCurrentIndex(idx + 1)
            self.log(f"🎮 自动检测 GPU: {total_mb//1024}GB → {GPU_MODES[idx][0]}")

    def _edit_gpu_config(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("GPU 配置")
        dlg.setMinimumWidth(420)
        layout = QFormLayout(dlg)

        temp_spin = QSpinBox(); temp_spin.setRange(60, 99)
        temp_spin.setValue(self.gpu_temp_limit)
        temp_spin.setSuffix(" °C")
        temp_spin.setToolTip("超过此温度将启动降温等待")
        layout.addRow("温度上限:", temp_spin)

        margin_spin = QSpinBox(); margin_spin.setRange(256, 8192)
        margin_spin.setValue(self.gpu_safety_margin)
        margin_spin.setSingleStep(256)
        margin_spin.setSuffix(" MB")
        margin_spin.setToolTip("处理时保留的空闲显存")
        layout.addRow("显存安全余量:", margin_spin)

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

        status_label = QLabel("")
        layout.addWidget(status_label)

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
        self.log("🔍 正在检测工具环境...")
        self.check_all_tools()

    def check_all_tools(self):
        key_map = {"LADA": "lada", "JASNA": "jasna", "Whisper": "whisper",
                   "FFmpeg": "ffmpeg", "MKVToolNix": "mkvmerge"}
        for name in STATUS_TOOLS:
            key = key_map.get(name, name.lower())
            path = self.tool_paths.get(key, DEFAULT_TOOL_PATHS.get(key, ""))
            ready, info = check_tool_ready(path)
            self.update_tool_status(name, ready, info)

    def update_tool_status(self, name, ready, info=""):
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
        group = QGroupBox("⚙ 配置")
        outer_layout = QVBoxLayout()
        outer_layout.setSpacing(4)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("输入目录:"))
        self.input_edit = QLineEdit(self.config["input_dir"])
        self.input_edit.editingFinished.connect(self._auto_scan_input_dir)
        row1.addWidget(self.input_edit, 1)
        btn_in = QPushButton("📂"); btn_in.setFixedWidth(30)
        btn_in.clicked.connect(lambda: self._browse_dir(self.input_edit))
        row1.addWidget(btn_in)
        row1.addWidget(QLabel("  输出目录:"))
        self.output_edit = QLineEdit(self.config["output_dir"])
        row1.addWidget(self.output_edit, 1)
        btn_out = QPushButton("📂"); btn_out.setFixedWidth(30)
        btn_out.clicked.connect(lambda: self._browse_dir(self.output_edit))
        row1.addWidget(btn_out)
        outer_layout.addLayout(row1)

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
        row = self.file_table.rowCount()
        self.file_table.insertRow(row)
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

    # ─── 处理控制 ───
    def start_processing(self):
        if self.file_table.rowCount() == 0:
            self.log("⚠ 请先添加要处理的文件")
            return
        if not (self.cb_demark.isChecked() or self.cb_subtitle.isChecked() or self.cb_compose.isChecked()):
            self.log("⚠ 请至少勾选一个功能")
            return

        files = []
        for i in range(self.file_table.rowCount()):
            files.append(self.file_table.item(i, 0).data(Qt.ItemDataRole.UserRole))

        for i in range(self.file_table.rowCount()):
            status_item = self.file_table.item(i, 1)
            if status_item:
                status_item.setText("准备中")
                status_item.setForeground(QColor("#7f8c8d"))
                self.file_table.item(i, 0).setForeground(QColor("#2c3e50"))

        mode_names = ["auto", "8g_serial", "12g_dc_parallel", "16g_full_parallel"]
        self.config["gpu_config"] = {
            "mode": mode_names[self.gpu_mode_combo.currentIndex()],
            "mode_index": self.gpu_mode_combo.currentIndex() - 1,
            "safety_margin_mb": self.gpu_safety_margin,
            "temp_limit": self.gpu_temp_limit,
            "temp_cooldown": self.gpu_cooldown_sec
        }

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
            tp = self.config.get("tool_paths", DEFAULT_TOOL_PATHS)
            whisper_exe = tp.get("whisper", DEFAULT_TOOL_PATHS["whisper"])
            model_path = str(Path(whisper_exe).parent / "models")
            self.log(f"  · 字幕模型: {model_path}")
            self.log(f"  · 字幕任务: {wp.get('task','translate')}")
        if self.cb_compose.isChecked():
            self.log(f"  · 合成引擎: {self.config['compose_engine'].upper()}")
        self.log(f"  · 输出目录: {output_dir}")
        self.log(f"{'#'*60}")

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
        if file_idx < self.file_table.rowCount():
            status_item = self.file_table.item(file_idx, 1)
            name_item = self.file_table.item(file_idx, 0)
            if not status_item:
                return
            status_text = {"ready":"准备中","demarking":"去码中","subtitling":"字幕中",
                           "composing":"合成中","done":"已完成","failed":"失败"}
            status_item.setText(status_text.get(status, status))
            if status == "done":
                status_item.setForeground(QColor("#27ae60"))
                name_item.setForeground(QColor("#27ae60"))
            elif status == "failed":
                status_item.setForeground(QColor("#e74c3c"))
                name_item.setForeground(QColor("#e74c3c"))
            elif status in ("demarking", "subtitling", "composing"):
                status_item.setForeground(QColor("#2980b9"))
            else:
                status_item.setForeground(QColor("#7f8c8d"))

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
