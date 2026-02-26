#!/usr/bin/env python3
import json
import os
import re
from typing import Any

import requests


_REFUSE_PATTERNS = [
    r"没有相关信息",
    r"资料中(?:没有|未找到)",
    r"无法确认",
    r"无法找到",
    r"暂无记录",
    r"not found in (?:the )?documents",
    r"no relevant information",
    r"insufficient information",
    r"cannot determine",
    r"未包含",
    r"未发现",
    r"未找到关于",
    r"无法获取",
    r"无明确信息",
    r"no (?:clear|explicit) information",
    r"does not contain",
]

_HALLUCINATION_SIGNALS = [
    r"\b\d{1,4}(?:[.,]\d{1,2})?\b",
    r"\b(?:aud|usd|元|澳币|美元)\b",
    r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b",
    r"\b\d{7,12}\b",
    r"@",
]

_DOMAIN_CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "bills": ("finance/bills",),
    "insurance": ("insurance",),
    "home": ("home/", "legal/property", "finance/bills/other"),
    "appliances": ("home/manuals", "home/appliances", "tech/hardware"),
    "pets": ("home/pets", "health/medical_records", "home/insurance/pet"),
}


def _clean(text: str) -> str:
    return str(text or "").strip().lower()


def _contains_any(text: str, items: list[str]) -> bool:
    body = _clean(text)
    if not body:
        return False
    return any(str(item or "").strip().lower() in body for item in items if str(item or "").strip())


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _is_refusal_answer(answer: str) -> bool:
    body = str(answer or "").strip().lower()
    if not body:
        return True
    return any(re.search(pattern, body, flags=re.I) for pattern in _REFUSE_PATTERNS)


def _looks_hallucinated_for_refusal(answer: str) -> bool:
    body = str(answer or "").strip()
    if not body:
        return False
    if _is_refusal_answer(body):
        return False
    return any(re.search(pattern, body, flags=re.I) for pattern in _HALLUCINATION_SIGNALS)


def score_by_rules(case: dict[str, Any], answer: str, related_docs: list[dict[str, Any]]) -> dict[str, Any]:
    should_refuse = bool(case.get("should_refuse"))
    body = str(answer or "").strip()
    expected_keywords = [str(x or "").strip() for x in list(case.get("keywords_expected") or []) if str(x or "").strip()]
    forbidden_keywords = [str(x or "").strip() for x in list(case.get("keywords_forbidden") or []) if str(x or "").strip()]

    if should_refuse:
        if _is_refusal_answer(body):
            return {
                "context_relevance": 1.0,
                "answer_faithfulness": 1.0,
                "answer_relevance": 1.0,
                "boundary_ok": True,
                "rule_notes": ["boundary_refusal_ok"],
            }
        if _looks_hallucinated_for_refusal(body):
            return {
                "context_relevance": 0.0,
                "answer_faithfulness": 0.0,
                "answer_relevance": 0.0,
                "boundary_ok": False,
                "rule_notes": ["boundary_hallucination"],
            }
        return {
            "context_relevance": 0.2,
            "answer_faithfulness": 0.2,
            "answer_relevance": 0.2,
            "boundary_ok": False,
            "rule_notes": ["boundary_non_refusal"],
        }

    notes: list[str] = []
    if not body:
        return {
            "context_relevance": 0.0,
            "answer_faithfulness": 0.0,
            "answer_relevance": 0.0,
            "boundary_ok": True,
            "rule_notes": ["empty_answer"],
        }

    domain = str(case.get("domain") or "").strip().lower()
    expected_paths = _DOMAIN_CATEGORY_HINTS.get(domain, tuple())
    matched_docs = 0
    for row in related_docs:
        path = _clean(str(row.get("category_path") or ""))
        if expected_paths and any(h in path for h in expected_paths):
            matched_docs += 1
    context_relevance = 0.5
    if related_docs:
        context_relevance = matched_docs / max(1, len(related_docs))
    elif expected_keywords and _contains_any(body, expected_keywords):
        context_relevance = 0.6
    context_relevance = _clip01(context_relevance)

    keyword_hit = 0
    if expected_keywords:
        keyword_hit = sum(1 for kw in expected_keywords if _contains_any(body, [kw]))
        answer_relevance = keyword_hit / max(1, len(expected_keywords))
    else:
        answer_relevance = 0.7
    answer_relevance = _clip01(answer_relevance)
    if keyword_hit > 0:
        notes.append(f"expected_keywords_hit={keyword_hit}")

    forbidden_hit = sum(1 for kw in forbidden_keywords if _contains_any(body, [kw]))
    if forbidden_hit:
        notes.append(f"forbidden_keywords_hit={forbidden_hit}")
    faithfulness = 0.75 - (0.25 * min(2, forbidden_hit))
    if not related_docs and not expected_keywords:
        faithfulness -= 0.1
    answer_faithfulness = _clip01(faithfulness)

    return {
        "context_relevance": round(context_relevance, 4),
        "answer_faithfulness": round(answer_faithfulness, 4),
        "answer_relevance": round(answer_relevance, 4),
        "boundary_ok": True,
        "rule_notes": notes,
    }


