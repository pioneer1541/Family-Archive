from typing import Any

from app.schemas import PlannerDecision, ResultCardAction

_ACTION_LABELS = {
    "open_docs": ("Open Docs", "打开文档"),
    "compare": ("Compare", "比较"),
    "timeline": ("Timeline", "时间线"),
    "retrieve_docs": ("Open Docs", "打开文档"),
    "compare_docs": ("Compare", "比较"),
    "timeline_extract": ("Timeline", "时间线"),
    "summarize_docs": ("Summarize", "生成摘要"),
    "extract_fields": ("Extract Fields", "提取字段"),
    "list_by_category": ("By Category", "按分类查看"),
    "queue_ops": ("Queue", "队列操作"),
    "queue_view": ("Queue", "队列状态"),
    "reprocess_doc": ("Reprocess", "重处理文档"),
    "tag_update": ("Update Tags", "更新标签"),
    "search_documents": ("Search", "检索"),
    "list_recent": ("Recent Docs", "最近文档"),
    "fallback_search": ("Fallback Search", "回退语义检索"),
    "extract_details": ("Extract Details", "提取细节"),
}

_ACTION_META: dict[str, dict[str, Any]] = {
    "open_docs": {"action_type": "navigate", "payload": {"target": "docs"}},
    "retrieve_docs": {"action_type": "navigate", "payload": {"target": "docs"}},
    "list_by_category": {"action_type": "navigate", "payload": {"target": "cats"}},
    "search_documents": {
        "action_type": "agent_command",
        "payload": {"command": "search"},
    },
    "queue_ops": {"action_type": "agent_command", "payload": {"command": "queue_view"}},
    "queue_view": {
        "action_type": "agent_command",
        "payload": {"command": "queue_view"},
    },
    "list_recent": {
        "action_type": "agent_command",
        "payload": {"command": "list_recent"},
    },
    "compare_docs": {
        "action_type": "agent_command",
        "payload": {"command": "compare_docs"},
    },
    "timeline_extract": {
        "action_type": "agent_command",
        "payload": {"command": "timeline_build"},
    },
    "extract_fields": {
        "action_type": "agent_command",
        "payload": {"command": "extract_fields"},
    },
    "extract_details": {
        "action_type": "agent_command",
        "payload": {"command": "extract_details"},
    },
    "fallback_search": {
        "action_type": "agent_command",
        "payload": {"command": "fallback_search"},
    },
    "reprocess_doc": {
        "action_type": "mutate",
        "payload": {"command": "reprocess_doc"},
        "requires_confirm": True,
        "confirm_text_en": "Reprocess selected document?",
        "confirm_text_zh": "确认重处理所选文档？",
    },
    "tag_update": {
        "action_type": "mutate",
        "payload": {"command": "tag_update"},
        "requires_confirm": True,
        "confirm_text_en": "Update tags for selected document?",
        "confirm_text_zh": "确认更新所选文档标签？",
    },
}


def _build_action(key: str, *, default_label_en: str, default_label_zh: str) -> ResultCardAction:
    meta = _ACTION_META.get(key, {})
    return ResultCardAction(
        key=key,
        label_en=default_label_en,
        label_zh=default_label_zh,
        action_type=str(meta.get("action_type") or "suggestion"),
        payload=meta.get("payload") if isinstance(meta.get("payload"), dict) else {},
        requires_confirm=bool(meta.get("requires_confirm", False)),
        confirm_text_en=str(meta.get("confirm_text_en") or ""),
        confirm_text_zh=str(meta.get("confirm_text_zh") or ""),
    )


def _default_actions(planner: PlannerDecision) -> list[ResultCardAction]:
    chosen: list[str] = []
    for action in list(planner.actions or []):
        key = str(action or "").strip()
        if key and key not in chosen:
            chosen.append(key)
    if planner.confidence < 0.55 and "fallback_search" not in chosen:
        chosen.insert(0, "fallback_search")
    if not chosen:
        chosen = ["open_docs", "search_documents"]

    out: list[ResultCardAction] = []
    for key in chosen[:4]:
        label_en, label_zh = _ACTION_LABELS.get(key, (key.replace("_", " ").title(), key))
        out.append(_build_action(key, default_label_en=label_en, default_label_zh=label_zh))
    return out
