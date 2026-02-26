from types import SimpleNamespace

import pytest

from app.services.agent_graph_nodes import (
    _router_assist_apply_result,
    _router_assist_candidate_chat_urls,
    _router_assist_should_trigger,
)

pytestmark = pytest.mark.no_db_reset


def test_router_assist_triggers_on_low_confidence_provider_contact_pattern():
    spec = {
        "subject_domain": "generic",
        "target_slots": [],
        "preferred_categories": ["finance/bills"],
    }
    conf = {"score": 0.42}
    yes, reason = _router_assist_should_trigger(
        query="家里网络提供商的联系方式是什么？",
        spec=spec,
        confidence=conf,
        mode="low_confidence",
    )
    assert yes is True
    assert reason in {
        "empty_target_slots",
        "generic_domain_with_domain_cues",
        "broad_or_empty_categories",
        "provider_contact_high_value_pattern",
        "low_rule_confidence",
    }


def test_router_assist_does_not_trigger_for_high_confidence_specific_spec():
    spec = {
        "subject_domain": "bills",
        "target_slots": ["bill_amount", "vendor", "billing_period"],
        "preferred_categories": ["finance/bills/internet", "finance/bills"],
    }
    conf = {"score": 0.88}
    yes, reason = _router_assist_should_trigger(
        query="最近的网费账单是多少？",
        spec=spec,
        confidence=conf,
        mode="low_confidence",
    )
    assert yes is False
    assert reason == "high_rule_confidence"


def test_router_assist_apply_result_filters_invalid_categories_and_keeps_rule_union():
    spec = {
        "subject_domain": "generic",
        "preferred_categories": ["finance/bills"],
    }
    confidence = {"score": 0.4}
    raw = {
        "selected_categories": ["finance/bills/internet", "invalid/path"],
        "confidence": 0.91,
        "keep_rule_categories": True,
        "reason_tags": ["billing_subtype"],
    }
    out_spec, diag = _router_assist_apply_result(
        spec=spec,
        raw_result=raw,
        candidate_categories=["finance/bills/internet", "finance/bills/electricity", "finance/bills"],
        confidence=confidence,
    )
    assert out_spec["preferred_categories"][0] == "finance/bills/internet"
    assert "finance/bills" in out_spec["preferred_categories"]
    assert out_spec["subject_domain"] == "bills"
    assert diag["fallback_used"] is False


def test_router_assist_apply_result_recovers_selected_category_when_llm_omits_it():
    spec = {
        "subject_domain": "bills",
        "preferred_categories": ["finance/bills"],
        "subject_aliases": ["internet bill", "superloop"],
        "target_slots": ["vendor", "contact_phone", "contact_email"],
    }
    confidence = {"score": 0.55}
    raw = {
        "selected_categories": [],
        "confidence": 0.93,
        "keep_rule_categories": True,
        "reason_tags": ["specific_sub_category_match", "billing_subtype"],
    }
    out_spec, diag = _router_assist_apply_result(
        spec=spec,
        raw_result=raw,
        candidate_categories=["finance/bills/internet", "finance/bills", "home/manuals"],
        confidence=confidence,
    )
    assert out_spec["preferred_categories"][0] == "finance/bills/internet"
    assert "finance/bills" in out_spec["preferred_categories"]
    assert diag["fallback_used"] is False


def test_router_assist_candidate_urls_adds_gateway_fallback_when_host_docker_internal_unresolved(monkeypatch):
    settings = SimpleNamespace(ollama_base_url="http://host.docker.internal:11434")
    monkeypatch.setattr("app.services.agent_graph_nodes.socket.gethostbyname", lambda _h: (_ for _ in ()).throw(OSError("unresolved")))
    monkeypatch.setattr("app.services.agent_graph_nodes._linux_default_gateway_ip", lambda: "172.31.0.1")
    urls = _router_assist_candidate_chat_urls(settings)
    assert urls[0] == "http://host.docker.internal:11434/api/chat"
    assert "http://172.31.0.1:11434/api/chat" in urls
