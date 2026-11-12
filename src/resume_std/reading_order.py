"""Step 3: fix reading order. PDFs only; DOCX is already in document order."""


def _detect_split(page_blocks, page_w, rcfg):
    narrow = [b for b in page_blocks if (b.x1 - b.x0) < 0.6 * page_w]
    if len(narrow) < 2 * rcfg["min_blocks_per_column"]:
        return None
    clusters = []
    for x in sorted(b.x0 for b in narrow):
        if clusters and x - clusters[-1][-1] <= 15:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    if len(clusters) < 2:
        return None
    gaps = [(clusters[i + 1][0] - clusters[i][-1], i) for i in range(len(clusters) - 1)]
    gap, i = max(gaps)
    if gap < rcfg["column_gap_ratio"] * page_w:
        return None
    left = [b for b in narrow if b.x0 <= clusters[i][-1]]
    right = [b for b in narrow if b.x0 >= clusters[i + 1][0]]
    if min(len(left), len(right)) < rcfg["min_blocks_per_column"]:
        return None
    left_edge, right_start = max(b.x1 for b in left), min(b.x0 for b in right)
    if left_edge > right_start - 5:
        return None  # the left column runs into the right one: not clean columns
    for col in (left, right):
        if max(b.x1 for b in col) - min(b.x0 for b in col) < rcfg["min_column_width_ratio"] * page_w:
            return None  # e.g. a narrow date column next to details is NOT a sidebar layout
    split = (left_edge + right_start) / 2
    return split


def order_blocks(blocks, page_sizes, file_type, rcfg):
    """Returns (blocks in reading order, notes). Sets block.order and block.column."""
    notes = []
    if file_type != "pdf":
        for i, b in enumerate(sorted(blocks, key=lambda b: b.id)):
            b.order = i
        return sorted(blocks, key=lambda b: b.order), notes
    ordered = []
    for pno in sorted({b.page for b in blocks}):
        pb = [b for b in blocks if b.page == pno]
        split = _detect_split(pb, page_sizes[pno][0], rcfg)
        if split is None:
            ordered += sorted(pb, key=lambda b: (b.y0, b.x0))
            continue
        notes.append(f"two_columns_detected(page={pno})")
        strad = sorted([b for b in pb if b.x0 < split - 5 and b.x1 > split + 5], key=lambda b: b.y0)
        keyed = []
        for b in pb:
            if b in strad:
                keyed.append(((strad.index(b), 1, 0, b.y0), b))
                b.column = -1
            else:
                band = sum(1 for s in strad if s.y0 < b.y0)
                b.column = 0 if b.x1 <= split + 5 else 1
                keyed.append(((band, 0, b.column, b.y0), b))
        ordered += [b for _, b in sorted(keyed, key=lambda kb: kb[0])]
    for i, b in enumerate(ordered):
        b.order = i
    return ordered, notes
