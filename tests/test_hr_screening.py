from scoring.hr_screening import HR_POINTS, MARK_CHOICES, NOT_MARKED, blank_packet, hr_score


def test_points_come_from_config():
    assert HR_POINTS == {"Met": 1.0, "Partly met": 0.5, "Not met": 0.0}
    assert MARK_CHOICES[0] == NOT_MARKED


def test_score_counts_only_marked_questions():
    assert hr_score(["Met", "Partly met", "Not met", None, NOT_MARKED]) == (1.5, 3)
    assert hr_score([]) == (0, 0)
    assert hr_score([None, NOT_MARKED]) == (0, 0)


def test_blank_packet_has_one_empty_sheet_per_candidate():
    qs = [{"question": "Q1"}, {"question": "Q2"}]
    p = blank_packet(qs, ["a.pdf", "b.pdf"])
    assert p["questions"] == qs and set(p["candidates"]) == {"a.pdf", "b.pdf"}
    assert p["candidates"]["a.pdf"]["marks"] == [None, None]
