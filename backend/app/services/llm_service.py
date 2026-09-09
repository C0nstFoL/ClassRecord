"""通过 OpenAI 兼容接口调用大模型进行课堂总结。"""

import logging

import httpx

from app.config import get_settings

settings = get_settings()

logger = logging.getLogger(__name__)


async def summarize_transcript(transcript_text: str, title: str | None = None) -> str:
    if not settings.llm_api_key:
        raise RuntimeError("未配置 LLM_API_KEY，请在环境变量中设置大模型 API Key")

    user_content = (
        f"课堂主题：{title}\n\n课堂语音转写文本：\n{transcript_text}"
        if title
        else transcript_text
    )
    return await _chat_completion(
        [
            {"role": "system", "content": settings.llm_summary_prompt},
            {"role": "user", "content": user_content},
        ]
    )


async def summarize_segment(segment_text: str) -> str:
    """实时录制的分段小结：文本短、上下文少，使用轻量专用 prompt。"""
    if not settings.llm_api_key:
        raise RuntimeError("未配置 LLM_API_KEY，请在环境变量中设置大模型 API Key")

    return await _chat_completion(
        [
            {"role": "system", "content": settings.llm_segment_prompt},
            {"role": "user", "content": segment_text},
        ]
    )


async def answer_question(transcript_text: str, question: str) -> str:
    if not settings.llm_api_key:
        raise RuntimeError("未配置 LLM_API_KEY，请在环境变量中设置大模型 API Key")

    system_prompt = (
        "你是一名专业的课堂记录助手。用户会给你一段课堂语音转写文本，以及一个关于这段内容的问题。"
        "请仅根据转写文本中的信息，用中文简洁准确地回答用户的问题。"
        "如果转写文本中没有足够信息回答该问题，请直接说明文本中未提及，不要编造内容。"
    )
    return await _chat_completion(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"课堂转写文本：\n{transcript_text}\n\n问题：{question}"},
        ]
    )


async def _chat_completion(messages: list[dict[str, str]]) -> str:
    url = f"{settings.llm_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": settings.llm_model,
        "messages": messages,
        "temperature": 0.3,
    }
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}

    # 整节课转写文本很长，LLM 生成耗时可能超过 3 分钟，读超时须足够宽
    # （连接超时单独收紧，快速暴露网络不通问题）
    timeout = httpx.Timeout(300.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(2):
            try:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                break
            except httpx.TimeoutException:
                if attempt == 0:
                    logger.warning("LLM 请求超时，正在重试（第 2 次）")
                    continue
                raise  # 重试后仍超时，向上抛出由调用方记录
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
