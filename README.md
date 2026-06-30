# 智能去码字幕工具箱 v1.2

PyQt6 桌面应用，一键完成视频去码 + 字幕生成 + 字幕合成。

## 功能

| 步骤 | 引擎 | 说明 |
|------|------|------|
| **去码** | LADA / JASNA | GPU 加速视频马赛克修复 |
| **字幕** | Faster-Whisper | 日语→中文字幕翻译 |
| **合成** | FFmpeg / MKVToolNix | 字幕嵌入视频 |

## 依赖工具

- [LADA](https://github.com/...) — 视频去码
- [JASNA](https://github.com/...) — 视频去码（备选引擎）
- [Faster-Whisper TransWithAI](https://github.com/...) — 语音识别+翻译
- [FFmpeg](https://ffmpeg.org/) — 字幕合成
- [MKVToolNix](https://mkvtoolnix.download/) — MKV 字幕合成

## 运行

Windows 环境，需 NVIDIA GPU + CUDA：

```bat
run.bat
```

或直接：

```bash
pip install PyQt6
python main.py
```

首次运行会自动生成 `config.json`，请在界面中配置各工具的 `.exe` 路径。

## 项目文件

| 文件 | 说明 |
|------|------|
| `main.py` | 主程序（PyQt6 GUI） |
| `config.json` | 配置文件（工具路径、参数） |
| `run.bat` | Windows 启动脚本 |
| `make_zip.py` | 打包脚本 |

## 工作流程

```
输入视频 → LADA/JASNA 去码 → Faster-Whisper 字幕 → FFmpeg/MKVToolNix 合成 → 输出视频
```

支持断点续传：已完成的步骤自动跳过。
