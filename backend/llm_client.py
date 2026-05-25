"""
LLM 客户端 — 直接 HTTP 调用 OpenAI 兼容 API。
借鉴 OpenHanako 的 provider-compat 架构，绕过 litellm 的消息格式问题。
支持 DeepSeek/Qwen/Gemini/OpenRouter 等 OpenAI 兼容 provider。
"""
import json
import httpx
from typing import AsyncGenerator, Optional
from backend.config import get_settings


def _is_deepseek_provider() -> bool:
    settings = get_settings()
    provider = (settings.llm_provider or "").lower()
    base_url = (settings.get_provider_base_url(settings.llm_provider) or "").lower()
    return provider == "deepseek" or "api.deepseek.com" in base_url


def _sanitize_messages(messages: list[dict]) -> list[dict]:
    is_deepseek = _is_deepseek_provider()
    clean = []
    for msg in messages:
        role = msg.get("role", "")
        if role not in ("system", "user", "assistant", "tool"):
            continue
        m = {"role": role}

        if role in ("system", "user"):
            m["content"] = msg.get("content", "") or ""

        elif role == "assistant":
            tcs = msg.get("tool_calls")
            if tcs and isinstance(tcs, list) and len(tcs) > 0:
                m["content"] = msg.get("content") or ""
                if is_deepseek and "reasoning_content" in msg:
                    rc = msg["reasoning_content"]
                    if isinstance(rc, str) and rc.strip():
                        m["reasoning_content"] = rc
                fixed_tcs = []
                for tc in tcs:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function")
                    if isinstance(fn, dict) and "name" in fn:
                        args = fn.get("arguments", "{}")
                        if not isinstance(args, str):
                            args = json.dumps(args, ensure_ascii=False)
                        fixed_tcs.append({
                            "id": tc.get("id", f"call_{len(fixed_tcs)}"),
                            "type": tc.get("type", "function"),
                            "function": {"name": fn["name"], "arguments": args},
                        })
                    elif "name" in tc:
                        args = tc.get("arguments", "{}")
                        if not isinstance(args, str):
                            args = json.dumps(args, ensure_ascii=False)
                        fixed_tcs.append({
                            "id": tc.get("id", f"call_{len(fixed_tcs)}"),
                            "type": "function",
                            "function": {"name": tc["name"], "arguments": args},
                        })
                if fixed_tcs:
                    m["tool_calls"] = fixed_tcs
                else:
                    m["content"] = msg.get("content") or ""
            else:
                m["content"] = msg.get("content", "") or ""
                if is_deepseek and "reasoning_content" in msg:
                    rc = msg["reasoning_content"]
                    if isinstance(rc, str) and rc.strip():
                        m["reasoning_content"] = rc

        elif role == "tool":
            m["content"] = msg.get("content", "") or ""
            m["tool_call_id"] = msg.get("tool_call_id", "")

        clean.append(m)

    validated = []
    pending_tool_ids = set()
    for m in clean:
        role = m.get("role", "")
        if role == "assistant" and "tool_calls" in m:
            for tc in m["tool_calls"]:
                tc_id = tc.get("id", "")
                if tc_id:
                    pending_tool_ids.add(tc_id)
            validated.append(m)
        elif role == "tool":
            tc_id = m.get("tool_call_id", "")
            if tc_id and tc_id in pending_tool_ids:
                pending_tool_ids.discard(tc_id)
                validated.append(m)
            else:
                if validated and validated[-1].get("role") in ("assistant", "user"):
                    validated[-1]["content"] = (validated[-1].get("content") or "") + f"\n[工具结果: {m.get('content', '')[:200]}]"
        else:
            validated.append(m)

    return validated


def _build_request(messages: list[dict], model: str, tools: Optional[list[dict]] = None,
                   temperature: float = 0.7, max_tokens: int = 4096) -> dict:
    body = {
        "model": model,
        "messages": _sanitize_messages(messages),
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if tools:
        clean_tools = []
        for t in tools:
            if isinstance(t, dict) and "function" in t:
                clean_tools.append({"type": "function", "function": t["function"]})
            elif isinstance(t, dict) and "type" in t:
                clean_tools.append(t)
        if clean_tools:
            body["tools"] = clean_tools
    return body


def _call_api_sync(body: dict) -> httpx.Response:
    settings = get_settings()
    api_key = settings.get_provider_key(settings.llm_provider)
    base_url = settings.get_provider_base_url(settings.llm_provider)
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp = httpx.post(endpoint, headers=headers, json=body, timeout=120.0)
    return resp


def _parse_response(resp: httpx.Response) -> dict:
    if resp.status_code != 200:
        try:
            err_data = resp.json()
            err_msg = err_data.get("error", {}).get("message", "") if isinstance(err_data, dict) else ""
        except Exception:
            err_msg = resp.text[:300]
        return {"content": f"[LLM调用失败] HTTP {resp.status_code}: {err_msg}", "error": f"HTTP {resp.status_code}"}

    data = resp.json()
    choice = data.get("choices", [{}])[0]
    msg = choice.get("message", {})
    content = msg.get("content") or ""
    result = {"content": content}

    reasoning_content = msg.get("reasoning_content")
    if reasoning_content and isinstance(reasoning_content, str):
        result["reasoning_content"] = reasoning_content

    tool_calls_raw = msg.get("tool_calls")
    if tool_calls_raw and isinstance(tool_calls_raw, list):
        parsed_tcs = []
        for tc in tool_calls_raw:
            fn = tc.get("function", {})
            args_str = fn.get("arguments", "{}")
            try:
                args = json.loads(args_str) if isinstance(args_str, str) else args_str
            except json.JSONDecodeError:
                args = {}
            parsed_tcs.append({
                "id": tc.get("id", ""),
                "name": fn.get("name", ""),
                "arguments": args,
            })
        if parsed_tcs:
            result["tool_calls"] = parsed_tcs

    return result


def chat(
    messages: list[dict],
    model: Optional[str] = None,
    tools: Optional[list[dict]] = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
) -> str:
    settings = get_settings()
    model_id = model or settings.default_model
    body = _build_request(messages, model_id, tools, temperature, max_tokens)
    try:
        resp = _call_api_sync(body)
        result = _parse_response(resp)
        return result.get("content", "")
    except Exception as e:
        return f"[LLM调用失败] {e}"


def chat_with_tools(
    messages: list[dict],
    model: Optional[str] = None,
    tools: Optional[list[dict]] = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> dict:
    settings = get_settings()
    model_id = model or settings.default_model
    body = _build_request(messages, model_id, tools, temperature, max_tokens)
    try:
        resp = _call_api_sync(body)
        return _parse_response(resp)
    except Exception as e:
        return {"content": f"[LLM调用失败] {e}", "error": str(e)}


async def chat_stream(
    messages: list[dict],
    model: Optional[str] = None,
    tools: Optional[list[dict]] = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
) -> AsyncGenerator[str, None]:
    settings = get_settings()
    model_id = model or settings.default_model
    api_key = settings.get_provider_key(settings.llm_provider)
    base_url = settings.get_provider_base_url(settings.llm_provider)
    endpoint = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    body = _build_request(messages, model_id, tools, temperature, max_tokens)
    body["stream"] = True

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", endpoint, headers=headers, json=body) as resp:
                if resp.status_code != 200:
                    err_text = await resp.aread()
                    yield f"\n[LLM调用失败] HTTP {resp.status_code}"
                    return
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content")
                        if content:
                            yield content
                    except json.JSONDecodeError:
                        continue
    except Exception as e:
        yield f"\n[LLM调用失败] {e}"
