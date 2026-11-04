import pytest

from engrag.generation import llm


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setattr(llm.settings.__class__, "llm_provider", "nope", raising=False)
    object.__setattr__(llm.settings, "llm_provider", "nope")
    with pytest.raises(ValueError):
        llm.complete("hi")


def _fake_openai(text):
    from unittest.mock import MagicMock
    fake = MagicMock()
    fake.choices = [MagicMock(message=MagicMock(content=text))]
    return fake


def test_describe_image_openai_sends_image_and_handles_skip(monkeypatch):
    from unittest.mock import patch
    object.__setattr__(llm.settings, "llm_provider", "openai")
    with patch("openai.OpenAI") as C:
        create = C.return_value.chat.completions.create
        create.return_value = _fake_openai("Site plan, scale 1:100, grid A-D")
        assert llm.describe_image(b"\x89PNG fake") == "Site plan, scale 1:100, grid A-D"
        content = create.call_args.kwargs["messages"][0]["content"]
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        create.return_value = _fake_openai("SKIP")
        assert llm.describe_image(b"x") == ""


# ---------------- Gemini ----------------
pytest.importorskip("google.genai")


class _Resp:
    def __init__(self, text, finish="STOP"):
        self.text = text
        self.candidates = [type("C", (), {"finish_reason": finish})()]


class _FakeGemini:
    def __init__(self, behaviours):
        self.calls, self._b = [], list(behaviours)
        self.models = self

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        b = self._b.pop(0)
        if isinstance(b, Exception):
            raise b
        return b


@pytest.fixture
def gemini(monkeypatch):
    object.__setattr__(llm.settings, "llm_provider", "gemini")
    object.__setattr__(llm.settings, "llm_model", "gemini-2.5-flash")
    object.__setattr__(llm.settings, "vision_model", "gemini-2.5-flash")
    monkeypatch.setattr(llm, "_sleep", lambda s: None)
    holder = {}
    def install(*behaviours):
        holder["c"] = _FakeGemini(behaviours)
        monkeypatch.setattr(llm, "_gemini_client", lambda: holder["c"])
        return holder["c"]
    yield install
    object.__setattr__(llm.settings, "llm_provider", "openai")
    object.__setattr__(llm.settings, "llm_model", "gpt-4o-mini")


def test_gemini_25_flash_switches_thinking_off_with_budget_zero(gemini):
    c = gemini(_Resp("25 Nm [1]"))
    assert llm.complete("q?", system="be brief", max_tokens=300) == "25 Nm [1]"
    cfg = c.calls[0]["config"]
    assert cfg.system_instruction == "be brief" and cfg.temperature == 0.0
    assert cfg.max_output_tokens == 1200 and cfg.thinking_config.thinking_budget == 0
    assert c.calls[0]["model"] == "gemini-2.5-flash" and c.calls[0]["contents"] == "q?"


def test_gemini_retries_rate_limit_then_succeeds(gemini):
    c = gemini(Exception("429 RESOURCE_EXHAUSTED quota"), Exception("503 UNAVAILABLE"), _Resp("ok"))
    assert llm.complete("q") == "ok" and len(c.calls) == 3


def test_gemini_gives_up_after_repeated_rate_limits(gemini):
    gemini(*[Exception("429 RESOURCE_EXHAUSTED")] * 4)
    with pytest.raises(Exception, match="429"):
        llm.complete("q")


def test_gemini_does_not_retry_real_errors(gemini):
    c = gemini(Exception("400 API key not valid"), _Resp("never reached"))
    with pytest.raises(Exception, match="API key"):
        llm.complete("q")
    assert len(c.calls) == 1


def test_gemini_falls_back_when_model_cannot_disable_thinking(gemini):
    c = gemini(Exception("400 Budget 0 is invalid. This model only works in thinking mode"), _Resp("ok"))
    assert llm.complete("q") == "ok"
    assert c.calls[0]["config"].thinking_config is not None and c.calls[1]["config"].thinking_config is None


def test_gemini_empty_answer_raises_clear_error(gemini):
    gemini(_Resp("", finish="SAFETY"))
    with pytest.raises(RuntimeError, match="SAFETY"):
        llm.complete("q")


def test_gemini_rejects_openai_model_name(gemini):
    gemini(_Resp("x"))
    object.__setattr__(llm.settings, "llm_model", "gpt-4o-mini")
    with pytest.raises(ValueError, match="not a Gemini model"):
        llm.complete("q")


