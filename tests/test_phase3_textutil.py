from interview.textutil import jaccard, mask_pii, quote_in_text

SRC = "- Built payment APIs in Python and\n  FastAPI serving 200 requests per second\n\u2022 Tuned slow PostgreSQL queries"


def test_quote_ignores_case_whitespace_bullets():
    assert quote_in_text("built payment APIs in python and fastapi serving 200 requests", SRC)


def test_quote_with_ellipsis_parts_in_order():
    assert quote_in_text("Built payment APIs ... 200 requests per second.", SRC)
    assert not quote_in_text("200 requests per second ... Built payment APIs", SRC)


def test_fabricated_or_tiny_quote_rejected():
    assert not quote_in_text("Built a Kafka cluster with 50 nodes", SRC)
    assert not quote_in_text("Python", SRC)
    assert not quote_in_text("", SRC)


def test_mask_pii_keeps_date_ranges():
    out = mask_pii("rahul@example.com | +91 98765 43210 | Jan 2019 - 2022")
    assert "[EMAIL]" in out and "[PHONE]" in out and "2019 - 2022" in out


def test_jaccard():
    assert jaccard("explain how docker works", "explain how docker works") == 1.0
    assert jaccard("explain docker", "describe kafka") == 0.0
