"""Step 7: the safety net. Proves nothing was added, lost, or changed between extraction and layout."""
from .layout import layout_blocks
from .mask_pii import unmask
from .textutil import nonspace_len


def check_fidelity(blocks, layout, pii_items, reference_chars):
    problems = []
    lay = list(layout_blocks(layout))
    ids_in = sorted(b.id for b in blocks)
    ids_out = sorted(b["id"] for b in lay)
    if ids_in != ids_out:
        missing = sorted(set(ids_in) - set(ids_out))
        extra = sorted(set(ids_out) - set(ids_in))
        dup = len(ids_out) - len(set(ids_out))
        problems.append(f"block_ids_differ(missing={missing[:5]}, extra={extra[:5]}, duplicates={dup})")
    by_id = {b.id: b for b in blocks}
    for lb in lay:
        src = by_id.get(lb["id"])
        if src is None:
            continue
        if lb["text"] != src.text:
            problems.append(f"text_changed(block={lb['id']})")
        elif unmask(lb["text"], pii_items, lb["id"]) != src.raw_text:
            problems.append(f"unmask_mismatch(block={lb['id']})")
    ours = sum(nonspace_len(b.raw_text) for b in blocks)
    if ours != reference_chars:
        problems.append(f"extraction_coverage(blocks={ours} chars, file={reference_chars} chars; "
                        f"content such as text boxes may be missing)")
    return {"ok": not problems, "problems": problems}
