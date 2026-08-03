#!/usr/bin/env python3
"""Tests for llm_providers.py (issue #39, parts 2/3): the vendor adapters,
.env credential loading, and redaction. Egress preflight() has its own
sibling suite, test_egress_preflight.py.

Follows this repo's established CASES/@case/main() convention (see e.g.
test_report_tokens.py, test_reprice_by_vintage.py).

NO TEST IN THIS FILE MAKES A REAL NETWORK CALL. urllib.request.urlopen is
monkeypatched at import time (see _POISON_INSTALLED below) to raise
AssertionError if anything ever calls it for real; every adapter test
monkeypatches llm_providers._post_json instead, and the one test that
exercises the real chokepoint (the induced-error redaction test) further
monkeypatches urlopen itself to raise a crafted, entirely local
urllib.error.HTTPError -- never opening a socket.

Run from the repo root:  ./.venv/bin/python analysis/test_llm_providers.py
"""
import ast
import io
import pathlib
import sys
import tempfile
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import llm_providers as lp  # noqa: E402

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


class SkipCase(Exception):
    """Raised by a case whose preconditions this checkout cannot meet."""


# ---------------------------------------------------------------------------
# Global network poison: after this module is imported, NOTHING in this
# process may open a real socket via urllib.request.urlopen. Every adapter
# conformance case mocks _post_json instead; the redaction case mocks
# urlopen itself with a crafted, local HTTPError.
# ---------------------------------------------------------------------------
def _poison_urlopen(*a, **kw):
    raise AssertionError("a test attempted a real network call via urllib.request.urlopen")


urllib.request.urlopen = _poison_urlopen


class _patched:
    """Temporarily replace an attribute on `obj`, restoring it on exit --
    used instead of unittest.mock so this file has no new dependency."""

    def __init__(self, obj, name, value):
        self.obj, self.name, self.value = obj, name, value

    def __enter__(self):
        self.old = getattr(self.obj, self.name)
        setattr(self.obj, self.name, self.value)
        return self.value

    def __exit__(self, *exc):
        setattr(self.obj, self.name, self.old)


