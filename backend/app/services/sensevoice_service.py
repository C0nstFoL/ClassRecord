"""基于 sherpa-onnx 的 SenseVoice 离线识别服务（中文/粤语专用，速度与准确率优于 Whisper）。

首次使用时自动从 GitHub release 下载 int8 量化模型（约 235MB）到本地缓存目录。
使用 sherpa-onnx CUDA 版轮子，支持双设备实例：上传转写走 GPU（provider=cuda），
实时录制的窗口转写走 CPU（provider=cpu）。
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

# 各设备的识别器实例（懒加载，进程内各只加载一次）
_recognizers: dict[str, object] = {}
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


def _ensure_model_files(model_dir: Path) -> tuple[str, str]:
    model_file = model_dir / "model.int8.onnx"
    tokens_file = model_dir / "tokens.txt"
    if not (model_file.exists() and tokens_file.exists()):
        _download_model(model_dir)
    return str(model_file), str(tokens_file)


def _load_recognizer(provider: str):
    import sherpa_onnx  # 延迟导入，避免未安装时影响其他语言路径

    model_dir = Path(settings.sensevoice_model_dir)
    model_file, tokens_file = _ensure_model_files(model_dir)
    logger.info("加载 SenseVoice 模型（provider=%s）: %s", provider, model_dir)
    return sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=model_file,
        tokens=tokens_file,
        use_itn=settings.sensevoice_use_itn,
        num_threads=settings.sensevoice_num_threads,
        provider=provider,
    )


def get_recognizer(device: str = "cpu"):
    """获取指定设备（"cpu" / "cuda"）的识别器，进程内各只加载一次。

    CUDA 初始化失败（驱动/库缺失等）时自动回落 CPU，不影响转写可用性。
    """
    if device == "cuda":
        if "cuda" not in _recognizers:
            with _recognizer_lock:
                if "cuda" not in _recognizers:
                    try:
                        _recognizers["cuda"] = _load_recognizer("cuda")
                    except Exception:
                        logger.exception("SenseVoice CUDA 初始化失败，回落 CPU")
                        # 此时仍持有锁，不能调 get_recognizer（会重复加锁死锁）
                        if "cpu" not in _recognizers:
                            _recognizers["cpu"] = _load_recognizer("cpu")
                        _recognizers["cuda"] = _recognizers["cpu"]
        return _recognizers["cuda"]

    if "cpu" not in _recognizers:
        with _recognizer_lock:
            if "cpu" not in _recognizers:
                _recognizers["cpu"] = _load_recognizer("cpu")
    return _recognizers["cpu"]


def transcribe_with_sensevoice(file_path: str, device: str = "cpu") -> str:
    """转写音频文件（任意 ffmpeg 支持的格式），返回纯文本。

    device：上传转写传 "cuda"（GPU 加速），实时录制窗口转写传 "cpu"。
    注意：sherpa-onnx 仅 transducer 模型支持热词（上下文偏置），SenseVoice 是
    encoder-decoder 架构不支持——传入热词会触发 C++ 层 abort 并杀死整个进程，
    因此这里绝不传热词。
    """
    recognizer = get_recognizer(device)
    samples, sample_rate = _decode_audio(file_path)
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
