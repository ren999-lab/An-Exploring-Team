"""Qwen3.8-Max API 客户端（赛题指定 LLM，API Key 调用）。

传输层说明（实测结论，技术报告可用作工程依据）
------------------------------------------------
赛题指定的 `qwen3.8-max` **只在 OpenAI 兼容端点提供服务**：

    POST https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions   -> 200
    dashscope SDK 默认 /api/v1 (Generation.call)                             -> 400
        InvalidParameter: url error, please check url!
    （对照：/api/v1 + qwen-max -> 200，说明 Key 与网络正常，是模型/端点不匹配）

因此默认传输层用**标准库 urllib** 直连 compatible-mode 端点：
既绕开了上面的 400，也不需要引入任何第三方 LLM 服务客户端库。
若要用官方 SDK（例如换成 /api/v1 上的模型），设 DSH_LLM_TRANSPORT=dashscope 即可。

其它要点
--------
* API Key 从环境变量 DASHSCOPE_API_KEY 读取，绝不写入代码。
* `qwen3.8-max` 是思考型模型，响应里 `content` 是最终答案、
  `reasoning_content` 是思维链。赛题技术报告明确要求"推理链日志
  （Chain-of-Thought）"，所以这里把两者一起落盘到 logs/。
"""

import json
import os
import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

BASE_URL = os.environ.get("DASHSCOPE_BASE_URL",
                          "https://dashscope.aliyuncs.com/compatible-mode/v1")
MODEL = os.environ.get("DASHSCOPE_MODEL", "qwen3.8-max")
TRANSPORT = os.environ.get("DSH_LLM_TRANSPORT", "compatible")  # compatible | dashscope
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
TIMEOUT = float(os.environ.get("DASHSCOPE_TIMEOUT", "180"))


def is_available() -> bool:
    """是否具备调用条件。"""
    return bool(os.environ.get("DASHSCOPE_API_KEY"))


def _log_call(tag, payload, response, elapsed, reasoning="", usage=None,
              error=None):
    """把一次完整调用写入日志文件（报告需要推理链日志）。"""
    LOG_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    record = {
        "time": ts,
        "tag": tag,
        "transport": TRANSPORT,
        "model": MODEL,
        "base_url": BASE_URL,
        "elapsed_sec": round(elapsed, 2),
        "request": payload,
        "reasoning_content": reasoning,   # 思维链：技术报告"推理链日志"素材
        "response": response,
        "usage": usage,
    }
    if error:
        record["error"] = error
    path = LOG_DIR / f"llm_{tag}_{ts}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _post_compatible(api_key, body):
    """标准库直连 OpenAI 兼容端点，返回 (content, reasoning, usage)。"""
    url = BASE_URL.rstrip("/") + "/chat/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")[:400]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from None

    msg = (data.get("choices") or [{}])[0].get("message") or {}
    return (msg.get("content") or "",
            msg.get("reasoning_content") or "",
            data.get("usage"))


def _call_dashscope_sdk(api_key, body):
    """可选传输层：阿里云官方 SDK（仅适用于 /api/v1 上可用的模型）。"""
    import dashscope
    from dashscope import Generation

    if BASE_URL and "compatible-mode" not in BASE_URL:
        dashscope.base_http_api_url = BASE_URL
    dashscope.api_key = api_key
    resp = Generation.call(
        model=body["model"],
        messages=body["messages"],
        temperature=body.get("temperature", 0.1),
        max_tokens=body.get("max_tokens"),
        result_format="message",
    )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.code} {resp.message}")
    m = resp.output.choices[0].message
    get = m.get if hasattr(m, "get") else (lambda k, d=None: getattr(m, k, d))
    return get("content") or "", get("reasoning_content") or "", resp.usage


def chat(tag: str, system_prompt: str, user_prompt: str,
         temperature: float = 0.1, max_retries: int = 3,
         max_tokens: int = 2048) -> str:
    """调用 Qwen3.8-Max，返回文本响应。tag 用于区分日志（如 spec_parse）。"""
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "未设置环境变量 DASHSCOPE_API_KEY。请先在阿里云百炼申请 Key，"
            "Windows 下执行: setx DASHSCOPE_API_KEY \"sk-xxx\" 然后重新打开终端"
        )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    body = {"model": MODEL, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens}
    sender = _call_dashscope_sdk if TRANSPORT == "dashscope" else _post_compatible

    last_err = None
    for attempt in range(1, max_retries + 1):
        t0 = time.time()
        try:
            text, reasoning, usage = sender(api_key, body)
            path = _log_call(tag, body, text, time.time() - t0, reasoning, usage)
            print(f"[llm] {tag} 完成（{time.time() - t0:.1f}s，"
                  f"思维链 {len(reasoning)} 字）-> {path}")
            return text
        except Exception as e:
            last_err = e
            _log_call(tag, body, "", time.time() - t0, error=str(e))
            print(f"[llm] {tag} 第 {attempt} 次调用失败: {e}")
            if attempt < max_retries:
                time.sleep(2 * attempt)
    raise RuntimeError(f"Qwen API 调用失败（已重试 {max_retries} 次）: {last_err}")


def extract_json(text: str) -> dict:
    """从 LLM 响应中稳健地提取 JSON（容忍 ```json 包裹、前后杂文）。"""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("LLM 响应中未找到 JSON: " + text[:200])
    return json.loads(text[start:end + 1])
