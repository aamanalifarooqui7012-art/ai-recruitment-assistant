"""Run with: pytest -q   (generates dummy resumes into a temp folder first)"""
import copy
import subprocess
import sys
from pathlib import Path

import pytest

from resume_std.config import load_config
from resume_std.fidelity import check_fidelity
from resume_std.label import label_blocks
from resume_std.layout import build_layout
from resume_std.mask_pii import mask_pii
from resume_std.pipeline import process_file
from resume_std.reading_order import order_blocks
from resume_std.extract import extract

ROOT = Path(__file__).resolve().parent.parent
CFG = load_config()


@pytest.fixture(scope="session")
def samples(tmp_path_factory):
    d = tmp_path_factory.mktemp("samples")
    subprocess.run([sys.executable, str(ROOT / "tools" / "make_samples.py"), str(d)], check=True)
    return d


def run(samples, name):
    return process_file(str(samples / name), CFG)


NO_OCR = {**CFG, "ocr": {**CFG["ocr"], "enabled": False}}


def test_scan_rejected_when_ocr_off(samples):
    r = process_file(str(samples / "scanned.pdf"), NO_OCR)
    assert r["status"] == "rejected" and r["reasons"][0].startswith("scanned_or_image_only")


@pytest.mark.parametrize("name,code", [
    ("protected.pdf", "password_protected"),
    ("corrupt.pdf", "corrupt"), ("legacy.doc", "legacy_or_encrypted_office"), ("notes.txt", "unsupported_type")])
def test_bad_files_rejected_with_reason(samples, name, code):
    r = run(samples, name)
    assert r["status"] == "rejected" and r["reasons"][0].startswith(code)


def sections(r):
    return {s["name"]: [b["text"] for e in s["entries"] for b in e["blocks"]] for s in r["layout"]["sections"]}


def test_headless_summary_is_found(samples):
    r = run(samples, "headless_summary.pdf")
    assert r["status"] == "ok"
    assert any(t.startswith("Backend developer with five years") for t in sections(r)["summary"])
    assert any("Python" in t for t in sections(r)["skills"])


def test_pii_is_masked_and_stored_separately(samples):
    r = run(samples, "classic_single.pdf")
    text = " ".join(t for ts in sections(r).values() for t in ts)
    assert "@" not in text and "98765" not in text and "linkedin.com" not in text
    assert "Aarav Mehta" in text            # name is shown by default (privacy.show_name)
    assert {p["kind"] for p in r["pii"]} >= {"EMAIL", "PHONE", "URL"}


def test_two_columns_do_not_mix(samples):
    r = run(samples, "two_column_sidebar.pdf")
    assert "two_columns_detected(page=1)" in r["notes"]
    assert any("PostgreSQL" in t for t in sections(r)["skills"])
    assert not any("PostgreSQL" in t for t in sections(r).get("experience", []))


def test_experience_dates_parsed(samples):
    r = run(samples, "classic_single.pdf")
    exp = next(s for s in r["layout"]["sections"] if s["name"] == "experience")
    assert [e["dates"] for e in exp["entries"] if e["dates"]] == [
        {"start": "2021-01", "end": "present"}, {"start": "2018-06", "end": "2020-12"}]


def test_hidden_text_goes_to_review(samples):
    r = run(samples, "hidden_text.pdf")
    assert r["status"] == "review" and "hidden_text_detected" in r["reasons"]


def test_no_headings_never_silently_ok(samples):
    assert run(samples, "no_headings.pdf")["status"] == "review"


def test_every_readable_sample_passes_fidelity(samples):
    for f in samples.iterdir():
        r = run(samples, f.name)
        if r["status"] in ("ok", "review"):
            assert r["fidelity"]["ok"], (f.name, r["fidelity"])


def test_fidelity_catches_tampering(samples):
    ex = extract(str(samples / "classic_single.pdf"), "pdf", CFG)
    blocks, _ = order_blocks(ex.blocks, ex.page_sizes, "pdf", CFG["reading_order"])
    pii, _ = mask_pii(blocks)
    label_blocks(blocks, CFG)
    layout = build_layout(blocks, {"status": "x"})
    assert check_fidelity(blocks, layout, pii, ex.reference_chars)["ok"]
    bad = copy.deepcopy(layout)
    bad["sections"][0]["entries"][0]["blocks"][0]["text"] = "Improved text"      # an "improvement"
    assert not check_fidelity(blocks, bad, pii, ex.reference_chars)["ok"]
    bad = copy.deepcopy(layout)
    bad["sections"][1]["entries"][0]["blocks"].pop()                               # a dropped block
    assert not check_fidelity(blocks, bad, pii, ex.reference_chars)["ok"]


def test_scan_read_by_ocr_and_always_reviewed(samples):
    from resume_std.ocr import tesseract_ready
    if not tesseract_ready(CFG)[0]:
        pytest.skip("Tesseract not installed")
    r = run(samples, "scanned.pdf")
    assert r["status"] == "review" and r["reasons"][0].startswith("ocr_used")
    assert r["fidelity"]["ok"] and r["layout"]["text_source"] == "ocr"
    assert {"experience", "education"} <= {s["name"] for s in r["layout"]["sections"]}


def _links(r):
    return {b["text"]: b["links"] for s in r["layout"]["sections"] for e in s["entries"] for b in e["blocks"] if b["links"]}


@pytest.mark.parametrize("name", ["links.pdf", "links.docx"])
def test_project_links_shown_profile_links_masked(samples, name):
    r = run(samples, name)
    found = _links(r)
    flat = [u for urls in found.values() for u in urls]
    assert "https://colab.research.google.com/drive/1AbCdEf" in flat                 # hidden behind "Colab notebook"
    if name.endswith("pdf"):
        assert "https://github.com/aaravmehta/expense-tracker" in flat               # visible repo URL
        text = " ".join(b["text"] for s in r["layout"]["sections"] for e in s["entries"] for b in e["blocks"])
        assert "github.com/aaravmehta/expense-tracker" in text
        assert "linkedin.com" not in text and "GitHub profile: [URL_" in text      # profile URLs masked
    assert r["fidelity"]["ok"]
