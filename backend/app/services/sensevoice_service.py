"""基于 sherpa-onnx 的 SenseVoice 离线识别服务（中文/粤语专用，速度与准确率优于 Whisper）。

首次使用时自动从 HF 镜像下载 int8 量化模型（约 235MB）到本地缓存目录。
"""

import logging
import re
import tarfile
import threading
import urllib.request
from pathlib import Path

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

_MODEL_TARBALL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17.tar.bz2"
)
# SenseVoice 输出中会带 <|zh|><|NEUTRAL|><|Speech|> 等特殊标记，统一剥掉
_TAG_RE = re.compile(r"<\|[^|]*\|>")

_recognizer = None
_recognizer_lock = threading.Lock()


def _download_model(model_dir: Path) -> None:
    """下载并解压 SenseVoice 模型（幂等，进程内加锁避免并发重复下载）。"""
    model_dir.mkdir(parents=True, exist_ok=True)
    tarball = model_dir / "sense-voice.tar.bz2"
    logger.info("开始下载 SenseVoice 模型（约 235MB）: %s", _MODEL_TARBALL)
    tmp = tarball.with_suffix(".part")
    urllib.request.urlretrieve(_MODEL_TARBALL, tmp)
    tmp.rename(tarball)
    with tarfile.open(tarball, "r:bz2") as tf:
        for member in tf.getmembers():
            name = Path(member.name).name
            if name in ("model.int8.onnx", "tokens.txt"):
                member.name = name
                tf.extract(member, model_dir)
    tarball.unlink(missing_ok=True)
    logger.info("SenseVoice 模型下载完成: %s", model_dir)


def get_recognizer():
    """惰性加载 SenseVoice 离线识别器，进程内只加载一次。"""
    global _recognizer
    if _recognizer is None:
        with _recognizer_lock:
            if _recognizer is None:
                import sherpa_onnx  # 延迟导入，避免未安装时影响其他语言路径

                model_dir = Path(settings.sensevoice_model_dir)
                model_file = model_dir / "model.int8.onnx"
                tokens_file = model_dir / "tokens.txt"
                if not (model_file.exists() and tokens_file.exists()):
                    _download_model(model_dir)
                logger.info("加载 SenseVoice 模型: %s", model_dir)
                _recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                    model=str(model_file),
                    tokens=str(tokens_file),
                    use_itn=settings.sensevoice_use_itn,
                    num_threads=settings.sensevoice_num_threads,
                )
    return _recognizer


def transcribe_with_sensevoice(file_path: str, hotwords: str = "") -> str:
    """转写音频文件（任意 ffmpeg 支持的格式），返回纯文本。

    hotwords：逗号/顿号分隔的热词表（课程场景预设），通过上下文偏置提升
    专有名词命中率；为空时不启用。
    """
    recognizer = get_recognizer()
    samples, sample_rate = _decode_audio(file_path)
    if hotwords:
        # 统一分隔符为逗号（sherpa-onnx 要求逗号分隔）
        normalized = re.sub(r"[、，\s]+", ",", hotwords.strip()).strip(",")
        stream = recognizer.create_stream(normalized)
    else:
        stream = recognizer.create_stream()
    stream.accept_waveform(sample_rate, samples)
    recognizer.decode_stream(stream)
    text = _TAG_RE.sub("", stream.result.text).strip()
    return text


def _decode_audio(file_path: str, target_sr: int = 16000) -> tuple["np.ndarray", int]:
    """用 PyAV 解码任意音视频格式为 16kHz 单声道 float32。"""
    import av
    import numpy as np

    container = av.open(file_path)
    resampler = av.AudioResampler(format="flt", layout="mono", rate=target_sr)
    chunks: list[np.ndarray] = []
    try:
        for frame in container.decode(audio=0):
            for resampled in resampler.resample(frame):
                chunks.append(resampled.to_ndarray().reshape(-1))
    finally:
        container.close()
    if not chunks:
        raise ValueError("音频文件中未找到音轨")
    return np.concatenate(chunks).astype(np.float32), target_sr
