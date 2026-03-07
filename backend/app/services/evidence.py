import re
from typing import Any

from app.schemas import PlannerDecision
from app.services.agent_constants import (
    _ANSWERABILITY_AMOUNT_TOKENS,
    _ANSWERABILITY_CONTACT_TOKENS,
    _ANSWERABILITY_DATE_TOKENS,
    _ANSWERABILITY_PRESENCE_TOKENS,
)


def _safe_text(value: Any, *, cap: int = 280) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= cap:
        return text
    return text[:cap].rstrip() + "..."


def _required_evidence_fields(query: str, planner: PlannerDecision) -> list[str]:
    lowered = str(query or "").lower()
    out: list[str] = []
    explicit = [
        str(x or "").strip()
        for x in list(getattr(planner, "required_evidence_fields", []) or [])
        if str(x or "").strip()
    ]
    allowed = {"amount", "date", "contact", "explicit_presence_evidence"}
    explicit = [x for x in explicit if x in allowed]
    amount_needed = any(tok in lowered for tok in _ANSWERABILITY_AMOUNT_TOKENS)
    date_needed = any(tok in lowered for tok in _ANSWERABILITY_DATE_TOKENS)
    contact_needed = any(tok in lowered for tok in _ANSWERABILITY_CONTACT_TOKENS)
    presence_needed = any(tok in lowered for tok in _ANSWERABILITY_PRESENCE_TOKENS)
    coverage_needed = any(
        tok in lowered
        for tok in (
            "coverage",
            "covered",
            "what's covered",
            "保障",
            "覆盖",
            "exclusion",
            "除外",
        )
    )
    # Use query-driven requirements first, and only lightly trust planner-provided
    # required fields to avoid over-refusal from broad defaults.
    if amount_needed or (
        "amount" in explicit
        and any(
            tok in lowered for tok in ("账单", "bill", "费用", "保费", "price", "cost")
        )
    ):
        out.append("amount")
    if date_needed or (
        "date" in explicit
        and any(
            tok in lowered
            for tok in ("到期", "日期", "when", "date", "expiry", "period")
        )
    ):
        out.append("date")
    if contact_needed or (
        "contact" in explicit
        and any(
            tok in lowered
            for tok in ("联系方式", "电话", "邮箱", "contact", "phone", "email")
        )
    ):
        out.append("contact")
    if coverage_needed and "explicit_presence_evidence" not in out:
        out.append("explicit_presence_evidence")
    if presence_needed:
        out.append("explicit_presence_evidence")
    return out[:8]


def _evidence_match(field: str, text: str) -> bool:
    lowered = str(text or "").lower()
    if not lowered:
        return False
    if field == "amount":
        return bool(re.search(r"(?:aud|澳币|\$)\s?\d+(?:\.\d{1,2})?", lowered)) or bool(
            re.search(r"\d+(?:\.\d{1,2})\s*(?:元|澳币|美元)", lowered)
        )
    if field == "date":
        _mo_lo = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
        return bool(
            re.search(
                r"20\d{2}[-/年\.]\d{1,2}[-/月\.]\d{1,2}日?", lowered
            )  # 2024-11-04
            or re.search(
                r"\b\d{1,2}\s+" + _mo_lo + r"\s+20\d{2}\b", lowered
            )  # 2 December 2025
            or re.search(
                _mo_lo + r"\s+\d{1,2},?\s+20\d{2}\b", lowered
            )  # December 2, 2025
            or re.search(
                r"\b\d{1,2}[-/]\d{1,2}[-/]20\d{2}\b", lowered
            )  # 04-11-2024 or 04/11/2024
        )
    if field == "contact":
        return ("@" in lowered) or bool(re.search(r"\b\d{8,12}\b", lowered))
    if field == "explicit_presence_evidence":
        return any(
            tok in lowered
            for tok in (
                "has",
                "have",
                "contains",
                "存在",
                "有",
                "无",
                "没有",
                "未见",
                "未找到",
            )
        )
    return False


def _build_evidence_map(
    fields: list[str], chunks: list[dict[str, Any]]
) -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = {}
    for field in fields:  # noqa: F402
        refs: list[dict[str, str]] = []
        for chunk in chunks[:12]:
            text = str(chunk.get("text") or "")
            if not _evidence_match(field, text):
                continue
            refs.append(
                {
                    "doc_id": str(chunk.get("doc_id") or ""),
                    "chunk_id": str(chunk.get("chunk_id") or ""),
                    "evidence_text": _safe_text(text, cap=120),
                }
            )
            if len(refs) >= 2:
                break
        out[field] = refs
    return out


