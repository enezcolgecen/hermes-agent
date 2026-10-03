"""Conservative local-only admission and one-shot answers before agent construction.

Admission is a positive grammar for static educational questions, not a negative
keyword guess. Unknown topics, session instructions and richer turns retain the
ordinary agent path. Once a wire attempt starts, failure is terminal: falling
back would create a hidden second inference attempt.
"""
from dataclasses import dataclass, field
import json
import logging
import re
import time
import uuid
import urllib.error
import urllib.request

log = logging.getLogger(__name__)
MODEL = "Qwen3.5-9B-Q4_K_M"
BASE = "http://127.0.0.1:18434/v1"
SYSTEM = (
    "Answer the user's static educational question in at most three short sentences in their language. "
    "Use only the visible conversation below. You have no tools, files, browser, "
    "memory retrieval, live data or action capability. Never claim to perform an "
    "action. If facts are uncertain, say so. Give a concise plain-text answer."
)
# Open-ended subjects such as 'my files', people, medicine, finance and current
# events cannot be admitted merely because the user phrased them as a question.
TOPICS = frozenset({
    "fotosentez", "photosynthesis", "yerçekimi", "gravity", "evrim", "evolution",
    "atom", "atoms", "molekül", "molecule", "molecules", "hücre", "cell", "cells",
    "dna", "rna", "elektron", "electron", "elektrik", "electricity",
    "ışık", "light", "ses", "sound", "ısı", "heat", "enerji", "energy",
    "sürtünme", "friction", "yoğunluk", "density", "buharlaşma", "evaporation",
    "difüzyon", "diffusion", "osmosis", "ozmoz", "mitoz", "mitosis",
    "mayoz", "meiosis", "asal sayı", "prime number", "prime numbers",
    "kesir", "fraction", "fractions", "pi sayısı", "pi", "üçgen", "triangle",
    "pisagor teoremi", "pythagorean theorem", "olasılık", "probability",
    "karbon döngüsü", "carbon cycle", "su döngüsü", "water cycle",
    "kuantum dolanıklık", "quantum entanglement", "gökkuşağı", "rainbow",
    "güneş sistemi", "solar system", "gezegen", "planet", "yıldız", "star",
})
QUESTION_PATTERNS = (
    re.compile(r"(?:what is|what are) (?P<topic>[a-z ]+)\??", re.I),
    re.compile(r"(?:explain|briefly explain) (?P<topic>[a-z ]+)\.?", re.I),
    re.compile(r"(?P<topic>[a-zçğıöşü ]+) (?:nedir|ne demek|ne anlama gelir)\??", re.I),
    re.compile(r"(?P<topic>[a-zçğıöşü ]+?)(?:['’](?:i|ı|u|ü|yi|yı|yu|yü))? (?:açıkla|kısaca açıkla)\.?", re.I),
)
FOLLOWUPS = frozenset({"bunu daha basit açıkla", "bunu kısaca açıkla", "bir örnek ver",
                       "explain that more simply", "give an example"})


class FastAnswerError(RuntimeError):
    """Safe error code, never provider bodies or exception strings."""


@dataclass(frozen=True)
class Plan:
    messages: tuple
    topic: str
    credential: str = field(repr=False)
    history_version: int = 0
    base_url: str = BASE
    model: str = MODEL
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


