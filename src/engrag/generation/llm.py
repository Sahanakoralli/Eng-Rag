"""One interface for OpenAI, Anthropic, Gemini and NVIDIA, so you can switch with LLM_PROVIDER in .env."""
import os
import re
import time

from engrag.config import settings

_sleep = time.sleep  # replaced in tests
_notify = lambda msg: print(msg, flush=True)  # noqa: E731  (replaced in tests)
_RETRYABLE = ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE", "500", "INTERNAL", "DEADLINE")


def _retry(fn, delays=(5, 15, 40)):
    """The free Gemini tier has low per-minute limits: wait and retry instead of failing the whole run."""
    for attempt in range(len(delays) + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if attempt == len(delays) or not any(t in str(e) for t in _RETRYABLE):
                raise
            _notify(f"    API busy or rate-limited; waiting {delays[attempt]}s, then retry {attempt + 1}/{len(delays)}...")
            _sleep(delays[attempt])


def _gemini_client():
    from google import genai

    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key or key == "your_key_here":
        raise RuntimeError("GEMINI_API_KEY is not set (add it to .env or Colab Secrets).")
    return genai.Client(api_key=key)


def _thinking_config(model: str):
    """Gemini 3 uses thinking_level (it cannot be switched off); 2.5 Flash uses thinking_budget (0 = off)."""
    from google.genai import types

    m = model.lower()
    if "gemini-3" in m:
        return types.ThinkingConfig(thinking_level="low")  # 'minimal' is rejected by 3.8 Flash
    if "gemini-2.5" in m and "pro" not in m:
        return types.ThinkingConfig(thinking_budget=0)
    return None


def _explain_model_error(e: Exception, model: str) -> Exception:
    msg = str(e)
    if "404" in msg and ("NOT_FOUND" in msg or "not found" in msg.lower() or "no longer available" in msg.lower()):
        return RuntimeError(
            f"Gemini model '{model}' is not available to your key.\nGoogle said: {msg[:300]}\n"
            "Fix: run `python -m engrag.cli list-models` to see which models your key can use, then set "
            "LLM_MODEL=<name> in .env (leave it blank to use the project default)."
        )
    return e


def _gemini_generate(model: str, contents, system: str, max_tokens: int, temperature: float) -> str:
    from google.genai import types

    if model.lower().startswith(("gpt-", "claude", "o1", "o3")):
        raise ValueError(f"LLM_MODEL='{model}' is not a Gemini model. Leave LLM_MODEL blank (or set e.g. "
                         "gemini-3.8-flash) in .env when LLM_PROVIDER=gemini.")
    client = _gemini_client()

    def call(use_thinking_cfg: bool, out_tokens: int):
        cfg = {"system_instruction": system or None, "temperature": temperature, "max_output_tokens": out_tokens}
        tc = _thinking_config(model) if use_thinking_cfg else None
        if tc is not None:
            cfg["thinking_config"] = tc
        return client.models.generate_content(model=model, contents=contents,
                                              config=types.GenerateContentConfig(**cfg))

    use_cfg = True
    r = None
    # Thinking tokens count towards the output limit, so if the answer is cut off, retry with more room.
    for out_tokens in (max(max_tokens * 4, 1024), max(max_tokens * 16, 4096)):
        try:
            r = _retry(lambda: call(use_cfg, out_tokens))
        except Exception as e:  # noqa: BLE001
            if use_cfg and "thinking" in str(e).lower():
                use_cfg = False  # this model rejects our thinking setting: use its default
                r = _retry(lambda: call(False, out_tokens))
            else:
                raise _explain_model_error(e, model) from e
        text = (r.text or "").strip()
        if text:
            return text
        reason = str(getattr(r.candidates[0], "finish_reason", "")) if getattr(r, "candidates", None) else "no candidates"
        if "MAX_TOKENS" not in reason.upper():
            break
    raise RuntimeError(f"Gemini returned no text (finish reason: {reason}). The answer may have been blocked or cut off.")


def _nvidia_client():
    from openai import OpenAI

    key = os.getenv("NVIDIA_API_KEY")
    if not key or key == "your_key_here":
        raise RuntimeError("NVIDIA_API_KEY is not set (get one at build.nvidia.com; it starts with nvapi-).")
    return OpenAI(base_url=settings.nvidia_base_url, api_key=key, timeout=180)


def _strip_think(text: str) -> str:
    """Reasoning models may leave their thoughts in the text: keep only the final answer."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    if "</think>" in text:  # the opening tag was part of the prompt template
        text = text.rsplit("</think>", 1)[1]
    return text.strip()


def _nvidia_chat(model: str, user_content, system: str, max_tokens: int, temperature: float) -> str:
    """NVIDIA's hosted API (OpenAI-compatible). Reasoning tokens count towards max_tokens, so thinking is OFF by
    default (fast, cheap, and the answer cannot be eaten by hidden thinking); set NVIDIA_THINKING=1 to turn it on."""
    client = _nvidia_client()
    thinking = settings.nvidia_thinking
    limit = max(max_tokens * 8, 4096) if thinking else max_tokens
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user_content}]
    extra = None
    if "nemotron" in model.lower():
        extra = {"chat_template_kwargs": {"enable_thinking": thinking}}
        if thinking:
            extra["reasoning_budget"] = min(limit // 2, 16384)

    def call(with_extra: bool):
        kw = {"extra_body": extra} if (with_extra and extra) else {}
        return client.chat.completions.create(model=model, messages=messages, max_tokens=limit,
                                              temperature=temperature, **kw)

    try:
        r = _retry(lambda: call(True))
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        if extra and any(t in msg for t in ("chat_template", "enable_thinking", "reasoning_budget", "extra")):
            r = _retry(lambda: call(False))  # this model does not understand the thinking switch
        elif "404" in msg or "not found" in msg:
            raise RuntimeError(f"NVIDIA model '{model}' was not found for your key. Run "
                               "`python -m engrag.cli list-models` and set LLM_MODEL in .env.") from e
        else:
            raise
    choice = r.choices[0]
    text = _strip_think(choice.message.content or "")
    if not text:
        raise RuntimeError(f"NVIDIA returned no answer (finish reason: {choice.finish_reason}). If thinking is on, the "
                           "hidden reasoning may have used the whole token budget: set NVIDIA_THINKING=0.")
    return text


def list_nvidia_models(contains: str | None = None) -> list[str]:
    """Model ids available to your NVIDIA key (the catalog is large, so filter with `contains`)."""
    ids = sorted(m.id for m in _nvidia_client().models.list())
    return [i for i in ids if not contains or contains.lower() in i.lower()]


def list_gemini_models() -> list[str]:
    """Models your key can use for text generation."""
    names = []
    for m in _gemini_client().models.list():
        if "generateContent" in (getattr(m, "supported_actions", None) or []):
            names.append(m.name.removeprefix("models/"))
    return sorted(names)


def complete(prompt: str, system: str = "", max_tokens: int = 1000, temperature: float = 0.0) -> str:
    provider = settings.llm_provider
    if provider == "openai":
        from openai import OpenAI

        client = OpenAI()  # reads OPENAI_API_KEY
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        r = client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return r.choices[0].message.content or ""

    if provider == "anthropic":
        import anthropic

        client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        r = client.messages.create(
            model=settings.llm_model,
            system=system or anthropic.NOT_GIVEN,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return "".join(b.text for b in r.content if b.type == "text")

    if provider == "gemini":
        return _gemini_generate(settings.llm_model, prompt, system, max_tokens, temperature)

    if provider == "nvidia":
        return _nvidia_chat(settings.llm_model, prompt, system, max_tokens, temperature)

    raise ValueError(f"Unknown LLM_PROVIDER '{provider}'. Use 'openai', 'anthropic', 'gemini' or 'nvidia'.")


CAPTION_PROMPT = (
    "This image comes from a construction/engineering document. Write a factual description that will "
    "be used for text search. State the kind of image (drawing, plan, chart, table, photo, diagram), what "
    "it shows, and copy any visible labels, dimensions, part numbers, units, values and legend text. "
    "Maximum 120 words. If it is only a logo, icon, decoration or blank, reply exactly: SKIP"
)


def describe_image(png: bytes, prompt: str = CAPTION_PROMPT, max_tokens: int = 300) -> str:
    """Vision call (OpenAI or Anthropic, per LLM_PROVIDER). Returns '' for decorative images."""
    import base64

    b64 = base64.b64encode(png).decode()
    provider, model = settings.llm_provider, settings.vision_model
    if provider == "openai":
        from openai import OpenAI

        r = OpenAI().chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]}],
        )
        text = r.choices[0].message.content or ""
    elif provider == "anthropic":
        import anthropic

        r = anthropic.Anthropic().messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                {"type": "text", "text": prompt},
            ]}],
        )
        text = "".join(b.text for b in r.content if b.type == "text")
    elif provider == "nvidia":  # the Omni model reads images; same message shape as OpenAI
        text = _nvidia_chat(model, [{"type": "text", "text": prompt},
                                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}],
                            "", max_tokens, 0.0)
    elif provider == "gemini":
        from google.genai import types

        text = _gemini_generate(model, [types.Part.from_bytes(data=png, mime_type="image/png"), prompt],
                                "", max_tokens, 0.0)
    else:
        raise ValueError(f"Unknown LLM_PROVIDER '{provider}'.")
    text = text.strip()
    return "" if text.upper().startswith("SKIP") else text
