#!/usr/bin/env python3
"""Provider adapters, .env credential loading, and the egress safety gate for
issue #39 part 2/3 (generate the report with the reader's own paid LLM key,
no agentic coding tool). The orchestrator/generator script and prose_lint.py
are separate, later work (issue #39 part 5) -- this module only knows how to
talk to a vendor's API and how to refuse to leak this household's private
data while doing it. Nothing here is wired to run automatically; the future
orchestrator calls preflight() before it calls call() on the request it is
about to send.

CHOKEPOINT
  _post_json() is the ONLY function in this module that constructs a
  urllib.request.Request or opens a socket (stdlib urllib.request only --
  zero new dependencies; verify with `git diff requirements.txt`). Every
  vendor adapter and both list-models paths funnel through it, so an egress
  audit -- and test_llm_providers.py's AST-based guard -- has exactly one
  call site to check.

PROVIDERS
  Anthropic, OpenAI, Google Gemini, each vendor's OWN native REST shape (not
  an OpenAI-compat shim -- Anthropic's and Google's compat endpoints are
  second-class and this issue explicitly rejects that route). No dated
  snapshot model id is hardcoded anywhere in this file: PROVIDERS[*]
  ["default_model"] is the literal sentinel DEFAULT_MODEL_SENTINEL, and
  call() fails closed (SystemExit) if no real model id was ever configured.
  list_models() calls each vendor's own model-list endpoint so a real run
  picks a current id from the vendor instead of a guess written in this repo.

CREDENTIALS
  load_env() is a ~10-line KEY=VALUE parser for a repo-root .env file (see
  .env.example) -- no python-dotenv dependency. get_api_key() fails closed
  (SystemExit, naming the exact variable and pointing at .env) when the key
  the chosen provider needs is missing. No key is ever accepted as a CLI
  argument anywhere in this module. Every loaded key value is registered
  with _register_secret() and _redact()/_redact_loaded() scrub it out of
  every exception message and log line this module produces -- see the
  induced-error redaction test in test_llm_providers.py.

EGRESS PREFLIGHT
  preflight() is the privacy gate CLAUDE.md section 4 requires for this new
  network egress path: every item contributing to a request body must be on
  a closed allowlist (a git-tracked file under data/, report-template.html,
  a caller-asserted CLAUDE.md excerpt or TODO-block string, or an
  already-resolved public-ok household token re-verified against
  report_tokens.py itself), and the assembled body text is scanned with this
  repo's own gitleaks rule chain before anything may be sent. Fails SAFE if
  gitleaks itself is not installed -- this is a security gate, not a
  nice-to-have. --dry-run writes the body under private/ and opens no
  socket. See test_egress_preflight.py.
"""
import datetime as dt
import hashlib
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request


def _repo_root():
    """Locate the repo root: the nearest ancestor directory containing BOTH an
    analysis/ and a data/ subdirectory. Walk up from the CWD first (so the
    documented private/verify copy-and-run sandbox works unchanged), then from
    this file's own location (running in place from analysis/)."""
    for start in (pathlib.Path.cwd(), pathlib.Path(__file__).resolve().parent):
        p = start
        while True:
            if (p / "analysis").is_dir() and (p / "data").is_dir():
                return p
            if p.parent == p:  # pragma: no cover - reachable only outside a checkout
                break
            p = p.parent
    raise SystemExit(  # pragma: no cover - the module's own path always resolves in-repo
        "repo root not found: no ancestor of the CWD or of this "
        "script contains both analysis/ and data/")


ROOT = _repo_root()
ENV_PATH = ROOT / ".env"
sys.path.insert(0, str(ROOT / "analysis"))
import report_tokens as rt  # noqa: E402 -- reused for the household_token re-check below


# ---------------------------------------------------------------------------
# Provider config. No dated snapshot model id is ever written here -- see the
# module docstring and call()'s fail-closed check on DEFAULT_MODEL_SENTINEL.
# ---------------------------------------------------------------------------
DEFAULT_MODEL_SENTINEL = "CONFIGURE_ME"