def test_gemini_vision_sends_image_bytes_and_handles_skip(gemini):
    c = gemini(_Resp("Site plan, scale 1:100"), _Resp("SKIP"))
    assert llm.describe_image(b"\x89PNG-bytes") == "Site plan, scale 1:100"
    part = c.calls[0]["contents"][0]
    assert part.inline_data.data == b"\x89PNG-bytes" and part.inline_data.mime_type == "image/png"
    assert llm.describe_image(b"x") == ""


def test_missing_gemini_key_is_explained(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="GEMINI_API_KEY"):
        llm._gemini_client()


def test_gemini3_uses_thinking_level_low_not_budget(gemini):
    object.__setattr__(llm.settings, "llm_model", "gemini-3.8-flash")
    c = gemini(_Resp("ok"))
    assert llm.complete("q") == "ok"
    tc = c.calls[0]["config"].thinking_config
    assert str(tc.thinking_level).endswith("LOW") and tc.thinking_budget is None


def test_unknown_model_family_sends_no_thinking_config(gemini):
    object.__setattr__(llm.settings, "llm_model", "gemini-2.5-pro")
    c = gemini(_Resp("ok"))
    llm.complete("q")
    assert c.calls[0]["config"].thinking_config is None


def test_retired_model_404_gives_actionable_message(gemini):
    gemini(Exception("404 NOT_FOUND. models/gemini-2.5-flash is no longer available to new users"))
    with pytest.raises(RuntimeError, match="list-models"):
        llm.complete("q")


def test_cut_off_by_thinking_is_retried_with_more_room(gemini):
    object.__setattr__(llm.settings, "llm_model", "gemini-3.8-flash")
    c = gemini(_Resp("", finish="FinishReason.MAX_TOKENS"), _Resp("full answer"))
    assert llm.complete("q", max_tokens=300) == "full answer"
    assert c.calls[1]["config"].max_output_tokens > c.calls[0]["config"].max_output_tokens


def test_list_gemini_models_filters_to_text_generation(monkeypatch):
    M = lambda n, acts: type("M", (), {"name": n, "supported_actions": acts})()
    client = type("C", (), {"models": type("Ms", (), {"list": staticmethod(lambda: [
        M("models/gemini-3.8-flash", ["generateContent"]), M("models/text-embedding-x", ["embedContent"]),
        M("models/gemini-3.6-flash", ["generateContent", "countTokens"])])})()})()
    monkeypatch.setattr(llm, "_gemini_client", lambda: client)
    assert llm.list_gemini_models() == ["gemini-3.6-flash", "gemini-3.8-flash"]


def test_rate_limit_waits_are_announced(gemini, monkeypatch):
    notes = []
    monkeypatch.setattr(llm, "_notify", notes.append)
    gemini(Exception("429 RESOURCE_EXHAUSTED"), _Resp("ok"))
    assert llm.complete("q") == "ok"
    assert len(notes) == 1 and "waiting 5s" in notes[0] and "retry 1/3" in notes[0]


# ---------------- NVIDIA (OpenAI-compatible hosted API) ----------------
from types import SimpleNamespace as NS  # noqa: E402


def _nv_resp(text, finish="stop"):
    return NS(choices=[NS(message=NS(content=text), finish_reason=finish)])


class _FakeNV:
    def __init__(self, behaviours, model_ids=()):
        self.calls, self._b = [], list(behaviours)
        self.chat = NS(completions=self)
        self.models = NS(list=lambda: [NS(id=i) for i in model_ids])

    def create(self, **kw):
        self.calls.append(kw)
        b = self._b.pop(0)
        if isinstance(b, Exception):
            raise b
        return b


