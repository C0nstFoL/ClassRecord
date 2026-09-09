from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 应用
    app_secret_key: str = "change-me-in-production"
    public_base_url: str = "http://localhost:8000"
    frontend_after_login_path: str = "/"
    cors_origins: str = ""  # 逗号分隔，开发环境下用于允许前端开发服务器跨域携带 cookie
    disable_auth: bool = False  # 仅本地测试使用：跳过登录鉴权，生产环境必须保持 False

    # Session / Cookie
    session_cookie_name: str = "classrecord_session"
    session_max_age: int = 60 * 60 * 24 * 7  # 7 天
    cookie_secure: bool = True  # 生产环境（HTTPS 反向代理）必须为 True

    # Zitadel OIDC
    zitadel_issuer: str = ""
    zitadel_client_id: str = ""
    zitadel_client_secret: str = ""

    # 存储
    database_url: str = "sqlite:///./data/classrecord.db"
    storage_dir: str = "./data/recordings"

    # Whisper
    whisper_model_size: str = "large-v3"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    # 领域热词提示（课程专有名词、人名、术语等，逗号或顿号分隔），
    # 通过 initial_prompt 偏置识别结果，对专有名词准确率提升明显。
    # 设置后全局生效，优先级高于下方按课程预设的热词
    whisper_initial_prompt: str = ""
    # 按课程类型的热词预设：前端录制时选择，key 通过接口传入。
    # 可在 .env 中以 JSON 覆盖，如 WHISPER_PRESETS='{"cs": {"label": "计算机", "hotwords": "...", "prompt": "..."}}'
    whisper_presets: dict[str, dict[str, str]] = {
        "default": {
            "label": "默认增强",
            "hotwords": "",
            "prompt": "以下是普通话课堂录音的转写，内容为老师讲课与课堂讨论。",
        },
        "cs": {
            "label": "计算机",
            "hotwords": (
                "Python、Java、C++、数据结构、算法、操作系统、计算机网络、数据库、"
                "机器学习、深度学习、神经网络、大语言模型、前端、后端、Linux、Git、Docker"
            ),
            "prompt": "以下是大学计算机课堂的转写，内容涉及编程语言、数据结构、算法与计算机专业术语。",
        },
        "politics": {
            "label": "思政课",
            "hotwords": (
                "马克思主义、毛泽东思想、中国特色社会主义、社会主义核心价值观、"
                "唯物辩证法、实事求是、改革开放、现代化建设、思想道德修养、法治"
            ),
            "prompt": "以下是高校思想政治理论课的转写，内容涉及马克思主义理论与思想政治教育相关概念。",
        },
        "math": {
            "label": "数学",
            "hotwords": (
                "极限、导数、积分、微分方程、矩阵、行列式、特征值、概率、"
                "随机变量、期望、方差、向量空间、线性变换、级数"
            ),
            "prompt": "以下是大学数学课堂的转写，内容涉及微积分、线性代数与概率统计等数学概念。",
        },
        "english": {
            "label": "英语",
            "hotwords": "",
            "prompt": "以下是大学英语课堂的转写，内容可能中英文混合，请正确保留英文单词与句子。",
        },
    }

    # 实时流式转写
    live_transcribe_interval_seconds: float = 3.0  # 累积多久音频后跑一次增量转写
    live_window_seconds: float = 30.0  # 锚定窗口：攒满该秒数即整体归档，锚点前移（无重叠，保证长课堂耗时恒定）
    live_segment_max_chars: int = 800  # 分段小结：转写文本累积达到该字数即触发
    live_segment_max_seconds: float = 300.0  # 分段小结：距上次小结超过该时长即触发（取先到者）

    # SenseVoice（中文/粤语识别专用模型，准确率与速度均优于 Whisper）
    # 首次使用时自动从 HF 镜像下载（约 235MB）到该目录
    sensevoice_model_dir: str = "./data/models/sensevoice"
    sensevoice_num_threads: int = 2
    sensevoice_use_itn: bool = True

    # LLM（OpenAI 兼容接口）
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_summary_prompt: str = (
        "你是一名专业的课堂记录助手。请根据下面的课堂语音转写文本，生成一份结构化的课堂总结。"
        "严格使用以下 Markdown 结构（二级标题必须带 emoji，与示例完全一致）：\n"
        "## 📌 课堂主题\n（一句话概括本节课主题）\n\n"
        "## 💡 重点内容\n- 要点一\n- 要点二\n（分条列出，每条尽量简洁）\n\n"
        "## ✅ 待办与作业\n- 待办一\n（如没有提到待办/作业，写：暂无）\n\n"
        "## 📝 简要总结\n（用 2-3 句话总结本节课内容）\n\n"
        "转写文本来自语音识别，可能存在同音字错误、口语化表达或识别引擎产生的重复片段，"
        "请先在理解的基础上修正这些错误、去除重复，再进行总结；无法理解的内容直接跳过，不要编造。"
        "使用中文输出，不要添加额外的说明文字。"
    )
    # 实时录制分段小结专用：文本短、上下文少，用轻量结构，避免生搬整课四段式
    llm_segment_prompt: str = (
        "你是一名课堂记录助手。这是一节课堂中最近一段的语音识别转写片段，"
        "请用 2-4 条简洁的要点概括该片段中的内容（Markdown 无序列表），"
        "片段可能不完整或突兀，只总结能理解的部分，不要编造。"
        "语音识别可能存在同音字错误和重复片段，请先修正再总结。使用中文，不要额外说明。"
    )

    @property
    def oidc_redirect_uri(self) -> str:
        return f"{self.public_base_url.rstrip('/')}/auth/callback"

    @property
    def frontend_after_login_url(self) -> str:
        return f"{self.public_base_url.rstrip('/')}{self.frontend_after_login_path}"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def log_file_path(self) -> str:
        return str(Path(self.storage_dir).parent / "app.log")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)
    Path(settings.database_url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
    return settings
