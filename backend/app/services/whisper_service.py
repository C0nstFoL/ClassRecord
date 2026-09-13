"""基于 faster-whisper 的本地语音转写服务。"""

import logging
import os
import threading

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# 新版 huggingface_hub 默认走 xet 协议直连 xethub.hf.co，国内镜像不代理该协议，需关闭回退到普通 HTTP 下载
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from faster_whisper import WhisperModel

from app.config import get_settings
from app.services.sensevoice_service import transcribe_with_sensevoice

settings = get_settings()
logger = logging.getLogger(__name__)

_model: WhisperModel | None = None
_model_lock = threading.Lock()

# 中文/粤语走 SenseVoice（中文场景准确率与速度均优于 Whisper），其余语言走 Whisper
SENSEVOICE_LANGUAGES = {"zh", "yue"}


def get_model() -> WhisperModel:
    """惰性加载模型，进程内只加载一次，避免重复占用内存/显存。"""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                logger.info(
                    "加载 Whisper 模型 %s (device=%s, compute_type=%s)",
                    settings.whisper_model_size,
                    settings.whisper_device,
                    settings.whisper_compute_type,
                )
                _model = WhisperModel(
                    settings.whisper_model_size,
                    device=settings.whisper_device,
                    compute_type=settings.whisper_compute_type,
                )
    return _model


def transcribe_audio(file_path: str, preset: str = "default", language: str = "zh", device: str = "cpu") -> str:
    """按语言路由转写：中文/粤语 → SenseVoice，其他语言 → Whisper。返回完整文本。

    device 仅对 SenseVoice 路径生效（上传转写传 "cuda" 走 GPU，实时录制默认 CPU）；
    Whisper 路径始终由 CTranslate2 管理（走 GPU）。
    热词仅 Whisper 路径生效（hotwords + initial_prompt）；SenseVoice 是
    encoder-decoder 模型，sherpa-onnx 不支持给它传热词（会直接 abort 进程）。
    """
    if language in SENSEVOICE_LANGUAGES:
        return transcribe_with_sensevoice(file_path, device=device)
    model = get_model()
    kwargs: dict = {
        "language": language,
        "beam_size": 5,
        "vad_filter": True,
        # VAD 分段适中：太宽松会把停顿也划进语音，太紧会切碎句尾
        "vad_parameters": {"min_silence_duration_ms": 500, "speech_pad_ms": 300},
        # 携带上文条件：中文同音字高度依赖上下文，开启后跨段纠错明显提升准确率；
        # 重复/幻觉循环通过下方三个阈值拦截（置信度低、静音、压缩比异常的段落直接丢弃）
        "condition_on_previous_text": True,
        "no_speech_threshold": 0.6,
        "log_prob_threshold": -1.0,
        "compression_ratio_threshold": 2.4,
    }
    if settings.whisper_initial_prompt:
        # 显式配置的全局热词优先级最高
        preset_hotwords = settings.whisper_initial_prompt
        preset_prompt = settings.whisper_initial_prompt
    else:
        preset_conf = settings.whisper_presets.get(preset) or settings.whisper_presets["default"]
        preset_hotwords = preset_conf.get("hotwords", "")
        preset_prompt = preset_conf.get("prompt", "")
    if preset_hotwords:
        # hotwords 直接偏置解码词表，对专有名词的命中率比 initial_prompt 更稳定
        kwargs["hotwords"] = preset_hotwords
    if preset_prompt:
        kwargs["initial_prompt"] = preset_prompt
    segments, _info = model.transcribe(file_path, **kwargs)
    # 按识别分段换行拼接，保留自然的语句边界，便于阅读与后续 LLM 处理
    return "\n".join(segment.text.strip() for segment in segments).strip()
