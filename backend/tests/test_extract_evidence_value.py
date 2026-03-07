"""
Unit tests for _extract_evidence_value() — specifically birth_date context validation.

Regression coverage for the Fluffy vet-certificate bug:
  Vet cert chunk contains both DOB (04 November 2024) and procedure date (31 October 2025).
  The wrong date was being returned because:
    - re.search() matched the procedure date first (it appeared earlier in text)
    - ±150 char symmetric window included "DOB" keyword → _ctx_ok incorrectly passed
  Fixed by:
    - Narrowing birth_date check to pre-context only (35 chars before match)
    - Switching to re.finditer() so rejected matches are skipped
    - Adding medical procedure keywords to _BIRTH_ANTI
"""

import pytest

from app.services.detail_extract import _extract_evidence_value

pytestmark = pytest.mark.no_db_reset  # pure unit tests — no DB needed


class TestBirthDateExtraction:

    def test_dob_label_extracts_correctly(self):
        """Standard DOB label immediately before date."""
        text = "Animal: Fluffy\nDOB: 04 November 2024\nBreed: Cavoodle"
        assert _extract_evidence_value(text, "pets", "birth_date") == "04 November 2024"

    def test_date_of_birth_label_extracts_correctly(self):
        """'Date of Birth' label — also numeric slash format."""
        text = "Date of Birth: 04/11/2024\nDate of Procedure: 31/10/2025"
        assert _extract_evidence_value(text, "pets", "birth_date") == "04/11/2024"

    def test_procedure_date_first_dob_second_picks_dob(self):
        """Core regression: procedure date before DOB must still return DOB (finditer fix)."""
        text = "Date of Procedure: 31 October 2025\nDOB: 04 November 2024"
        assert _extract_evidence_value(text, "pets", "birth_date") == "04 November 2024"

    def test_vaccination_date_rejected(self):
        """Vaccination anti-keyword must prevent extraction."""
        text = "Vaccination Date: 21 December 2025"
        assert _extract_evidence_value(text, "pets", "birth_date") == ""

    def test_desexing_date_rejected(self):
        """Desexing is a medical procedure — date must be rejected (new anti-keyword)."""
        text = "Desexing Date: 31 October 2025"
        assert _extract_evidence_value(text, "pets", "birth_date") == ""

    def test_surgery_date_rejected(self):
        """Surgery keyword — date must be rejected."""
        text = "Surgery: 31 October 2025"
        assert _extract_evidence_value(text, "pets", "birth_date") == ""

    def test_no_birth_context_rejected(self):
        """Date with no surrounding label must not be extracted as birth_date."""
        text = "31 October 2025"
        assert _extract_evidence_value(text, "pets", "birth_date") == ""

    def test_chinese_birth_label(self):
        """Chinese birth label with ISO date format."""
        text = "出生日期: 2024-11-04"
        assert _extract_evidence_value(text, "pets", "birth_date") == "2024-11-04"

    def test_birthday_keyword_accepted(self):
        """'birthday' keyword is a valid birth label."""
        text = "birthday: 04 November 2024"
        assert _extract_evidence_value(text, "pets", "birth_date") == "04 November 2024"

    def test_full_vet_cert_chunk_procedure_before_dob(self):
        """Realistic vet cert: procedure date appears before DOB in extracted text."""
        text = (
            "Certificate of Desexing\n"
            "Animal Name: Fluffy\n"
            "Date of Procedure: 31 October 2025\n"
            "DOB: 04 November 2024\n"
            "Breed: Cavoodle\n"
        )
        assert _extract_evidence_value(text, "pets", "birth_date") == "04 November 2024"

    def test_full_vet_cert_chunk_dob_before_procedure(self):
        """Vet cert where DOB comes before procedure — should also return DOB."""
        text = (
            "DOB: 04 November 2024\n"
            "Date of Procedure: 31 October 2025\n"
        )
        assert _extract_evidence_value(text, "pets", "birth_date") == "04 November 2024"

    def test_dob_only_no_other_dates(self):
        """Simple case: only DOB present."""
        text = "DOB: 04-11-2024"
        assert _extract_evidence_value(text, "pets", "birth_date") == "04-11-2024"

    def test_born_keyword_accepted(self):
        """'born' is a valid birth label."""
        text = "Fluffy was born: 04 November 2024"
        assert _extract_evidence_value(text, "pets", "birth_date") == "04 November 2024"