PROVIDERS = {
    "anthropic": {
        "base_url": "https://api.anthropic.com",
        "env_var": "ANTHROPIC_API_KEY",
        "api_version": "2023-06-01",  # the Messages API version header, not a model id
        "default_model": DEFAULT_MODEL_SENTINEL,
    },
    "openai": {
        "base_url": "https://api.openai.com",
        "env_var": "OPENAI_API_KEY",
        "default_model": DEFAULT_MODEL_SENTINEL,
    },
    "google": {
        "base_url": "https://generativelanguage.googleapis.com",
        "env_var": "GEMINI_API_KEY",
        "default_model": DEFAULT_MODEL_SENTINEL,
    },
}


class ProviderError(Exception):
    """Raised by _post_json, and re-raised with more context by the vendor
    adapters, for anything short of a clean 2xx JSON response: a non-2xx HTTP
    status, a network/timeout failure, a malformed response body, or a
    response whose shape doesn't match what the adapter expected. Carries
    .provider and .status (None when no HTTP status was ever received, e.g. a
    timeout) so a caller can distinguish "retry this" (429/5xx) from "hard
    fail this block" (everything else), per the issue's stated failure-mode
    handling. The message is passed through _redact_loaded() before storage,
    so a vendor error body that happens to echo request content can never
    leak a loaded API key into a log line or a re-raised exception."""

    def __init__(self, provider, status, message):
        self.provider = provider
        self.status = status
        self.message = _redact_loaded(message)
        suffix = f" (HTTP {status})" if status is not None else ""
        super().__init__(f"{provider}: {self.message}{suffix}")


class EgressRefused(Exception):
    """Raised by preflight() (and its helpers) when a request is not safe to
    send: an item outside the allowlist, an untracked or private-tree path, a
    household token that doesn't re-verify, or a gitleaks hit -- including
    gitleaks being unavailable at all, which fails safe rather than skipping
    the scan."""


# ---------------------------------------------------------------------------
# .env credential loading.
# ---------------------------------------------------------------------------
def load_env(path=None):
    """Parse a `.env` file into a dict: KEY=VALUE per line, '#' starts a
    whole-line comment, blank lines are skipped. No quoting or escaping --
    deliberately minimal (see .env.example), not a python-dotenv
    replacement. A missing file returns {} -- get_api_key() is what fails
    closed, naming the specific missing variable; a caller building env by
    hand (tests) never needs this function at all."""
    path = pathlib.Path(path) if path is not None else ENV_PATH
    env = {}
    if not path.is_file():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


_LOADED_SECRETS = set()


def _register_secret(value):
    if value:
        _LOADED_SECRETS.add(value)


def _redact(text, keys):
    """Replace every occurrence of any string in `keys` (the VALUES of
    currently-loaded API keys) with the fixed placeholder "[REDACTED]". Pure
    and side-effect-free, so it is directly unit-testable with fabricated
    keys independent of anything this module has actually loaded."""
    if text is None:
        return text
    redacted = str(text)
    for k in keys:
        if k:
            redacted = redacted.replace(k, "[REDACTED]")
    return redacted


def _redact_loaded(text):
    """The wrapper this module's own error paths use: redact against every
    key value actually registered via _register_secret() (i.e. every key
    get_api_key() has handed out so far in this process)."""
    return _redact(text, _LOADED_SECRETS)


def get_api_key(provider, env=None):
    """Fetch the API key for `provider`, from `env` if given, else from
    load_env() against the repo-root .env. Fails closed with SystemExit
    naming the exact missing variable and pointing at .env -- matching
    analysis/household.py's fail-closed message shape -- rather than
    returning None or an empty string. Registers the value for redaction
    before returning it."""
    if provider not in PROVIDERS:
        raise SystemExit(f"unknown provider {provider!r} -- choose one of {sorted(PROVIDERS)}")
    var = PROVIDERS[provider]["env_var"]
    env = load_env() if env is None else env
    value = (env.get(var) or "").strip()
    if not value:
        raise SystemExit(
            f"missing {var} -- add it to .env at the repo root (see .env.example; "
            f".env is gitignored and never committed) before calling the {provider} API")
    _register_secret(value)
    return value


