> Moved into the combined repo. Paths below are the new ones; see the top-level README for the whole project.

# Resume standardiser (prototype, no LLM)

Moves text, never writes it: PDF/DOCX -> blocks -> mask PII -> label sections by rules -> fixed layout -> fidelity check.

    pip install -r requirements.txt
    python scripts/resume_console.py                         # interactive console (start here)
    python tools/make_samples.py samples                     # dummy resumes + gold labels
    python scripts/resume_cli.py samples --out out           # run the pipeline
    python tools/evaluate.py samples/gold out                # are the labels actually right?
    python -m pytest -q

Outputs in `out/`: `layout/*.json|md|docx`, `debug/*.labels.txt` (what each block was labelled and why),
`pii/*.pii.json` (originals; keep private), `review_log.csv` (fill in the last two columns as you review).

Tune everything in `config/config.yaml` (section `phase2`). Status `ok` means no check fired, not that labels are right; use `evaluate.py`.

OCR (for PDFs with no text layer, e.g. scans or 'Microsoft Print to PDF'): install the Tesseract engine
(Windows: https://github.com/UB-Mannheim/tesseract/wiki), then `pip install -r requirements.txt`. OCR files always
go to review. Set `ocr.enabled: false` in config/config.yaml (section `phase2`) to reject them instead.

Known limits: OCR can misread words (a short list of known misreads such as AI/Al is auto-corrected and flagged); DOCX text boxes, headers and footers are not read (the coverage
check flags text boxes); date-column tables are handled, but unusual multi-column layouts may be misordered;
unknown headings that look like body text are inherited by the section above; line-merge gap ratio is tuned
on generated PDFs, so check it on real ones; address masking covers pincodes only.

Privacy settings (`config/config.yaml` (section `phase2`), `privacy:`): `show_name` shows or hides the name. LinkedIn/social URLs and bare profile pages
(github.com/username) are masked; repo, notebook (Colab, Kaggle, Drive) and article links are shown, including links hidden
behind clickable text such as "Colab notebook".