@pytest.fixture
def nvidia(monkeypatch):
    old = (llm.settings.llm_provider, llm.settings.llm_model, llm.settings.vision_model, llm.settings.nvidia_thinking)
    for k, v in (("llm_provider", "nvidia"), ("llm_model", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"),
                 ("vision_model", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"), ("nvidia_thinking", False)):
        object.__setattr__(llm.settings, k, v)
    monkeypatch.setattr(llm, "_sleep", lambda s: None)
    monkeypatch.setattr(llm, "_notify", lambda m: None)
    def install(*behaviours, model_ids=()):
        c = _FakeNV(behaviours, model_ids)
        monkeypatch.setattr(llm, "_nvidia_client", lambda: c)
        return c
    yield install
    for k, v in zip(("llm_provider", "llm_model", "vision_model", "nvidia_thinking"), old):
        object.__setattr__(llm.settings, k, v)


def test_nvidia_default_turns_thinking_off_and_sends_system_prompt(nvidia):
    c = nvidia(_nv_resp("25 Nm [1]"))
    assert llm.complete("q?", system="be brief", max_tokens=600, temperature=0.2) == "25 Nm [1]"
    kw = c.calls[0]
    assert kw["model"].endswith("reasoning") and kw["max_tokens"] == 600 and kw["temperature"] == 0.2
    assert kw["messages"] == [{"role": "system", "content": "be brief"}, {"role": "user", "content": "q?"}]
    assert kw["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}


def test_nvidia_thinking_mode_gets_a_much_bigger_token_budget(nvidia):
    object.__setattr__(llm.settings, "nvidia_thinking", True)
    c = nvidia(_nv_resp("answer"))
    llm.complete("q", max_tokens=600)
    kw = c.calls[0]
    assert kw["max_tokens"] == 4800 and kw["extra_body"]["chat_template_kwargs"]["enable_thinking"] is True
    assert kw["extra_body"]["reasoning_budget"] == 2400


def test_nvidia_strips_leaked_thinking(nvidia):
    nvidia(_nv_resp("<think>let me see...</think>The torque is 25 Nm."), _nv_resp("long hidden reasoning</think>Final answer."))
    assert llm.complete("q") == "The torque is 25 Nm."
    assert llm.complete("q") == "Final answer."


def test_nvidia_empty_answer_explains_the_token_trap(nvidia):
    nvidia(_nv_resp("", finish="length"))
    with pytest.raises(RuntimeError, match="NVIDIA_THINKING=0"):
        llm.complete("q")


def test_nvidia_retries_rate_limits_and_does_not_retry_bad_keys(nvidia):
    c = nvidia(Exception("Error code: 429 - rate limit"), _nv_resp("ok"))
    assert llm.complete("q") == "ok" and len(c.calls) == 2
    c = nvidia(Exception("401 Unauthorized"), _nv_resp("never"))
    with pytest.raises(Exception, match="401"):
        llm.complete("q")
    assert len(c.calls) == 1


def test_nvidia_falls_back_when_model_rejects_the_thinking_switch(nvidia):
    object.__setattr__(llm.settings, "llm_model", "nvidia/nemotron-something-else")
    c = nvidia(Exception("400 unknown field chat_template_kwargs"), _nv_resp("ok"))
    assert llm.complete("q") == "ok" and "extra_body" in c.calls[0] and "extra_body" not in c.calls[1]


def test_non_nemotron_nvidia_models_get_no_thinking_switch(nvidia):
    object.__setattr__(llm.settings, "llm_model", "meta/llama-3.3-70b-instruct")
    c = nvidia(_nv_resp("ok"))
    llm.complete("q")
    assert "extra_body" not in c.calls[0]


def test_nvidia_unknown_model_gives_actionable_error(nvidia):
    nvidia(Exception("404 Not Found: model does not exist"))
    with pytest.raises(RuntimeError, match="list-models"):
        llm.complete("q")


def test_nvidia_vision_sends_an_image_and_handles_skip(nvidia):
    c = nvidia(_nv_resp("Floor plan, level 2, scale 1:100"), _nv_resp("SKIP"))
    assert llm.describe_image(b"\x89PNG") == "Floor plan, level 2, scale 1:100"
    content = c.calls[0]["messages"][-1]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,") and content[0]["type"] == "text"
    assert llm.describe_image(b"x") == ""


def test_nvidia_model_listing_filters(nvidia):
    nvidia(model_ids=["meta/llama-3.3-70b-instruct", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning", "nvidia/nemotron-4-340b-instruct"])
    assert llm.list_nvidia_models("nemotron") == ["nvidia/nemotron-3-nano-omni-30b-a3b-reasoning", "nvidia/nemotron-4-340b-instruct"]
    assert len(llm.list_nvidia_models()) == 3


def test_missing_nvidia_key_is_explained(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="nvapi-"):
        llm._nvidia_client()


def test_nvidia_default_model_comes_from_config():
    from engrag.config import DEFAULT_MODELS
    assert DEFAULT_MODELS["nvidia"] == "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