# ---------------------------------------------------------------------------
# The one chokepoint. Nothing else in this module may import urllib.request's
# Request/urlopen -- test_llm_providers.py asserts that with an AST walk.
# ---------------------------------------------------------------------------
def _post_json(provider, url, headers, body, method="POST", timeout=60):
    """Send one HTTP request and return its parsed JSON body. `body` is a
    dict (JSON-encoded here) for a POST, or None for a GET (the list-models
    paths). Raises ProviderError -- naming the provider, the HTTP status if
    any, and a short redacted diagnostic -- for a non-2xx response, a
    network/timeout failure, or a response body that is not valid JSON.
    Never lets a raw urllib.error.HTTPError, URLError, socket.timeout or
    json.JSONDecodeError escape to the caller."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        raw = e.read()
        text = raw.decode("utf-8", errors="replace")
        raise ProviderError(provider, e.code, f"HTTP {e.code}: {text[:500]}") from None
    except socket.timeout:
        raise ProviderError(provider, None, f"timed out after {timeout}s") from None
    except urllib.error.URLError as e:
        raise ProviderError(provider, None, f"network error: {e.reason}") from None
    except Exception as e:  # pragma: no cover - safety net, see class docstring
        raise ProviderError(provider, None,
                             f"unexpected transport error: {type(e).__name__}: {e}") from None
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ProviderError(provider, None,
                             f"malformed JSON response: {e}; body starts {raw[:200]!r}") from None


def _url(base, *segments):
    """Join `base` with one or more path segments, each written as its own
    string literal so no code line ever contains a bare multi-segment path
    such as v1/messages as a single quoted constant. That form is
    indistinguishable, to test_scripts_runnable.py's
    case_no_absolute_paths_outside_the_repo guard, from a hardcoded
    filesystem path outside the repo (the thing that guard exists to catch)
    -- so URLs are assembled from single-segment pieces instead of worked
    around with an exemption."""
    return base + "".join(f"/{seg}" for seg in segments)


# ---------------------------------------------------------------------------
# Vendor adapters. Each builds its own native request shape, calls the one
# chokepoint, and normalizes the response to {"text", "finish_reason",
# "usage"}. None of these touches urllib directly.
# ---------------------------------------------------------------------------
def _call_anthropic(key, model, system_prompt, user_prompt, max_tokens):
    """Anthropic Messages API: POST /v1/messages, auth via x-api-key plus the
    anthropic-version header (not Authorization: Bearer)."""
    cfg = PROVIDERS["anthropic"]
    url = _url(cfg["base_url"], "v1", "messages")
    headers = {
        "x-api-key": key,
        "anthropic-version": cfg["api_version"],
        "content-type": "application/json",
    }
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    resp = _post_json("anthropic", url, headers, body)
    try:
        text = "".join(b.get("text", "") for b in resp["content"] if b.get("type") == "text")
        usage = resp.get("usage", {})
        return {
            "text": text,
            "finish_reason": resp["stop_reason"],
            "usage": {"input_tokens": usage.get("input_tokens"),
                      "output_tokens": usage.get("output_tokens")},
        }
    except (KeyError, TypeError, AttributeError) as e:
        raise ProviderError("anthropic", None,
                             f"unexpected response shape: {type(e).__name__}: {e}") from None


def _call_openai(key, model, system_prompt, user_prompt, max_tokens):
    """OpenAI Chat Completions API: POST /v1/chat/completions, auth via
    Authorization: Bearer."""
    cfg = PROVIDERS["openai"]
    url = _url(cfg["base_url"], "v1", "chat", "completions")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    resp = _post_json("openai", url, headers, body)
    try:
        choice = resp["choices"][0]
        usage = resp.get("usage", {})
        return {
            "text": choice["message"]["content"],
            "finish_reason": choice["finish_reason"],
            "usage": {"input_tokens": usage.get("prompt_tokens"),
                      "output_tokens": usage.get("completion_tokens")},
        }
    except (KeyError, IndexError, TypeError) as e:
        raise ProviderError("openai", None,
                             f"unexpected response shape: {type(e).__name__}: {e}") from None


def _call_google(key, model, system_prompt, user_prompt, max_tokens):
    """Google Gemini generateContent API: POST
    /v1beta/models/{model}:generateContent, auth via the x-goog-api-key
    header (kept out of the URL/query string so it never lands in a logged
    request line)."""
    cfg = PROVIDERS["google"]
    url = _url(cfg["base_url"], "v1beta", "models", f"{model}:generateContent")
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    body = {
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    resp = _post_json("google", url, headers, body)
    try:
        candidate = resp["candidates"][0]
        text = "".join(p.get("text", "") for p in candidate["content"]["parts"])
        usage = resp.get("usageMetadata", {})
        return {
            "text": text,
            "finish_reason": candidate["finishReason"],
            "usage": {"input_tokens": usage.get("promptTokenCount"),
                      "output_tokens": usage.get("candidatesTokenCount")},
        }
    except (KeyError, IndexError, TypeError) as e:
        raise ProviderError("google", None,
                             f"unexpected response shape: {type(e).__name__}: {e}") from None


_ADAPTERS = {"anthropic": _call_anthropic, "openai": _call_openai, "google": _call_google}


def call(provider, model, system_prompt, user_prompt, max_tokens, env=None):
    """Normalized per-block call: call(provider, model, system_prompt,
    user_prompt, max_tokens) -> {"text", "finish_reason", "usage"}, the same
    shape regardless of vendor, since the (not-yet-built) orchestrator treats
    all three identically. Fails closed with SystemExit if `provider` is
    unknown or `model` was never configured (None, empty, or the literal
    DEFAULT_MODEL_SENTINEL) -- this repo does not hardcode a dated snapshot
    model id anywhere; call list_models(provider) / --list-models to pick a
    current one from the vendor."""
    if provider not in PROVIDERS:
        raise SystemExit(f"unknown provider {provider!r} -- choose one of {sorted(PROVIDERS)}")
    if not model or model == DEFAULT_MODEL_SENTINEL:
        raise SystemExit(
            f"no model id configured for provider {provider!r} -- this repo deliberately does "
            "not hardcode a dated snapshot model id (it would go stale and be unverifiable); "
            f"call list_models({provider!r}) or run --list-models to pick a current id from "
            "the vendor, then pass it explicitly")
    key = get_api_key(provider, env)
    return _ADAPTERS[provider](key, model, system_prompt, user_prompt, max_tokens)


def list_models(provider, env=None):
    """Call `provider`'s own model-list endpoint and return its raw list of
    model records, so a real run can pick a current id from the vendor
    rather than a hardcoded guess. Anthropic: GET /v1/models -> data[].
    OpenAI: GET /v1/models -> data[]. Google: GET /v1beta/models ->
    models[]."""
    if provider not in PROVIDERS:
        raise SystemExit(f"unknown provider {provider!r} -- choose one of {sorted(PROVIDERS)}")
    key = get_api_key(provider, env)
    cfg = PROVIDERS[provider]
    if provider == "anthropic":
        url = _url(cfg["base_url"], "v1", "models")
        headers = {"x-api-key": key, "anthropic-version": cfg["api_version"]}
        resp = _post_json(provider, url, headers, None, method="GET")
        return resp.get("data", [])
    if provider == "openai":
        url = _url(cfg["base_url"], "v1", "models")
        headers = {"Authorization": f"Bearer {key}"}
        resp = _post_json(provider, url, headers, None, method="GET")
        return resp.get("data", [])
    # google
    url = _url(cfg["base_url"], "v1beta", "models")
    headers = {"x-goog-api-key": key}
    resp = _post_json(provider, url, headers, None, method="GET")
    return resp.get("models", [])


# ---------------------------------------------------------------------------
# Egress preflight -- the privacy gate for this new network path (CLAUDE.md
# section 4). Never opens a socket itself; it is what must run, and pass,
# before the not-yet-built orchestrator is allowed to call the adapters
# above.
# ---------------------------------------------------------------------------
ALLOWED_KINDS = {"data_file", "template_file", "claude_excerpt", "todo_text", "household_token"}


def _is_git_tracked(path):
    rel = path.relative_to(ROOT)
    r = subprocess.run(["git", "ls-files", "--error-unmatch", str(rel)],
                        cwd=ROOT, capture_output=True, text=True)
    return r.returncode == 0


def _validate_item(kind, value):
    """Validate one (kind, value) contribution to a request body against the
    egress allowlist. Raises EgressRefused naming the exact problem; never
    silently drops or skips an item."""
    if kind not in ALLOWED_KINDS:
        raise EgressRefused(
            f"item kind {kind!r} is not on the egress allowlist {sorted(ALLOWED_KINDS)}")

    if kind in ("claude_excerpt", "todo_text"):
        # Caller-asserted literal text (the issue's own design: "you can pass
        # these as literal strings the caller provides"). Still subject to
        # the gitleaks scan over the assembled body text below.
        if not isinstance(value, str):
            raise EgressRefused(f"{kind} item must be a literal string, got "
                                 f"{type(value).__name__}")
        return

    if kind == "household_token":
        # Never trust the caller's claim that a string is a public-ok
        # household value -- re-resolve it through report_tokens.py itself
        # (which enforces privacy_tiers.py at resolution time) and refuse on
        # any mismatch, so a stale or tampered value can never reach a
        # request body under this label.
        if not (isinstance(value, tuple) and len(value) == 2):
            raise EgressRefused(
                "household_token item must be a (token_name, rendered_value) tuple, "
                f"got {value!r}")
        token_name, rendered_value = value
        try:
            # resolve_token() raises SystemExit for a documented gap/failure,
            # but a NAME that isn't in TOKENS at all raises a bare KeyError
            # (TOKENS[name] happens before resolve_token's own try/except) --
            # both must fail closed here, not just the documented one.
            resolved = rt.resolve_token(token_name)
        except (SystemExit, KeyError) as e:
            raise EgressRefused(
                f"household token {token_name!r} does not resolve through "
                f"report_tokens.py -- refusing: {e}") from e
        if resolved != rendered_value:
            raise EgressRefused(
                f"household token {token_name!r} value {rendered_value!r} does not match "
                f"report_tokens.py's own resolution {resolved!r} -- refusing a possibly "
                "stale or tampered value")
        return

    # kind in ("data_file", "template_file") -- value is a real path.
    path = pathlib.Path(value).resolve()
    if "private" in path.parts:
        raise EgressRefused(f"{path} is under a private/ directory -- never eligible for egress")
    if kind == "template_file":
        if path != rt.TEMPLATE.resolve():
            raise EgressRefused(
                f"{path} is not report-template.html -- the only allowed template_file path")
    else:
        try:
            path.relative_to((ROOT / "data").resolve())
        except ValueError:
            raise EgressRefused(
                f"{path} is not under data/ -- refusing a data_file item outside the "
                "allowed directory") from None
    if not path.is_file():
        raise EgressRefused(f"{path} does not exist -- refusing an item that cannot be verified")
    if not _is_git_tracked(path):
        raise EgressRefused(f"{path} is not git-tracked -- refusing an untracked file")


def _gitleaks_scan(text):
    """Scan `text` with this repo's own gitleaks rule chain -- the same gate
    .githooks/pre-commit uses, applied to egress instead of a commit. Fails
    SAFE: if the gitleaks binary is not installed, this refuses rather than
    skipping the scan, since this is a security-critical gate, not a
    nice-to-have.

    Scanned via a real, uniquely-named `.csv` temp file and `--source`/
    `--no-git`, NOT `--pipe`: an adversarial review found that `--pipe` gives
    gitleaks no file path to test, which silently disables every PATH-SCOPED
    rule -- including .gitleaks.toml's own `green-button-export-header` rule
    (path = '.*\\.csv$'), the one rule written specifically for "a raw Green
    Button export's header carries the customer's name/address", i.e. exactly
    the kind of content this gate exists to catch. `.csv` is used because it
    is the broadest extension any currently-committed path-scoped rule
    matches. `--no-git` lets a bare temp file (outside any git working tree)
    be scanned at all. The temp file is removed in `finally`, including on a
    timeout or an unexpected exception.

    The `--config` path is ALWAYS passed explicitly now (never omitted to
    rely on auto-discovery): gitleaks's config auto-discovery does not search
    the process's cwd when `--source` points somewhere else, so the old
    "no --config, let gitleaks find .gitleaks.toml at the repo root" fallback
    (correct for `--pipe`, which runs with cwd=ROOT) would silently scan with
    NO rules at all under `--source` -- confirmed by direct reproduction, not
    assumed.
    """
    binary = shutil.which("gitleaks")
    if not binary:
        raise EgressRefused(
            "gitleaks not installed (brew install gitleaks) -- refusing to send unscanned")
    pii_rules = ROOT / "private" / "pii-rules.toml"
    config_path = str(pii_rules) if pii_rules.is_file() else str(ROOT / ".gitleaks.toml")
    fd, tmp_name = tempfile.mkstemp(suffix=".csv", prefix="egress-scan-")
    os.close(fd)
    try:
        pathlib.Path(tmp_name).write_text(text)
        cmd = [binary, "detect", "--no-git", "--source", tmp_name, "--config", config_path,
               "--redact", "--verbose", "--exit-code", "1"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT), timeout=60)
        except subprocess.TimeoutExpired:
            raise EgressRefused("gitleaks scan timed out -- refusing to send unscanned") \
                from None
        except OSError as e:
            raise EgressRefused(
                f"gitleaks scan failed to run: {e} -- refusing to send unscanned") from None
        except Exception as e:  # noqa: BLE001 -- deliberately broad: this is a
            # security-relevant scan, so ANY unexpected failure here must fail
            # SAFE (refuse to send) rather than let a bare exception crash the
            # whole run with the body already staged. The exception type and
            # message are preserved in the EgressRefused text (and _redact_
            # loaded()-scrubbed) precisely so this doesn't mask a real bug
            # during development -- it converts a crash into a refusal with
            # the same diagnostic still visible, never a silent pass-through.
            raise EgressRefused(
                f"gitleaks scan failed unexpectedly ({type(e).__name__}: "
                f"{_redact_loaded(str(e))}) -- refusing to send unscanned") from None
    finally:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
    if r.returncode == 1:
        raise EgressRefused(
            "gitleaks flagged the assembled request body -- refusing to send: "
            f"{_redact_loaded(r.stdout[-2000:])}")
    if r.returncode != 0:
        raise EgressRefused(
            f"gitleaks exited {r.returncode} (not 'clean', not 'leaks found') -- refusing to "
            f"send unscanned: {_redact_loaded((r.stderr or r.stdout)[-500:])}")


def _dry_run_dir():
    d = ROOT / "private" / "llm_dry_run"
    d.mkdir(parents=True, exist_ok=True)
    return d


def preflight(items, body_text, dry_run=False):
    """The egress gate. `items` is a list of (kind, value) tuples describing
    every source that contributed to `body_text` (see ALLOWED_KINDS);
    `body_text` is the exact text that would be sent (e.g.
    json.dumps(request_body)). Raises EgressRefused naming the first problem
    found; returns a small status dict on success. Never opens a socket
    itself -- that stays the not-yet-built orchestrator's job, done only
    after this returns cleanly.

    dry_run=True additionally writes `body_text` under
    private/llm_dry_run/ and returns before any caller could plausibly go on
    to call the adapters -- so a user can inspect exactly what would be sent
    before spending an API call."""
    for kind, value in items:
        _validate_item(kind, value)
    _gitleaks_scan(body_text)
    if dry_run:
        digest = hashlib.sha256(body_text.encode("utf-8")).hexdigest()[:16]
        stamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
        path = _dry_run_dir() / f"{stamp}-{digest}.json"
        path.write_text(body_text)
        return {"ok": True, "dry_run": True, "path": str(path)}
    return {"ok": True, "dry_run": False}


if __name__ == "__main__":  # pragma: no cover - manual smoke test only
    print(f"{len(PROVIDERS)} providers configured: {sorted(PROVIDERS)}")
    for name in PROVIDERS:
        try:
            get_api_key(name)
            print(f"  {name}: key present in .env")
        except SystemExit as e:
            print(f"  {name}: {e}")
