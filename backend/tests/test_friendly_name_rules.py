from app.services.friendly_name import generate_friendly_names


def test_friendly_name_electricity_not_misclassified_as_water():
    text = (
        "YOUR ELECTRICITY ACCOUNT PLAN AND USAGE DETAILS. "
        "For complaints contact Energy and Water Ombudsman."
    )
    en, zh = generate_friendly_names(
        file_name="Invoice9804231.pdf",
        text=text,
        category_path="finance/bills/electricity",
        source_type="mail",
    )
    assert "Electricity" in en
    assert "电费" in zh
    assert "Water" not in en
    assert "水费" not in zh


def test_friendly_name_non_bill_does_not_force_date_prefix():
    text = "Rheem installation manual. Model CF-12-26-876A-874A. Updated 2026-02."
    en, zh = generate_friendly_names(
        file_name="Rheem-CF-12-26-876A-874A-Series-TD.pdf",
        text=text,
        category_path="home/manuals",
        source_type="nas",
    )
    assert not en.startswith("2026")
    assert not zh.startswith("2026年")


def test_friendly_name_bill_keeps_date_prefix():
    text = "Water bill invoice. Billing period 2026-02. Amount due AUD $166.20."
    en, zh = generate_friendly_names(
        file_name="invoice_202602.pdf",
        text=text,
        category_path="finance/bills/water",
        source_type="mail",
    )
    assert en.startswith("2026-02")
    assert zh.startswith("2026年2月")
