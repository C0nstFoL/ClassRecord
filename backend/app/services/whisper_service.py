"""基于 faster-whisper 的本地语音转写服务。"""

import logging
import os
import threading

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# 新版 huggingface_hub 默认走 xet 协议直连 xethub.hf.co，国内镜像不代理该协议，需关闭回退到普通 HTTP 下载
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from faster_whisper import WhisperModel

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

_model: WhisperModel | None = None
_model_lock = threading.Lock()


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


def transcribe_audio(file_path: str) -> str:
    """转写音频文件，返回拼接后的完整文本。"""
    model = get_model()
    kwargs: dict = {
        "language": "zh",
        "beam_size": 5,
        "vad_filter": True,
        # VAD 分段更宽松，避免把弱音/句尾切碎影响识别
        "vad_parameters": {"min_silence_duration_ms": 700, "speech_pad_ms": 400},
        # 不携带上文条件：中文长音频下该机制容易诱发重复/幻觉循环，
        # 关闭后重复文本明显减少，单段识别质量由 beam_size + VAD 保障
        "condition_on_previous_text": False,
    }
    if settings.whisper_initial_prompt:
        # hotwords 直接偏置解码词表，对专有名词的命中率比 initial_prompt 更稳定
        kwargs["hotwords"] = settings.whisper_initial_prompt
    segments, _info = model.transcribe(file_path, **kwargs)
    # 按识别分段换行拼接，保留自然的语句边界，便于阅读与后续 LLM 处理
    return "\n".join(segment.text.strip() for segment in segments).strip()
