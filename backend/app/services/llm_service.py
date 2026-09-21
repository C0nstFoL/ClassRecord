"""通过 OpenAI 兼容接口调用大模型进行课堂总结。"""

import json
import logging
from dataclasses import dataclass

import httpx

from app.config import get_settings

settings = get_settings()

logger = logging.getLogger(__name__)


@dataclass
class HomeworkTaskData:
    content: str
    source_indexes: list[int]
    deadline: str | None = None
    details: str | None = None


@dataclass
class HomeworkExtractionResult:
    title: str
    markdown: str
    tasks: list[HomeworkTaskData]


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


async def extract_homework(recordings: list[tuple[str, str]]) -> HomeworkExtractionResult:
    if not settings.llm_api_key:
        raise RuntimeError("未配置 LLM_API_KEY，请在环境变量中设置大模型 API Key")

    summary_parts = [
        f"课程编号：{index}\n课程记录：{title}\n课堂总结：\n{text}"
        for index, (title, text) in enumerate(recordings, start=1)
    ]
    system_prompt = (
        "你是一名专业的课堂记录助手。请仅根据多节课已有的课堂总结，整理其中的待办事项、作业、练习、阅读和截止时间。"
        "合并语义重复的事项，但保留各课程独有的要求；不要重新分析课堂原文，不要补充总结中没有的信息。"
        "只输出一个 JSON 对象，不要使用 Markdown 代码块或附加说明。格式为："
        '{"title":"根据全部事项概括的简短标题","tasks":[{"content":"事项名称","source_indexes":[1],"deadline":null,"details":null}],'
        '"reminders":["提醒内容"]}。'
        "title 应概括这组作业的主题，不超过 20 个汉字，不要包含日期、Markdown 或“待办与作业”这类泛化标题。"
        "source_indexes 必须使用输入中的课程编号；同一事项涉及多节课时可以包含多个编号。"
        "deadline 和 details 未提及时必须为 null。没有待办或作业时 tasks 返回空数组。"
    )
    raw = await _chat_completion(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "\n\n---\n\n".join(summary_parts)},
        ]
    )
    return _parse_homework_result(raw, recordings)


def _parse_homework_result(
    raw: str,
    recordings: list[tuple[str, str]],
) -> HomeworkExtractionResult:
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end < start:
        raise ValueError("大模型未返回有效的待办 JSON")
    data = json.loads(raw[start : end + 1])
    raw_tasks = data.get("tasks", [])
    if not isinstance(raw_tasks, list):
        raise ValueError("大模型返回的 tasks 格式无效")

    tasks: list[HomeworkTaskData] = []
    for item in raw_tasks:
        if not isinstance(item, dict) or not isinstance(item.get("content"), str):
            continue
        content = item["content"].strip()
        if not content:
            continue
        indexes = item.get("source_indexes", [])
        source_indexes = list(
            dict.fromkeys(
                index
                for index in indexes
                if isinstance(index, int) and 1 <= index <= len(recordings)
            )
        ) if isinstance(indexes, list) else []
        deadline = item.get("deadline")
        details = item.get("details")
        tasks.append(
            HomeworkTaskData(
                content=content,
                source_indexes=source_indexes,
                deadline=deadline.strip() if isinstance(deadline, str) and deadline.strip() else None,
                details=details.strip() if isinstance(details, str) and details.strip() else None,
            )
        )

    reminders = data.get("reminders", [])
    if not isinstance(reminders, list):
        reminders = []
    reminder_texts = [item.strip() for item in reminders if isinstance(item, str) and item.strip()]
    raw_title = data.get("title")
    title = raw_title.strip().replace("\n", " ") if isinstance(raw_title, str) else ""
    title = title.strip("#*` ")[:60]
    if title in {"待办", "作业", "待办与作业"}:
        title = ""
    if not title:
        if len(tasks) == 1:
            title = tasks[0].content[:60]
        elif tasks:
            title = f"{tasks[0].content[:36]}等 {len(tasks)} 项待办"
        else:
            title = "待办与作业"

    lines = ["## 待办与作业"]
    if not tasks:
        lines.append("- 暂未发现明确的待办或作业")
    for task in tasks:
        source_names = [recordings[index - 1][0] for index in task.source_indexes]
        source_label = "、".join(source_names) if source_names else "来源未注明"
        lines.extend(
            [
                f"- [ ] **{task.content}** — {source_label}",
                f"  - 截止时间：{task.deadline or '未注明'}",
                f"  - 要求：{task.details or '未注明'}",
            ]
        )
    lines.extend(["", "## 提醒"])
    lines.extend(f"- {item}" for item in reminder_texts)
    if not reminder_texts:
        lines.append("暂无")
    return HomeworkExtractionResult(title=title, markdown="\n".join(lines), tasks=tasks)


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
