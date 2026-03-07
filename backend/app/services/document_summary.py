import re
from collections import Counter

from sqlalchemy.orm import Session

from app.services.llm_summary import summarize_document_with_model

_SENTENCE_SPLIT = re.compile(r"(?:\r?\n)+|(?<=[\.\!\?。！？；;])\s+")
_DATE_PATTERNS = [
    re.compile(r"\b20\d{2}[/-](?:0?[1-9]|1[0-2])(?:[/-](?:0?[1-9]|[12]\d|3[01]))?\b"),
    re.compile(r"\b(?:0?[1-9]|1[0-2])[/-]20\d{2}\b"),
    re.compile(r"20\d{2}\s*年\s*(?:0?[1-9]|1[0-2])\s*月"),
]
_AMOUNT_PATTERN = re.compile(
    r"(?:[$€£]|aud|usd|cny|rmb)?\s*\d{1,3}(?:[,\s]\d{3})*(?:\.\d{1,2})?",
    flags=re.IGNORECASE,
)
_NOISE_PATTERN = re.compile(r"^[\W_]+$")
_HAS_ZH = re.compile(r"[\u4e00-\u9fff]")

_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
    "from",
    "are",
    "was",
    "were",
    "have",
    "has",
    "had",
    "will",
    "shall",
    "your",
    "you",
    "our",
    "their",
    "to",
    "of",
    "in",
    "on",
    "at",
    "is",
    "as",
    "by",
    "or",
    "an",
    "a",
    "已",
    "并",
    "在",
    "和",
    "或",
    "于",
    "的",
    "了",
    "及",
}

_THEME_TERMS = {
    "bill": "账单",
    "invoice": "发票",
    "statement": "对账单",
    "payment": "付款",
    "amount": "金额",
    "due": "到期",
    "date": "日期",
    "water": "水费",
    "electricity": "电费",
    "gas": "燃气",
    "internet": "网络",
    "warranty": "保修",
    "maintenance": "维护",
    "meeting": "会议",
    "notice": "通知",
    "strata": "业主委员会",
    "property": "房产",
}

_SIGNAL_TERMS = {
    "invoice",
    "bill",
    "payment",
    "amount",
    "due",
    "date",
    "warranty",
    "meeting",
    "notice",
    "electricity",
    "water",
    "gas",
    "账单",
    "发票",
    "付款",
    "金额",
    "到期",
    "日期",
    "保修",
    "会议",
    "通知",
    "电费",
    "水费",
    "燃气",
}

_AMOUNT_HINTS = {
    "$",
    "aud",
    "usd",
    "cny",
    "rmb",
    "amount",
    "due",
    "total",
    "invoice",
    "bill",
    "fee",
    "payment",
    "金额",
    "应付",
    "合计",
    "总计",
    "账单",
    "发票",
    "费用",
}


def _compact_text(text: str, limit: int) -> str:
    merged = " ".join(str(text or "").split())
    if len(merged) <= limit:
        return merged
    return merged[:limit].rstrip() + "..."


def _normalize_token(token: str) -> str:
    out = "".join(ch for ch in str(token or "") if ch.isalnum() or ("\u4e00" <= ch <= "\u9fff"))
    return out.strip().lower()


def _contains_meaningful_letters(token: str) -> bool:
    s = str(token or "")
    has_zh = any("\u4e00" <= ch <= "\u9fff" for ch in s)
    has_alpha = any(ch.isalpha() for ch in s)
    return bool(has_zh or has_alpha)


def _extract_keywords(text: str, *, top_n: int = 8) -> list[str]:
    words: list[str] = []
    for token in str(text or "").replace("|", " ").split():
        clean = _normalize_token(token)
        if len(clean) < 2 or clean in _STOPWORDS:
            continue
        if not _contains_meaningful_letters(clean):
            continue
        words.append(clean)
    if not words:
        return []
    freq = Counter(words)
    return [w for w, _ in freq.most_common(max(1, int(top_n)))]


def _is_noise_sentence(sentence: str) -> bool:
    s = str(sentence or "").strip()
    if len(s) < 8:
        return True
    if _NOISE_PATTERN.match(s):
        return True
    alpha = sum(ch.isalpha() for ch in s)
    digits = sum(ch.isdigit() for ch in s)
    if (alpha + digits) <= 3:
        return True
    return False


def _split_sentences(text: str) -> list[str]:
    out = []
    for part in _SENTENCE_SPLIT.split(str(text or "")):
        s = str(part or "").strip()
        if (not s) or _is_noise_sentence(s):
            continue
        out.append(s)
    return out


def _has_date(text: str) -> bool:
    raw = str(text or "")
    return any(p.search(raw) for p in _DATE_PATTERNS)


