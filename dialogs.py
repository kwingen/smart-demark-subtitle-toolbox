#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""参数设置对话框"""

from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QComboBox, QGroupBox, QLabel, QLineEdit, QCheckBox,
    QSpinBox, QScrollArea, QFrame, QTabWidget, QDialogButtonBox
)
from PyQt6.QtCore import QEvent
from PyQt6.QtGui import QFont

from worker import DEFAULT_TOOL_PATHS

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