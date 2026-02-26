import pytest

from app.schemas import DetailEvidenceRef, DetailRow, DetailSection
from app.services.agent_graph_nodes import _build_graph_evidence_outputs

pytestmark = pytest.mark.no_db_reset


def test_graph_evidence_linking_prefers_slot_evidence_over_context_noise():
    class LegacyStub:
        @staticmethod
        def _build_related_docs(_db, doc_ids, cap=6):
            return [{"doc_id": doc_id, "file_name": f"{doc_id}.pdf"} for doc_id in list(doc_ids)[:cap]]

    slot_results = [
        {
            "slot": "registration_no",
            "value": "PET-12345",
            "status": "found",
            "source_doc_ids": ["pet-doc"],
            "evidence_refs": [{"doc_id": "pet-doc", "chunk_id": "pet-c1", "evidence_text": "Registration No: PET-12345"}],
        }
    ]
    detail_sections = [
        DetailSection(
            section_name="slot_results",
            rows=[
                DetailRow(
                    field="registration_no",
                    label_en="Registration No",
                    label_zh="登记证号",
                    value_en="PET-12345",
                    value_zh="PET-12345",
                    evidence_refs=[DetailEvidenceRef(doc_id="pet-doc", chunk_id="pet-c1", evidence_text="Registration No: PET-12345")],
                )
            ],
        )
    ]
    context_chunks = [
        {"doc_id": "noise-doc", "chunk_id": "noise-c1", "title_zh": "无关文档", "title_en": "Noise", "text": "irrelevant"},
        {"doc_id": "pet-doc", "chunk_id": "pet-c1", "title_zh": "宠物登记", "title_en": "Pet Registration", "text": "Registration No: PET-12345"},
    ]

    out = _build_graph_evidence_outputs(
        db=None,
        legacy_agent=LegacyStub,
        slot_results=slot_results,
        detail_sections=detail_sections,
        derivations=[],
        context_chunks=context_chunks,
    )

    assert out["related_doc_selection_mode"] in {"slot_evidence_first", "slot_evidence_plus_context"}
    assert out["evidence_link_quality"] == "slot_evidence_first"
    assert out["slot_evidence_doc_ids"] == ["pet-doc"]
    assert out["sources"][0].doc_id == "pet-doc"
    assert out["sources"][0].chunk_id == "pet-c1"
    assert out["related_docs"][0]["doc_id"] == "pet-doc"