def classify(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 240:
        return None
    clean = text.strip().casefold()
    if any(c in clean for c in ("\n", "\r", "@", "/", "\\", "<", ">", "`", "\x00")):
        return None
    for pattern in QUESTION_PATTERNS:
        if match := pattern.fullmatch(clean):
            topic = match.group("topic").strip()
            if topic in TOPICS:
                return topic
            if "açıkla" in clean:
                for base in TOPICS:
                    if topic in {base + suffix for suffix in ("i","ı","u","ü","yi","yı","yu","yü")}:
                        return base
    return None


def prepare(session, params, text, *, config=None, endpoint=None):
    """Return a plan or None; this admission step performs no inference/startup."""
    if any(value for key, value in params.items() if key not in {"session_id", "text"}):
        log.info("local_fast_answer outcome=fallthrough reason=rich_turn")
        return None
    if any(session.get(key) for key in (
        "attached_images", "model_override", "resume_runtime_overrides", "system_prompt",
        "system_prompt_override", "persona", "_hosted_room_task", "_compute_host",
        "turn_isolation", "parent_session_id", "_turn_cancel_requested",
        "create_reasoning_override", "create_service_tier_override", "room_plumbing",
        "pending_hidden", "personality_override",
        "agent",
    )):
        log.info("local_fast_answer outcome=fallthrough reason=session_behavior")
        return None
    prior = session.get("_local_fast_answer_context")
    topic = classify(text)
    context = ()
    if isinstance(prior, dict) and prior.get("version") == session.get("history_version", 0):
        if (isinstance(text, str) and text.strip().casefold().rstrip(".?!") in FOLLOWUPS
                and prior.get("topic") in TOPICS):
            topic = prior["topic"]
        if topic == prior.get("topic"):
            context = tuple(prior.get("messages", ()))
    if topic is None:
        log.info("local_fast_answer outcome=fallthrough reason=unproven_text")
        return None
    if config is None:
        from hermes_cli.config import load_config
        config = load_config()
    cfg = config.get("model") or {}
    if (cfg.get("provider") != "llamacpp" or cfg.get("default") != MODEL
            or cfg.get("base_url") != BASE):
        log.info("local_fast_answer outcome=fallthrough reason=route_config")
        return None
    if any(config.get(key) for key in ("system_prompt", "system_prompt_file")):
        log.info("local_fast_answer outcome=fallthrough reason=custom_instruction")
        return None
    if endpoint is None:
        from hermes_cli.local_runtime.endpoint import managed_root
        root = managed_root()
        endpoint = {"base_url": root[0] + "/v1", "api_key": root[1]} if root else {}
    if endpoint.get("base_url") != BASE or not endpoint.get("api_key"):
        log.info("local_fast_answer outcome=fallthrough reason=managed_route")
        return None
    messages = ({"role": "system", "content": SYSTEM}, *context,
                {"role": "user", "content": text.strip()})
    if (len(context) > 4 or any(not isinstance(m,dict) or m.get("role") not in {"user","assistant"}
                              or not isinstance(m.get("content"),str) for m in context)
            or sum(len(m["content"]) for m in messages) > 2400):
        log.info("local_fast_answer outcome=fallthrough reason=context_bound")
        return None
    plan = Plan(messages=messages, topic=topic, credential=endpoint["api_key"],
                history_version=session.get("history_version",0))
    log.info("local_fast_answer outcome=admit trace=%s messages=%d prompt_chars=%d",
             plan.trace_id, len(messages), sum(len(m["content"]) for m in messages))
    return plan


def answer(plan, *, opener=None, cancelled=lambda: False):
    """Exactly one local HTTP attempt, without proxy, redirect, tools or retry."""
    if plan.base_url != BASE or plan.model != MODEL or cancelled():
        raise FastAnswerError("preflight_rejected")
    from hermes_cli.local_runtime.endpoint import managed_root
    root = managed_root() if opener is None else (BASE.removesuffix('/v1'), plan.credential)
    if not root or root != (BASE.removesuffix('/v1'), plan.credential):
        raise FastAnswerError("runtime_drift")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    body = json.dumps({"model": MODEL, "messages": list(plan.messages), "stream": False,
                       "max_tokens": 512, "chat_template_kwargs": {"enable_thinking": False}}).encode()
    req = urllib.request.Request(BASE + "/chat/completions", data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Authorization": "Bearer " + plan.credential})
    start = time.monotonic()
    log.info("local_fast_answer outcome=attempt trace=%s prompt_bytes=%d attempt=1", plan.trace_id, len(body))
    try:
        with opener.open(req, timeout=60) as response:
            if response.status != 200:
                raise FastAnswerError("http_status")
            raw = response.read(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise FastAnswerError("response_bound")
            result = json.loads(raw)
        choice = result["choices"][0]; message = choice["message"]
        content = message.get("content")
        if (result.get("model") != MODEL or message.get("role") != "assistant"
                or choice.get("finish_reason") != "stop" or message.get("tool_calls")
                or message.get("function_call") or not isinstance(content,str)
                or not content.strip() or len(content) > 8000 or cancelled()):
            raise FastAnswerError("incomplete_or_nontext")
        usage = result.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens")
        if type(prompt_tokens) is not int or not 0 < prompt_tokens <= 2048:
            raise FastAnswerError("prompt_usage_bound")
    except FastAnswerError:
        log.info("local_fast_answer outcome=failed trace=%s elapsed_ms=%.3f", plan.trace_id, (time.monotonic()-start)*1000)
        raise
    except Exception:
        log.info("local_fast_answer outcome=failed trace=%s reason=transport_or_decode elapsed_ms=%.3f",
                 plan.trace_id, (time.monotonic()-start)*1000)
        raise FastAnswerError("transport_or_decode") from None
    metrics = {"prompt_tokens":prompt_tokens,"elapsed_ms":round((time.monotonic()-start)*1000,3)}
    details = usage.get("prompt_tokens_details") or {}
    if type(details.get("cached_tokens")) is int:
        metrics["cached_tokens"] = details["cached_tokens"]
    timings = result.get("timings") or {}
    for key in ("prompt_n", "prompt_ms", "predicted_n", "predicted_ms", "cache_n"):
        if type(timings.get(key)) in (int,float):
            metrics[key] = timings[key]
    log.info("local_fast_answer outcome=hit trace=%s prompt_tokens=%d elapsed_ms=%.3f",
             plan.trace_id, prompt_tokens, metrics["elapsed_ms"])
    return content.strip(), metrics


def remember(session, plan, content):
    """Only short context generated by this path can authorize a follow-up."""
    with session["history_lock"]:
        if (len(content) > 900 or session.get("running")
                or session.get("history_version",0) != plan.history_version+1):
            session.pop("_local_fast_answer_context",None)
            return
        messages = tuple(m for m in plan.messages if m["role"] != "system") + (
            {"role":"assistant", "content":content},)
        session["_local_fast_answer_context"] = {
            "topic":plan.topic,"messages":messages[-4:],"version":session.get("history_version",0)}
