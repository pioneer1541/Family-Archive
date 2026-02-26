import pytest

from app.services.agent_slots import derive_facts, extract_slots_from_chunks
from app.services.evidence_patterns import find_dates, find_phones

pytestmark = pytest.mark.no_db_reset


def test_evidence_patterns_support_multiple_date_and_phone_formats():
    text = "Contact 1800 123 456 before 12/03/2026 or Mar 20, 2026. Backup +61 412 345 678."
    dates = find_dates(text)
    phones = find_phones(text)
    assert "2026-03-12" in dates
    assert "2026-03-20" in dates
    assert any("1800" in p for p in phones)
    assert any("+61" in p or "0412" in p for p in phones)


def test_extract_slots_finds_emergency_phone_and_policy_number():
    spec = {
        "target_slots": ["emergency_contact_phone", "policy_no"],
        "needs_presence_evidence": False,
        "needs_status_evidence": False,
    }
    chunks = [
        {
            "doc_id": "doc-1",
            "chunk_id": "c-1",
            "text": "Motor Insurance Policy Number: MPA-167699547. For emergency roadside assistance call 1800 111 222.",
        }
    ]
    rows = extract_slots_from_chunks(query_spec=spec, context_chunks=chunks)
    by_slot = {row["slot"]: row for row in rows}
    assert by_slot["policy_no"]["status"] == "found"
    assert "MPA" in by_slot["policy_no"]["value"]
    assert by_slot["emergency_contact_phone"]["status"] == "found"
    assert "1800" in by_slot["emergency_contact_phone"]["value"]


def test_derive_facts_computes_next_vaccine_due_when_interval_present():
    slot_results = [
        {"slot": "vaccine_date_last", "value": "2025-10-31", "status": "found"},
        {"slot": "vaccine_interval", "value": "every 12 month", "status": "found"},
    ]
    derived = derive_facts(slot_results=slot_results, derivations=["estimate_next_vaccine_due"])
    assert derived
    assert derived[0]["status"] in {"derived", "partial"}
    if derived[0]["status"] == "derived":
        assert derived[0]["value"].startswith("2026-10")


def test_extract_slots_prefers_internet_bill_amount_over_other_bills():
    spec = {
        "subject_domain": "bills",
        "target_slots": ["bill_amount", "vendor", "billing_period"],
        "preferred_categories": ["finance/bills/internet", "finance/bills"],
        "subject_aliases": ["internet bill", "nbn", "superloop"],
    }
    chunks = [
        {
            "doc_id": "doc-water",
            "chunk_id": "c-water",
            "category_path": "finance/bills/water",
            "title_en": "Water bill",
            "text": "Yarra Valley Water invoice. Amount due AUD 267.43. Contact enquiry@yvw.com.au",
        },
        {
            "doc_id": "doc-net",
            "chunk_id": "c-net",
            "category_path": "finance/bills/internet",
            "title_en": "Internet bill",
            "text": "Superloop internet bill for Feb 2026. Total amount AUD 109.00. Billing period 2026-02-01 to 2026-02-28.",
        },
    ]
    rows = extract_slots_from_chunks(query_spec=spec, context_chunks=chunks)
    by_slot = {row["slot"]: row for row in rows}
    assert by_slot["bill_amount"]["status"] == "found"
    assert "109.00" in by_slot["bill_amount"]["value"]
    assert by_slot["vendor"]["status"] == "found"
    assert "superloop" in by_slot["vendor"]["value"].lower()
    assert by_slot["billing_period"]["status"] == "found"
    assert "2026-02" in by_slot["billing_period"]["value"]


def test_extract_slots_prefers_network_provider_contact_over_unrelated_utility_contact():
    spec = {
        "subject_domain": "bills",
        "target_slots": ["vendor", "contact_phone", "contact_email", "provider"],
        "preferred_categories": ["finance/bills/internet", "finance/bills"],
        "subject_aliases": ["internet bill", "superloop", "nbn"],
    }
    chunks = [
        {
            "doc_id": "doc-water",
            "chunk_id": "c-water",
            "category_path": "finance/bills/water",
            "title_en": "Water bill",
            "text": "Yarra Valley Water. Call 1300 304 688 or email enquiry@yvw.com.au",
        },
        {
            "doc_id": "doc-net",
            "chunk_id": "c-net",
            "category_path": "finance/bills/internet",
            "title_en": "Superloop invoice",
            "text": "Superloop internet provider. Contact us 1800 57 87 37 or billing@home.superloop.com for billing support.",
        },
    ]
    rows = extract_slots_from_chunks(query_spec=spec, context_chunks=chunks)
    by_slot = {row["slot"]: row for row in rows}
    assert "superloop" in by_slot["vendor"]["value"].lower()
    assert "superloop" in by_slot["provider"]["value"].lower()
    assert "1800" in by_slot["contact_phone"]["value"]
    assert "superloop" in by_slot["contact_email"]["value"].lower()


def test_extract_slots_howto_uses_neighbor_chunks_and_extracts_steps():
    spec = {
        "task_kind": "howto_lookup",
        "subject_domain": "home",
        "target_slots": ["maintenance_interval"],
        "subject_aliases": ["water tank", "maintenance", "水箱"],
    }
    chunks = [
        {
            "doc_id": "doc-tank",
            "chunk_id": "c-1",
            "chunk_index": 1,
            "category_path": "home/maintenance",
            "title_en": "Water Tank Maintenance",
            "text": "Responsibility of each townhouse owner. The filters are located at the t op of the water tank and require to be checked and cleane",
        },
        {
            "doc_id": "doc-tank",
            "chunk_id": "c-2",
            "chunk_index": 2,
            "category_path": "home/maintenance",
            "title_en": "Water Tank Maintenance",
            "text": "d regularly. Check the tank filter monthly and clean debris if blocked. Inspect overflow outlet and flush if needed.",
        },
    ]
    rows = extract_slots_from_chunks(query_spec=spec, context_chunks=chunks)
    by_slot = {row["slot"]: row for row in rows}
    assert "maintenance_steps" in by_slot
    assert by_slot["maintenance_steps"]["status"] == "found"
    assert any(tok in by_slot["maintenance_steps"]["value"].lower() for tok in ("check", "clean", "inspect"))
    assert "maintenance_interval" in by_slot
    # If interval exists, it should not be the broken OCR fragment.
    if by_slot["maintenance_interval"]["status"] == "found":
        assert "onsibility" not in by_slot["maintenance_interval"]["value"].lower()
