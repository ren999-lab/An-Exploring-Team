"""Qwen3.8-Max API 客户端（赛题指定 LLM，API Key 调用）。

- 通过 DashScope 的 OpenAI 兼容接口调用
- API Key 从环境变量 DASHSCOPE_API_KEY 读取，绝不写入代码
- 每次调用的 prompt / 原始响应自动落盘到 logs/，供技术报告"推理链日志"使用
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path

from openai import OpenAI

BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
MODEL = "qwen3.8-max"  # 以百炼平台实际模型 ID 为准
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


def _log_call(tag: str, payload: dict, response: str, elapsed: float):
    """把一次完整调用写入日志文件（报告需要推理链日志）。"""
    LOG_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    record = {
        "time": ts,
        "tag": tag,
        "model": MODEL,
        "elapsed_sec": round(elapsed, 2),
        "request": payload,
        "response": response,
    }
    path = LOG_DIR / f"llm_{tag}_{ts}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def chat(tag: str, system_prompt: str, user_prompt: str,
         temperature: float = 0.1, max_retries: int = 3) -> str:
    """调用 Qwen3.8-Max，返回文本响应。tag 用于区分日志（如 spec_parse / topo_recognize）。"""
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "未设置环境变量 DASHSCOPE_API_KEY。请先在阿里云百炼申请 Key，"
            "Windows 下执行: setx DASHSCOPE_API_KEY \"sk-xxx\" 然后重开终端"
        )
    client = OpenAI(api_key=api_key, base_url=BASE_URL)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            t0 = time.time()
            resp = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                temperature=temperature,
            )
            text = resp.choices[0].message.content or ""
            path = _log_call(tag, {"messages": messages}, text, time.time() - t0)
            print(f"[llm] {tag} 完成，日志: {path}")
            return text
        except Exception as e:  # 网络抖动/限流重试
            last_err = e
            print(f"[llm] {tag} 第 {attempt} 次调用失败: {e}")
            time.sleep(2 * attempt)
    raise RuntimeError(f"Qwen API 调用失败（已重试 {max_retries} 次）: {last_err}")


def extract_json(text: str) -> dict:
    """从 LLM 响应中稳健地提取 JSON（容忍 ```json 包裹、前后杂文）。"""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("LLM 响应中未找到 JSON: " + text[:200])
    return json.loads(text[start:end + 1])