def _sentence_score(sentence: str, keywords: list[str]) -> int:
    lowered = str(sentence or "").lower()
    score = 0
    for kw in keywords[:8]:
        if kw and kw in lowered:
            score += 2
    if _has_date(lowered):
        score += 3
    if _AMOUNT_PATTERN.search(lowered):
        score += 3
    if any(term in lowered for term in _SIGNAL_TERMS):
        score += 2
    score += min(4, max(0, len(lowered) // 70))
    return score


def _pick_key_sentences(text: str, *, keywords: list[str], max_sentences: int = 3) -> list[str]:
    rows = _split_sentences(text)
    if not rows:
        short = _compact_text(text, 240)
        return [short] if short else []
    scored: list[tuple[int, int, str]] = []
    for idx, sentence in enumerate(rows):
        scored.append((_sentence_score(sentence, keywords), idx, sentence))
    scored.sort(key=lambda item: (-item[0], item[1]))
    top = sorted(scored[: max(1, int(max_sentences))], key=lambda item: item[1])
    return [item[2] for item in top]


def _extract_dates(text: str, *, cap: int = 3) -> list[str]:
    seen: list[str] = []
    raw = str(text or "")
    for pattern in _DATE_PATTERNS:
        for m in pattern.findall(raw):
            value = str(m if isinstance(m, str) else "".join(m)).strip()
            if (not value) or (value in seen):
                continue
            seen.append(value)
            if len(seen) >= max(1, int(cap)):
                return seen
    return seen


def _extract_amounts(text: str, *, cap: int = 3) -> list[str]:
    out: list[str] = []
    raw = str(text or "")
    for match in _AMOUNT_PATTERN.finditer(raw):
        value = " ".join(str(match.group(0) or "").split()).strip()
        if len(value) < 3:
            continue
        if (not any(ch.isdigit() for ch in value)) or (value in out):
            continue
        left = max(0, match.start() - 20)
        right = min(len(raw), match.end() + 20)
        window = raw[left:right].lower()
        if (not any(h in window for h in _AMOUNT_HINTS)) and ("." not in value):
            continue
        out.append(value)
        if len(out) >= max(1, int(cap)):
            break
    return out


def _zh_themes(keywords: list[str], *, cap: int = 4) -> list[str]:
    out: list[str] = []
    for kw in keywords:
        raw = str(kw or "").lower()
        mapped = _THEME_TERMS.get(raw, "")
        if (not mapped) and _HAS_ZH.search(raw):
            mapped = raw
        if (not mapped) or (mapped in out):
            continue
        out.append(mapped)
        if len(out) >= max(1, int(cap)):
            break
    return out


def build_document_summaries(
    *,
    text: str,
    doc_lang: str,
    category_label_en: str,
    category_label_zh: str,
    title_en: str,
    title_zh: str,
    db: Session | None = None,
) -> tuple[str, str]:
    raw = str(text or "").strip()
    if not raw:
        return (
            f"No reliable body text extracted. Focus area: {category_label_en or 'General'}.",
            f"未提取到稳定正文内容，建议检查原始文件质量。主题：{category_label_zh or '通用'}。",
        )

    model_summary = summarize_document_with_model(
        text=raw,
        title_en=title_en,
        title_zh=title_zh,
        category_label_en=category_label_en,
        category_label_zh=category_label_zh,
        db=db,
    )
    if model_summary is not None:
        en, zh = model_summary
        if en or zh:
            return (str(en or "").strip(), str(zh or "").strip())

    scoped = raw[:18000]
    keywords = _extract_keywords(scoped, top_n=10)
    key_sentences = _pick_key_sentences(scoped, keywords=keywords, max_sentences=3)
    dates = _extract_dates(scoped, cap=3)
    amounts = _extract_amounts(scoped, cap=3)

    sentence_en = _compact_text(" ".join(key_sentences), 360)
    if not sentence_en:
        sentence_en = _compact_text(scoped, 220)

    themes_zh = _zh_themes(keywords, cap=4)
    theme_text_zh = "、".join(themes_zh) if themes_zh else (category_label_zh or "通用主题")
    date_text = "、".join(dates) if dates else "未明确给出"
    amount_text = "、".join(amounts) if amounts else "未明确给出"

    summary_en = (
        f"Content focus: {category_label_en or 'General'}. "
        f"Key details include date references ({', '.join(dates) if dates else 'n/a'}) "
        f"and amounts ({', '.join(amounts) if amounts else 'n/a'}). "
        f"Core points: {sentence_en}"
    )
    summary_en = _compact_text(summary_en, 650)

    if _HAS_ZH.search(sentence_en) or str(doc_lang or "").lower() == "zh":
        core_zh = _compact_text("；".join(key_sentences), 360)
    else:
        core_zh = f"文档围绕{theme_text_zh}展开，核心信息与付款/时间节点相关。"

    summary_zh = (
        f"核心分析：该文档主要涉及{theme_text_zh}。"
        f"重点提炼：时间信息 {date_text}；金额信息 {amount_text}。"
        f"内容要点：{core_zh or (title_zh or title_en or '无可用文本要点')}。"
    )
    summary_zh = _compact_text(summary_zh, 650)
    return (summary_en, summary_zh)
