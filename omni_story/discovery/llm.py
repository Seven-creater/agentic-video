"""Browser Use adapter: Qwen JSON Object + local schema, no invisible POST retries."""
from __future__ import annotations

import base64
import asyncio
from copy import deepcopy
import json
import os
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse

from ..pipeline import json_sha, sha, write
from ..api import curl_json
from .state import DiscoveryStopped


def model_config():
    base = os.environ.get("QWEN_BROWSER_BASE_URL") or os.environ.get(
        "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    parsed = urlparse(base)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError("browser_API_base_must_be_credential_free_https")
    return {"model": os.environ.get("QWEN_BROWSER_MODEL", "qwen3.8-27b"),
            "base_url": base.rstrip("/"), "enable_thinking": False,
            "response_format": "json_object", "transport_retries": 0,
            "max_completion_tokens": 4096}


def archive_messages(state, messages):
    recorded = deepcopy(messages)
    index = state.data["qwen_calls"] + 1
    n = 0
    for message in recorded:
        if not isinstance(message.get("content"), list):
            continue
        for part in message["content"]:
            image_url = part.get("image_url", {})
            url = image_url.get("url", "")
            if isinstance(url, str) and url.startswith("data:image/"):
                prefix, encoded = url.split(",", 1)
                suffix = ".jpg" if "jpeg" in prefix else ".png"
                image = state.output / "screenshots" / f"{index:03d}_{n}{suffix}"
                image.parent.mkdir(exist_ok=True)
                image.write_bytes(base64.b64decode(encoded))
                image_url["url"] = "artifact:" + str(image.relative_to(state.output))
                n += 1
    return recorded


def reject_unknown_fields(raw, parsed):
    """Browser Use models may silently ignore extras; invalid action keys must be repaired."""
    if getattr(type(parsed), "__pydantic_root_model__", False):
        reject_unknown_fields(raw, parsed.root)
        return
    fields = getattr(type(parsed), "model_fields", None)
    if isinstance(raw, dict) and fields is not None:
        allowed = {field.alias or name: name for name, field in fields.items()}
        for key, value in raw.items():
            if key not in allowed:
                raise ValueError("unknown_model_field:" + key)
            reject_unknown_fields(value, getattr(parsed, allowed[key]))
    elif isinstance(raw, list) and isinstance(parsed, list):
        for value, item in zip(raw, parsed):
            reject_unknown_fields(value, item)


def make_llm(state):
    # Imports are deliberately lazy so the original stdlib-only pipeline still works.
    from browser_use import ChatOpenAI
    from browser_use.llm.openai.serializer import OpenAIMessageSerializer
    from browser_use.llm.views import ChatInvokeCompletion
    from openai.types.chat import ChatCompletion

    key = os.environ.get("QWEN_BROWSER_API_KEY") or os.environ.get("DASHSCOPE_API_KEY")
    if not key:
        raise ValueError("QWEN_BROWSER_API_KEY_or_DASHSCOPE_API_KEY_missing")
    config = model_config()
    curl = shutil.which("curl.exe" if sys.platform == "win32" else "curl")
    if not curl:
        raise ValueError("curl_missing")

    class QwenBrowser(ChatOpenAI):
        async def ainvoke(self, messages, output_format=None, **kwargs):
            serialized = OpenAIMessageSerializer.serialize_messages(messages)
            schema = output_format.model_json_schema() if output_format else None
            serialized.insert(0, {"role": "system", "content":
                "Return a JSON object only. Follow this action schema exactly. "
                "Example of nested action parameters: {\"action\":[{\"navigate\":{\"url\":\"https://www.douyin.com/\"}}]}.\n"
                + (json.dumps(schema, ensure_ascii=False) if schema else "")})
            original = deepcopy(serialized)
            for attempt in (0, 1):
                request = {"messages": archive_messages(state, serialized), "model_config": config,
                           "actual_messages_sha256": json_sha(serialized), "format_repair": bool(attempt),
                           "output_schema": schema, "adapter_sha256": sha(Path(__file__))}
                call, folder = state.begin_call("qwen", "browser", request)
                try:
                    transport = await asyncio.to_thread(curl_json, curl, config["base_url"] + "/chat/completions",
                        key, "POST", {"model": self.model, "messages": serialized,
                            "response_format": {"type": "json_object"}, "max_tokens": 4096,
                            "temperature": 0.2, "enable_thinking": False}, timeout=120)
                except Exception as exc:
                    write(folder / "transport_failure.json", {"error_type": type(exc).__name__})
                    raise DiscoveryStopped("paid_request_outcome_unknown:" + call["id"]) from exc
                # Record reception before parsing so malformed HTTP bodies cannot look like lost submissions.
                state.finish_call(call, folder, transport,
                                  status="received" if 200 <= transport["http_status"] < 300 else "rejected")
                if not 200 <= transport["http_status"] < 300:
                    raise DiscoveryStopped("Qwen_HTTP_" + str(transport["http_status"]))
                saved = json.loads(transport["body_text"])
                call["usage"] = saved.get("usage") or {}
                state.save()
                response = ChatCompletion.model_validate(saved)
                try:
                    text = response.choices[0].message.content
                    if response.choices[0].finish_reason == "length":
                        raise ValueError("model_output_truncated")
                    raw = json.loads(text)
                    parsed = output_format.model_validate_json(text, strict=True) if output_format else raw
                    if output_format:
                        reject_unknown_fields(raw, parsed)
                    if hasattr(parsed, "action"):
                        if len(parsed.action) != 1 or len(parsed.action[0].model_dump(exclude_none=True)) != 1:
                            raise ValueError("exactly_one_browser_action_per_step_required")
                    if not output_format and not isinstance(parsed, dict):
                        raise ValueError("JSON_object_required")
                    write(folder / "validation.json", {"status": "pass"})
                    return ChatInvokeCompletion(completion=parsed if output_format else text,
                                                usage=self._get_usage(response), stop_reason=response.choices[0].finish_reason)
                except (ValueError, TypeError, KeyError, IndexError) as exc:
                    write(folder / "validation.json", {"status": "invalid_protocol", "error": str(exc)})
                    if attempt:
                        state.run_error = "Qwen_protocol_repair_exhausted"
                        raise DiscoveryStopped("Qwen_protocol_repair_exhausted") from exc
                    serialized = original + [
                        {"role": "assistant", "content": response.choices[0].message.content or ""},
                        {"role": "user", "content": "Repair the JSON/schema only; do not change the intended action. Error: " + str(exc)}]

    return QwenBrowser(model=config["model"], api_key=key, base_url=config["base_url"],
                       max_retries=0, timeout=120, frequency_penalty=None)