# ---------------------------------------------------------------------------
# AC: PROVIDERS names the exact three env vars the issue specifies (GEMINI,
# not GOOGLE), and no dated snapshot model id is hardcoded anywhere.
# ---------------------------------------------------------------------------
@case
def case_providers_declare_the_exact_three_env_var_names():
    env_vars = {cfg["env_var"] for cfg in lp.PROVIDERS.values()}
    assert env_vars == {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"}, env_vars
    return f"PROVIDERS declares exactly {sorted(env_vars)}"


@case
def case_no_provider_ships_a_configured_model_id_by_default():
    for name, cfg in lp.PROVIDERS.items():
        assert cfg["default_model"] == lp.DEFAULT_MODEL_SENTINEL, (
            f"{name}: default_model is {cfg['default_model']!r}, not the sentinel -- "
            "this repo must not hardcode a dated snapshot model id")
    return f"all {len(lp.PROVIDERS)} providers use the {lp.DEFAULT_MODEL_SENTINEL!r} sentinel"


@case
def case_call_fails_closed_on_unknown_provider():
    try:
        lp.call("does-not-exist", "some-model", "sys", "user", 100)
        raise AssertionError("call() accepted an unknown provider")
    except SystemExit as e:
        assert "does-not-exist" in str(e), e
    return "call() exits naming an unknown provider"


@case
def case_call_fails_closed_on_unconfigured_model():
    for bad_model in (None, "", lp.DEFAULT_MODEL_SENTINEL):
        try:
            lp.call("anthropic", bad_model, "sys", "user", 100)
            raise AssertionError(f"call() accepted an unconfigured model {bad_model!r}")
        except SystemExit as e:
            assert "anthropic" in str(e) and "model" in str(e), e
    return "call() exits naming the provider when no real model id was ever configured"


# ---------------------------------------------------------------------------
# AC: all three providers pass an identical adapter conformance test against
# recorded response fixtures -- normal stop, truncation, 429, malformed body
# -- with zero live calls.
# ---------------------------------------------------------------------------
_ADAPTER = {"anthropic": lp._call_anthropic, "openai": lp._call_openai, "google": lp._call_google}

# Recorded (hand-authored, matching each vendor's documented native response
# shape) fixtures -- see llm_providers.py's adapter docstrings for the shapes.
_NORMAL = {
    "anthropic": {"content": [{"type": "text", "text": "hello world"}],
                  "stop_reason": "end_turn",
                  "usage": {"input_tokens": 10, "output_tokens": 5}},
    "openai": {"choices": [{"message": {"content": "hello world"}, "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 10, "completion_tokens": 5}},
    "google": {"candidates": [{"content": {"parts": [{"text": "hello world"}]},
                               "finishReason": "STOP"}],
               "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}},
}
_TRUNCATED = {
    "anthropic": {"content": [{"type": "text", "text": "cut off"}],
                  "stop_reason": "max_tokens",
                  "usage": {"input_tokens": 100, "output_tokens": 200}},
    "openai": {"choices": [{"message": {"content": "cut off"}, "finish_reason": "length"}],
               "usage": {"prompt_tokens": 100, "completion_tokens": 200}},
    "google": {"candidates": [{"content": {"parts": [{"text": "cut off"}]},
                               "finishReason": "MAX_TOKENS"}],
               "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 200}},
}
_TRUNCATED_REASON = {"anthropic": "max_tokens", "openai": "length", "google": "MAX_TOKENS"}
# Valid JSON, wrong shape -- exercises the adapter's own KeyError/IndexError handling.
_MALFORMED_SHAPE = {"unexpected": "shape, no content/choices/candidates key at all"}


@case
def case_all_three_adapters_extract_text_and_finish_reason_on_a_normal_stop():
    results = []
    for provider, fixture in _NORMAL.items():
        with _patched(lp, "_post_json", lambda *a, **kw: fixture):
            out = _ADAPTER[provider]("fake-key", "fake-model", "sys", "user", 100)
        assert out["text"] == "hello world", (provider, out)
        assert out["finish_reason"], (provider, out)
        assert set(out) == {"text", "finish_reason", "usage"}, (provider, out)
        assert out["usage"]["input_tokens"] and out["usage"]["output_tokens"], (provider, out)
        results.append(provider)
    return f"{results} all normalize a normal-stop fixture to {{text, finish_reason, usage}}"


@case
def case_all_three_adapters_surface_a_truncated_finish_reason():
    for provider, fixture in _TRUNCATED.items():
        with _patched(lp, "_post_json", lambda *a, **kw: fixture):
            out = _ADAPTER[provider]("fake-key", "fake-model", "sys", "user", 100)
        assert out["text"] == "cut off", (provider, out)
        assert out["finish_reason"] == _TRUNCATED_REASON[provider], (provider, out)
    return f"{sorted(_TRUNCATED)} all surface their vendor's own truncated finish_reason"


@case
def case_all_three_adapters_raise_providererror_on_a_429():
    for provider in _ADAPTER:
        def fake_post_json(*a, **kw):
            raise lp.ProviderError(provider, 429, "HTTP 429: rate limited")
        with _patched(lp, "_post_json", fake_post_json):
            try:
                _ADAPTER[provider]("fake-key", "fake-model", "sys", "user", 100)
                raise AssertionError(f"{provider}: adapter swallowed a 429")
            except lp.ProviderError as e:
                assert e.status == 429, (provider, e.status)
                assert e.provider == provider, (provider, e.provider)
    return f"{sorted(_ADAPTER)} all propagate a 429 as ProviderError(status=429)"


@case
def case_all_three_adapters_raise_providererror_on_a_malformed_shape():
    for provider in _ADAPTER:
        with _patched(lp, "_post_json", lambda *a, **kw: _MALFORMED_SHAPE):
            try:
                _ADAPTER[provider]("fake-key", "fake-model", "sys", "user", 100)
                raise AssertionError(f"{provider}: adapter did not reject a malformed shape")
            except lp.ProviderError as e:
                assert provider in str(e), (provider, e)
            except (KeyError, IndexError, TypeError) as e:  # pragma: no cover - the bug this guards
                raise AssertionError(
                    f"{provider}: a raw {type(e).__name__} escaped instead of ProviderError") from e
    return f"{sorted(_ADAPTER)} all raise ProviderError (never a raw KeyError) on a malformed body"


@case
def case_post_json_itself_raises_providererror_on_malformed_json_body():
    """Exercises the real chokepoint (not a mocked one): a 200 response whose
    body is not valid JSON must not let json.JSONDecodeError escape."""
    def fake_urlopen(req, timeout=None):
        class _Resp:
            def read(self_):
                return b"this is not json{{{"
            def __enter__(self_):
                return self_
            def __exit__(self_, *a):
                return False
        return _Resp()
    with _patched(urllib.request, "urlopen", fake_urlopen):
        try:
            lp._post_json("anthropic", "https://example.invalid/v1/messages", {}, {"a": 1})
            raise AssertionError("_post_json accepted a malformed JSON body")
        except lp.ProviderError as e:
            assert e.provider == "anthropic", e
            assert "malformed JSON" in str(e), e
    return "_post_json raises ProviderError (not json.JSONDecodeError) on a malformed body"


@case
def case_post_json_raises_providererror_on_socket_timeout():
    import socket

    def fake_urlopen(req, timeout=None):
        raise socket.timeout("timed out")

    with _patched(urllib.request, "urlopen", fake_urlopen):
        try:
            lp._post_json("google", "https://example.invalid/v1beta/models/x:generateContent",
                          {}, {}, timeout=5)
            raise AssertionError("_post_json accepted a socket timeout as success")
        except lp.ProviderError as e:
            assert e.status is None, e
            assert "timed out" in str(e), e
    return "_post_json raises ProviderError(status=None) on a socket timeout"


@case
def case_post_json_raises_providererror_on_url_error():
    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("name resolution failed")

    with _patched(urllib.request, "urlopen", fake_urlopen):
        try:
            lp._post_json("openai", "https://example.invalid/v1/models", {}, None, method="GET")
            raise AssertionError("_post_json accepted a URLError as success")
        except lp.ProviderError as e:
            assert e.status is None, e
            assert "network error" in str(e), e
    return "_post_json raises ProviderError(status=None) on a URLError"


@case
def case_post_json_raises_providererror_on_http_error_with_status():
    body = b'{"error": {"message": "rate limited"}}'
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError("https://example.invalid", 429, "Too Many Requests",
                                      None, io.BytesIO(body))
    with _patched(urllib.request, "urlopen", fake_urlopen):
        try:
            lp._post_json("openai", "https://example.invalid/v1/chat/completions", {}, {})
            raise AssertionError("_post_json accepted a 429 as success")
        except lp.ProviderError as e:
            assert e.status == 429, e
            assert e.provider == "openai", e
    return "_post_json raises ProviderError(status=429) on an HTTPError, not a raw HTTPError"


# ---------------------------------------------------------------------------
# list_models() plumbing -- also funnels through the one chokepoint.
# ---------------------------------------------------------------------------
@case
def case_call_end_to_end_success_path_with_a_mocked_chokepoint():
    """Exercises call()'s own body (get_api_key + dispatch), not just the
    adapter functions directly -- the other conformance cases call
    _call_anthropic/etc. themselves and never touch call()'s dispatch line."""
    env = {"ANTHROPIC_API_KEY": "sk-fabricated-end-to-end"}
    with _patched(lp, "_post_json", lambda *a, **kw: _NORMAL["anthropic"]):
        out = lp.call("anthropic", "some-model", "sys", "user", 100, env=env)
    assert out["text"] == "hello world", out
    return "call() dispatches through get_api_key() and the adapter table end to end"


@case
def case_get_api_key_fails_closed_on_unknown_provider():
    try:
        lp.get_api_key("does-not-exist", env={})
        raise AssertionError("get_api_key() accepted an unknown provider")
    except SystemExit as e:
        assert "does-not-exist" in str(e), e
    return "get_api_key() exits naming an unknown provider"


@case
def case_list_models_fails_closed_on_unknown_provider():
    try:
        lp.list_models("does-not-exist", env={})
        raise AssertionError("list_models() accepted an unknown provider")
    except SystemExit as e:
        assert "does-not-exist" in str(e), e
    return "list_models() exits naming an unknown provider"


@case
def case_list_models_unwraps_each_vendors_native_list_shape():
    fixtures = {
        "anthropic": {"data": [{"id": "model-a"}, {"id": "model-b"}]},
        "openai": {"data": [{"id": "gpt-a"}]},
        "google": {"models": [{"name": "models/gemini-a"}]},
    }
    env = {"ANTHROPIC_API_KEY": "k", "OPENAI_API_KEY": "k", "GEMINI_API_KEY": "k"}
    for provider, fixture in fixtures.items():
        with _patched(lp, "_post_json", lambda *a, **kw: fixture):
            out = lp.list_models(provider, env=env)
        assert isinstance(out, list) and out, (provider, out)
    return f"{sorted(fixtures)} list_models() all unwrap their vendor's native list shape"


# ---------------------------------------------------------------------------
# .env parsing.
# ---------------------------------------------------------------------------
@case
def case_env_parser_handles_comments_blanks_and_key_value_lines():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / ".env"
        p.write_text(
            "# a comment\n"
            "\n"
            "ANTHROPIC_API_KEY=sk-fabricated-1\n"
            "   \n"
            "# OPENAI_API_KEY=sk-should-be-ignored\n"
            "OPENAI_API_KEY=sk-fabricated-2\n"
        )
        env = lp.load_env(p)
    assert env == {"ANTHROPIC_API_KEY": "sk-fabricated-1", "OPENAI_API_KEY": "sk-fabricated-2"}, env
    return "load_env() parses KEY=VALUE lines, skipping comments and blanks"


@case
def case_env_parser_on_a_missing_file_returns_empty_dict():
    assert lp.load_env(pathlib.Path("/definitely/does/not/exist/.env")) == {}
    return "load_env() returns {} for a missing file rather than raising"


@case
def case_env_parser_skips_a_line_with_no_equals_sign():
    with tempfile.TemporaryDirectory() as d:
        p = pathlib.Path(d) / ".env"
        p.write_text("this line has no equals sign at all\nANTHROPIC_API_KEY=sk-fabricated-3\n")
        env = lp.load_env(p)
    assert env == {"ANTHROPIC_API_KEY": "sk-fabricated-3"}, env
    return "load_env() silently skips a malformed line with no '=' rather than raising"


@case
def case_missing_required_key_raises_systemexit_naming_the_exact_variable():
    try:
        lp.get_api_key("google", env={})
        raise AssertionError("get_api_key() accepted an empty env")
    except SystemExit as e:
        assert "GEMINI_API_KEY" in str(e), e
        assert ".env" in str(e), e
    return "get_api_key() exits naming GEMINI_API_KEY and pointing at .env"


@case
def case_blank_value_is_treated_as_missing_not_as_an_empty_key():
    try:
        lp.get_api_key("openai", env={"OPENAI_API_KEY": "   "})
        raise AssertionError("get_api_key() accepted a blank/whitespace-only value")
    except SystemExit as e:
        assert "OPENAI_API_KEY" in str(e), e
    return "get_api_key() treats a blank value the same as a missing one"


# ---------------------------------------------------------------------------
# Redaction.
# ---------------------------------------------------------------------------
@case
def case_redact_replaces_every_occurrence_of_every_key():
    text = "key1=sk-aaa and again sk-aaa, plus sk-bbb once"
    out = lp._redact(text, ["sk-aaa", "sk-bbb"])
    assert "sk-aaa" not in out and "sk-bbb" not in out, out
    assert out.count("[REDACTED]") == 3, out
    return "_redact() replaces every occurrence of every key with [REDACTED]"


@case
def case_redact_is_a_no_op_on_text_with_no_keys_present():
    text = "nothing secret here"
    assert lp._redact(text, ["sk-aaa"]) == text
    return "_redact() leaves clean text untouched"


@case
def case_redact_passes_none_through_unchanged():
    assert lp._redact(None, ["sk-aaa"]) is None
    return "_redact(None, ...) returns None rather than raising"


@case
def case_induced_http_error_that_echoes_the_key_is_redacted_in_the_exception():
    """The induced-error test the brief requires: a fabricated key is loaded,
    a (locally crafted, no real socket) HTTP error body is made to echo it
    back, and the resulting ProviderError must not contain the raw key."""
    fabricated_key = "sk-fabricated-TESTKEY-should-never-appear-raw"
    env = {"ANTHROPIC_API_KEY": fabricated_key}
    key = lp.get_api_key("anthropic", env=env)  # registers the secret for redaction
    body = f'{{"error": {{"message": "bad request, saw key {fabricated_key}"}}}}'.encode()

    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError("https://example.invalid", 400, "Bad Request",
                                      None, io.BytesIO(body))

    with _patched(urllib.request, "urlopen", fake_urlopen):
        try:
            lp._call_anthropic(key, "fake-model", "sys", "user", 100)
            raise AssertionError("adapter did not raise on the induced HTTP error")
        except lp.ProviderError as e:
            msg = str(e)
            assert fabricated_key not in msg, f"raw key leaked into exception message: {msg}"
            assert "[REDACTED]" in msg, f"redaction placeholder missing from: {msg}"
    return "an HTTP error body that echoes a loaded key is redacted in the raised ProviderError"


# ---------------------------------------------------------------------------
# AC: _post_json is the only outbound network call site in this module --
# no adapter or helper bypasses the chokepoint. AST-based, matching
# test_scripts_runnable.py's own AST-guard convention (walk the tree,
# scoped to enclosing FunctionDef, rather than a source substring grep).
# ---------------------------------------------------------------------------
class _SocketCallFinder(ast.NodeVisitor):
    def __init__(self):
        self.current = None
        self.hits = {}

    def visit_FunctionDef(self, node):
        prev = self.current
        self.current = node.name
        self.generic_visit(node)
        self.current = prev

    def visit_Call(self, node):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else (
            func.id if isinstance(func, ast.Name) else None)
        if name in ("Request", "urlopen"):
            self.hits.setdefault(self.current, []).append(node.lineno)
        self.generic_visit(node)


@case
def case_post_json_is_the_only_socket_opening_call_site_in_the_module():
    src = pathlib.Path(lp.__file__).read_text()
    finder = _SocketCallFinder()
    finder.visit(ast.parse(src))
    offenders = {fn: lines for fn, lines in finder.hits.items() if fn != "_post_json"}
    assert finder.hits, "no urllib.request.Request/urlopen call found at all -- guard is broken"
    assert not offenders, f"socket-opening calls outside _post_json: {offenders}"
    return "urllib.request.Request/urlopen are constructed only inside _post_json"


def main():
    listed = [fn.__name__ for fn in CASES]
    assert len(listed) == len(set(listed)), (
        f"CASES lists a case twice: {sorted(n for n in listed if listed.count(n) > 1)}")
    ran, skipped = 0, 0
    for fn in CASES:
        try:
            msg = fn()
            print(f"PASS {fn.__name__}\n     {msg}")
            ran += 1
        except SkipCase as e:
            print(f"SKIP {fn.__name__}\n     {e}")
            skipped += 1
        except AssertionError as e:
            print(f"FAIL {fn.__name__}\n     AssertionError: {e}")
            raise SystemExit(1)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {fn.__name__}\n     {type(exc).__name__}: {exc}")
            raise SystemExit(1)
    tail = f" ({skipped} skipped)" if skipped else ""
    print(f"\n{ran}/{len(CASES)} passed{tail}")


if __name__ == "__main__":
    main()
