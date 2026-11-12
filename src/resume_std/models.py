from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Block:
    id: int
    text: str                      # masked after mask_pii; verbatim from extraction before
    raw_text: str = ""             # verbatim extraction (baseline for the fidelity check)
    page: int = 0
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 0.0
    y1: float = 0.0
    font_size: float = 0.0
    bold: bool = False
    is_bullet: bool = False
    style: str = ""                # DOCX paragraph style name
    in_table: bool = False
    column: int = -1               # -1 full width / single column, 0 left, 1 right
    order: int = -1                # reading-order position
    section: str = ""
    label_source: str = ""         # heading | heading_inline | heading_style | inferred
    confidence: float = 0.0
    is_heading: bool = False
    flags: list = field(default_factory=list)
    links: list = field(default_factory=list)      # project/work links (visible or hidden behind clickable text)


@dataclass
class PIIItem:
    placeholder: str
    kind: str
    original: str
    block_id: int


@dataclass
class ExtractResult:
    blocks: list
    file_type: str
    page_sizes: dict               # page -> (width, height); empty for DOCX
    reference_chars: int           # independent count of non-space characters in the file
    notes: list = field(default_factory=list)
    ocr: bool = False
