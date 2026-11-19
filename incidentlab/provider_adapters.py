"""Provider wire formats. The agent engines consume normalized tool calls only."""
import copy
import json


class InvalidToolOutput(ValueError):
    pass


class AnthropicAdapter:
    origin = "https://api.anthropic.com"
    path = "/v1/messages"

    def headers(self, key):
        return {"x-api-key": key, "anthropic-version": "2023-06-01"}

    def request(self, model, system, messages, tools, max_tokens, parallel):
        return {"model": model, "system": system, "messages": messages,
                "tools": tools, "max_tokens": max_tokens,
                "tool_choice": {"type": "any", "disable_parallel_tool_use": not parallel}}

    def response(self, value):
        if value.get("stop_reason") not in {"tool_use", "end_turn", "max_tokens"}:
            raise InvalidToolOutput("stop_reason")
        content = value["content"]
        if not isinstance(content, list) or len(content) > 10 or any(not isinstance(c, dict) for c in content):
            raise InvalidToolOutput("content")
        calls = [c for c in content if c.get("type") == "tool_use"]
        return content, calls, value.get("usage", {})


class OpenRouterAdapter:
    origin = "https://openrouter.ai"
    path = "/api/v1/chat/completions"

    def headers(self, key):
        return {"Authorization": "Bearer " + key}

    def request(self, model, system, messages, tools, max_tokens, parallel):
        output = [{"role": "system", "content": system}]
        for message in messages:
            role, content = message["role"], message["content"]
            if isinstance(content, str):
                output.append({"role": role, "content": content})
                continue
            if not isinstance(content, list):
                raise InvalidToolOutput("message_content")
            text, calls, results, metadata = [], [], [], {}
            for block in content:
                kind = block.get("type")
                if kind == "text":
                    text.append(block["text"])
                elif kind == "tool_use" and role == "assistant":
                    calls.append({"id": block["id"], "type": "function", "function": {
                        "name": block["name"], "arguments": json.dumps(block["input"])}})
                elif kind == "tool_result" and role == "user":
                    result = block["content"]
                    if not isinstance(result, str):
                        result = json.dumps(result)
                    if block.get("is_error"):
                        result = "Tool validation error: " + result
                    results.append({"role": "tool", "tool_call_id": block["tool_use_id"], "content": result})
                elif kind == "provider_metadata" and role == "assistant":
                    metadata = block["metadata"]
                else:
                    raise InvalidToolOutput("message_block")
            if role == "assistant":
                assistant = {"role": role, "content": "\n".join(text) or None}
                if calls:
                    assistant["tool_calls"] = calls
                # Preserve provider-required reasoning metadata only in the ephemeral model transcript.
                if "reasoning_details" in metadata:
                    assistant["reasoning_details"] = copy.deepcopy(metadata["reasoning_details"])
                output.append(assistant)
            else:
                # Every tool_call_id is paired before the following user instruction.
                output.extend(results)
                if text:
                    output.append({"role": role, "content": "\n".join(text)})
        functions = [{"type": "function", "function": {
            "name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
            for t in tools]
        return {"model": model, "messages": output, "tools": functions,
                "max_tokens": max_tokens, "tool_choice": "required", "parallel_tool_calls": parallel}

    def response(self, value):
        choices = value["choices"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise InvalidToolOutput("choices")
        message = choices[0]["message"]
        calls, content = [], []
        if message.get("content"):
            content.append({"type": "text", "text": message["content"]})
        raw_calls = message.get("tool_calls") or []
        if not isinstance(raw_calls, list) or len(raw_calls) > 6:
            raise InvalidToolOutput("tool_count")
        for call in raw_calls:
            if call.get("type") != "function":
                raise InvalidToolOutput("tool_type")
            arguments = call["function"]["arguments"]
            if not isinstance(arguments, str):
                raise InvalidToolOutput("tool_arguments")
            try:
                arguments = json.loads(arguments)
            except ValueError:
                raise InvalidToolOutput("arguments_json") from None
            if not isinstance(arguments, dict):
                raise InvalidToolOutput("arguments_object")
            normalized = {"type": "tool_use", "id": call["id"], "name": call["function"]["name"], "input": arguments}
            calls.append(normalized)
            content.append(normalized)
        if message.get("reasoning_details"):
            content.append({"type": "provider_metadata", "metadata": {
                "reasoning_details": copy.deepcopy(message["reasoning_details"])}})
        usage = value.get("usage", {})
        return content, calls, {"input_tokens": usage.get("prompt_tokens", 0), "output_tokens": usage.get("completion_tokens", 0)}