def _coverage_from_map(
    fields: list[str], evidence_map: dict[str, list[dict[str, str]]]
) -> tuple[float, list[str]]:
    if not fields:
        return (1.0, [])
    hit = 0
    missing: list[str] = []
    for field in fields:  # noqa: F402
        rows = evidence_map.get(field) or []
        if rows:
            hit += 1
        else:
            missing.append(field)
    return (round(hit / max(1, len(fields)), 4), missing)


def _infer_answerability(
    *,
    hit_count: int,
    coverage_ratio: float,
    refusal_candidate: bool,
    has_requirements: bool,
) -> str:
    if hit_count <= 0 and (refusal_candidate or has_requirements):
        return "none"
    if hit_count <= 0:
        return "insufficient"
    if refusal_candidate and coverage_ratio < 1.0:
        return "none"
    if has_requirements and coverage_ratio < 0.4:
        return "insufficient"
    return "sufficient"


def _presence_evidence_sufficient(query: str, chunks: list[dict[str, Any]]) -> bool:
    lowered = str(query or "").lower()
    tokens = re.findall(r"[a-z]{3,}|[\u4e00-\u9fff]{2,}", lowered)
    stop = {
        "我们",
        "家里",
        "有没有",
        "是否",
        "有无",
        "what",
        "which",
        "have",
        "did",
        "can",
    }
    targets = [tok for tok in tokens if tok not in stop][:5]
    if not targets:
        return False
    patterns = (r"(有|没有|未|无|已|申请|购买|做过|完成|not found|has|have|did)",)
    for chunk in chunks[:12]:
        text = str(chunk.get("text") or "").lower()
        if not text:
            continue
        for tok in targets:
            if tok not in text:
                continue
            if any(re.search(pat, text) for pat in patterns):
                return True
    return False


def _subject_coverage_ok(anchor_terms: list[str], chunks: list[dict[str, Any]]) -> bool:
    if not anchor_terms:
        return True
    texts = []
    for chunk in chunks[:12]:
        texts.append(str(chunk.get("text") or "").lower())
        texts.append(str(chunk.get("title_zh") or "").lower())
        texts.append(str(chunk.get("title_en") or "").lower())
        texts.append(str(chunk.get("category_path") or "").lower())
    blob = "\n".join(texts)
    if not blob.strip():
        return False
    return any(term in blob for term in anchor_terms if term)


def _target_field_coverage_ok(
    target_fields: list[str], chunks: list[dict[str, Any]]
) -> bool:
    if not target_fields:
        return True
    texts = []
    for chunk in chunks[:12]:
        texts.append(str(chunk.get("text") or "").lower())
        texts.append(str(chunk.get("title_zh") or "").lower())
        texts.append(str(chunk.get("title_en") or "").lower())
    blob = "\n".join(texts)
    if not blob.strip():
        return False
    for field in target_fields:  # noqa: F402
        if field == "birth_date":
            if any(
                tok in blob for tok in ("birthday", "birth date", "dob", "生日", "出生")
            ):
                return True
        elif field == "coverage_scope":
            if any(
                tok in blob
                for tok in ("coverage", "covered", "exclusion", "保障", "覆盖", "除外")
            ):
                return True
        elif field == "maintenance_howto":
            if any(
                tok in blob
                for tok in (
                    "maintain",
                    "maintenance",
                    "service",
                    "filter",
                    "clean",
                    "维护",
                    "保养",
                    "清洁",
                )
            ):
                return True
        elif field == "contact":
            if ("@" in blob) or any(
                tok in blob for tok in ("contact", "phone", "email", "电话", "邮箱")
            ):
                return True
    return False


def _contains_specific_claim(text: str) -> bool:
    body = str(text or "")
    if not body.strip():
        return False
    if re.search(r"https?://|www\.", body, flags=re.I):
        return True
    if re.search(r"\b\d{8,12}\b", body):
        return True
    if re.search(r"(?:aud|澳币|\$)\s?\d+(?:\.\d{1,2})?", body, flags=re.I):
        return True
    if re.search(r"(20\d{2}[-/年\.]\d{1,2}[-/月\.]\d{1,2}日?)", body):
        return True
    return False