def judge_with_llm(
    *,
    case: dict[str, Any],
    answer: str,
    related_docs: list[dict[str, Any]],
    model: str,
    ollama_base_url: str,
    timeout_sec: int = 30,
) -> dict[str, Any]:
    docs_preview = [
        {
            "title": str(row.get("title") or row.get("file_name") or ""),
            "category_path": str(row.get("category_path") or ""),
        }
        for row in related_docs[:6]
    ]
    system_prompt = (
        "You are an evaluator. Score JSON only with keys: "
        "context_relevance, answer_faithfulness, answer_relevance, rationale. "
        "Each score must be 0..1. "
        "If should_refuse=true and answer does not refuse, score all 0."
    )
    user_prompt = {
        "question": str(case.get("question_zh") or ""),
        "expected_behavior": str(case.get("expected_behavior") or ""),
        "should_refuse": bool(case.get("should_refuse")),
        "answer": str(answer or ""),
        "related_docs": docs_preview,
    }
    payload = {
        "model": str(model or "qwen3:4b-instruct"),
        "format": "json",
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_prompt, ensure_ascii=False)},
        ],
        "options": {"temperature": 0.0},
    }
    url = str(ollama_base_url or "").rstrip("/") + "/api/chat"
    resp = requests.post(url, json=payload, timeout=max(5, int(timeout_sec)))
    resp.raise_for_status()
    data = resp.json() if hasattr(resp, "json") else {}
    raw = str((((data or {}).get("message") or {}).get("content") or "")).strip()
    obj = json.loads(raw) if raw else {}
    return {
        "context_relevance": _clip01(float(obj.get("context_relevance") or 0.0)),
        "answer_faithfulness": _clip01(float(obj.get("answer_faithfulness") or 0.0)),
        "answer_relevance": _clip01(float(obj.get("answer_relevance") or 0.0)),
        "rationale": str(obj.get("rationale") or "").strip(),
    }


def score_case_mixed(
    *,
    case: dict[str, Any],
    answer: str,
    related_docs: list[dict[str, Any]],
    judge_model: str | None = None,
    ollama_base_url: str | None = None,
    judge_timeout_sec: int = 30,
) -> dict[str, Any]:
    rule = score_by_rules(case, answer, related_docs)
    if bool(case.get("should_refuse")) and not bool(rule.get("boundary_ok")):
        return {
            "rule": rule,
            "judge": None,
            "judge_error": "",
            "mixed": {
                "context_relevance": 0.0,
                "answer_faithfulness": 0.0,
                "answer_relevance": 0.0,
                "overall": 0.0,
            },
        }

    model = str(judge_model or os.getenv("FAMILY_VAULT_EVAL_JUDGE_MODEL", "qwen3:4b-instruct"))
    base_url = str(ollama_base_url or os.getenv("FAMILY_VAULT_OLLAMA_BASE_URL", "http://127.0.0.1:11434"))
    judge = None
    judge_error = ""
    try:
        judge = judge_with_llm(
            case=case,
            answer=answer,
            related_docs=related_docs,
            model=model,
            ollama_base_url=base_url,
            timeout_sec=judge_timeout_sec,
        )
    except Exception as exc:  # pragma: no cover - exercised by integration tests.
        judge_error = type(exc).__name__

    if judge is None:
        mixed_ctx = float(rule["context_relevance"])
        mixed_fai = float(rule["answer_faithfulness"])
        mixed_rel = float(rule["answer_relevance"])
    else:
        mixed_ctx = (0.6 * float(judge["context_relevance"])) + (0.4 * float(rule["context_relevance"]))
        mixed_fai = (0.6 * float(judge["answer_faithfulness"])) + (0.4 * float(rule["answer_faithfulness"]))
        mixed_rel = (0.6 * float(judge["answer_relevance"])) + (0.4 * float(rule["answer_relevance"]))

    overall = (mixed_ctx + mixed_fai + mixed_rel) / 3.0
    return {
        "rule": rule,
        "judge": judge,
        "judge_error": judge_error,
        "mixed": {
            "context_relevance": round(_clip01(mixed_ctx), 4),
            "answer_faithfulness": round(_clip01(mixed_fai), 4),
            "answer_relevance": round(_clip01(mixed_rel), 4),
            "overall": round(_clip01(overall), 4),
        },
    }
