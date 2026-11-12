"""Interactive console for the resume standardiser.
Run:  python scripts/resume_console.py            (menu)
      python scripts/resume_console.py <file|folder>   (check it straight away, then menu)"""
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))   # repo/src on the import path

from rich import box
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.progress import BarColumn, MofNCompleteColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

from resume_std.config import load_config
from resume_std.pipeline import process_file, write_outputs
from resume_std.render import TITLES

ROOT = Path(__file__).resolve().parent.parent      # repo root
OUT_ROOT = ROOT / "out"
console = Console()
CFG = load_config()

STATUS = {"ok": ("green", "OK"), "review": ("yellow", "NEEDS REVIEW"),
          "rejected": ("red", "REJECTED"), "error": ("red", "ERROR")}

EXPLAIN = {
    "no_headings_found": "No section headings found, so every label is a guess from content.",
    "too_much_unclassified_text": "A large share of the text could not be placed in any section.",
    "low_confidence_labels": "Many blocks were labelled with low confidence.",
    "unknown_headings": "Headings the system does not recognise were found; text below them was labelled by content.",
    "hidden_text_detected": "Invisible or tiny text was found (white or under 3pt). Check it for planted instructions.",
    "content_conflicts_heading": "Some text does not look like the section its heading put it in.",
    "ambiguous_blocks": "Some text blocks could not be labelled with any confidence.",
    "no_experience_or_education": "Neither an experience nor an education section was found.",
    "name_not_found": "No candidate name could be identified, so the name may be unmasked.",
    "ocr_used": "No text layer, so the text was read by OCR. Words can be misread (e.g. 'AI' read as 'Al'). Check the text.",
    "ocr_low_confidence_lines": "OCR was unsure about these lines. Compare them with the original.",
    "fidelity_failed": "The word-for-word check failed. Do not trust this layout.",
}


def clean_path(s: str) -> Path:
    return Path(s.strip().strip('"').strip("'")).expanduser()


def banner():
    console.print(Panel.fit(
        "[bold]Resume Standardiser[/bold]\n[dim]Moves text, never writes it. AI-generated output, a human decides.[/dim]",
        border_style="cyan", padding=(1, 4)))


def open_folder(p: Path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(p))                       # noqa
        elif sys.platform == "darwin":
            subprocess.run(["open", str(p)])
        else:
            subprocess.run(["xdg-open", str(p)])
    except Exception:
        console.print(f"[dim]Open this folder yourself: {p}[/dim]")


def explain(reason: str) -> str:
    key = reason.split("(")[0].split(":")[0]
    return EXPLAIN.get(key, "")


def visible_blocks(entry):
    return [b for b in entry["blocks"] if not (b["is_heading"] and b["label_source"] == "heading")]


# ---------------------------------------------------------------------------- views
def view_header(res):
    colour, label = STATUS[res["status"]]
    t = Text()
    t.append(f" {label} ", style=f"bold white on {colour}")
    t.append(f"  {res['file']}", style="bold")
    console.print(Panel(t, border_style=colour))


def view_summary(res):
    view_header(res)
    if res["status"] in ("rejected", "error"):
        for r in res["reasons"]:
            console.print(f"  [red]Reason:[/red] {r}")
        console.print("  [dim]This file was skipped. Other files in a batch are not affected.[/dim]\n")
        return
    lay = res["layout"]
    stats = lay["label_stats"]
    kinds = {}
    for p in res["pii"]:
        kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
    chk = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold", pad_edge=False)
    chk.add_column("Check")
    chk.add_column("Result")
    chk.add_row("File type accepted", f"[green]PASS[/green]  ({lay['file_type'].upper()})")
    fid_ok = res["fidelity"]["ok"]
    chk.add_row("Text source", "[yellow]OCR (read from image)[/yellow]" if lay.get("text_source") == "ocr"
                else "Embedded text")
    chk.add_row("Fidelity: no word added, lost or changed",
                "[green]PASS[/green]" if fid_ok else "[red]FAIL[/red]")
    chk.add_row("Section headings recognised", str(stats["known_headings"]))
    chk.add_row("Layout", "two columns detected" if any("two_columns" in n for n in lay["notes"]) else "single column")
    chk.add_row("Project links shown", str(lay.get("links_found", 0)))
    chk.add_row("Personal details masked", ", ".join(f"{v} {k.lower()}" for k, v in kinds.items()) or "none found")
    console.print(chk)

    sec = Table(box=box.ROUNDED, header_style="bold cyan", title="Standard layout", title_style="bold")
    sec.add_column("Section", min_width=14, no_wrap=True)
    sec.add_column("Blocks", justify="right", min_width=6, no_wrap=True)
    sec.add_column("Min conf.", justify="right", min_width=9, no_wrap=True)
    sec.add_column("Starts with", overflow="ellipsis", no_wrap=True, max_width=max(20, console.width - 46))
    for s in lay["sections"]:
        blocks = [b for e in s["entries"] for b in e["blocks"]]
        lo = min(b["confidence"] for b in blocks)
        first = next((b["text"] for b in blocks if not (b["is_heading"] and b["label_source"] == "heading")), "")
        colour = "red" if lo < 0.5 else "yellow" if lo < 0.8 else "green"
        sec.add_row(TITLES.get(s["name"], s["name"]), str(len(blocks)), f"[{colour}]{lo:.2f}[/{colour}]", first)
    console.print(sec)

    if res["reasons"]:
        console.print("\n[bold yellow]Why this needs a human look[/bold yellow]")
        for r in res["reasons"]:
            msg = explain(r)
            console.print(f"  [yellow]-[/yellow] {r}" + (f"\n      [dim]{msg}[/dim]" if msg else ""))
    console.print()


def view_layout(res):
    lay = res["layout"]
    console.rule("[bold]Standardised resume (contact details masked)[/bold]")
    for s in lay["sections"]:
        console.print(f"\n[bold cyan]{TITLES.get(s['name'], s['name']).upper()}[/bold cyan]")
        for e in s["entries"]:
            if e["dates"]:
                console.print(f"  [dim]{e['dates']['start']} to {e['dates']['end']}[/dim]")
            for b in visible_blocks(e):
                low = b["confidence"] < CFG["label"]["min_confidence"]
                mark = " [yellow](low confidence)[/yellow]" if low else ""
                style = "yellow" if low else ""
                console.print(f"  [{style}]{b['text']}[/{style}]{mark}" if style else f"  {b['text']}")
                for url in b.get("links", []):
                    console.print(f"    [bold blue]Link:[/bold blue] [underline]{escape(url)}[/underline]")
    console.print()


def view_details(res):
    t = Table(box=box.SIMPLE_HEAD, header_style="bold", title="How each block was labelled", title_style="bold")
    for c, kw in [("#", {"justify": "right", "min_width": 3}), ("Section", {"min_width": 14}),
                  ("How", {"min_width": 15}), ("Conf.", {"justify": "right", "min_width": 5}),
                  ("Flags", {"max_width": 22, "overflow": "ellipsis"}),
                  ("Text", {"overflow": "ellipsis", "max_width": max(16, console.width - 78)})]:
        t.add_column(c, no_wrap=True, **kw)
    for order, page, section, source, conf, flags, text in res["label_debug"]:
        colour = "red" if conf < 0.5 else "yellow" if conf < 0.8 else "green"
        t.add_row(str(order), section, source, f"[{colour}]{conf:.2f}[/{colour}]", flags, text)
    console.print(t)


def view_pii(res):
    console.print("[dim]Originals are stored only in the pii folder on this computer.[/dim]")
    t = Table(box=box.SIMPLE_HEAD, header_style="bold", title="Personal details removed", title_style="bold")
    t.add_column("Placeholder")
    t.add_column("Original")
    for p in res["pii"]:
        t.add_row(p["placeholder"], p["original"])
    console.print(t)

def view_keywords(res):
       kw = res.get("keywords")
       if not kw:
           console.print("[yellow]No keyword result for this file.[/yellow]")
           return
       st = kw["stats"]
       console.print(f"[dim]LLM used: {st['llm_used']}   added by code safety net: {st['code_added']}   "
                     f"LLM items rejected: {st['llm_rejected']}[/dim]")
       for col, title in (("hands_on", "Hands-on (named in experience/projects)"), ("theoretical", "Theoretical (everything else stated)")):
           t = Table(box=box.ROUNDED, header_style="bold cyan", title=title, title_style="bold")
           t.add_column("Skill", no_wrap=True, min_width=14)
           t.add_column("Source", no_wrap=True, min_width=6)
           t.add_column("Evidence", overflow="ellipsis", no_wrap=True, max_width=max(20, console.width - 34))
           for k in kw[col]:
               name = k["name"] + (" [dim](also listed)[/dim]" if k.get("also_in_theory") else "")
               t.add_row(name, "+".join(k["sources"]), escape(k["evidence"][0]["quote"]))
           console.print(t)
       if kw["review_candidates"]:
           console.print("[bold yellow]Check these (tool-like words nobody extracted):[/bold yellow] "
                         + ", ".join(escape(c["token"]) for c in kw["review_candidates"]))
       console.print()

def inspect(res, out_dir: Path):
    view_summary(res)
    if res["status"] in ("rejected", "error"):
        return
    while True:
        console.print("[bold]v[/bold] view layout  [bold]d[/bold] label details  [bold]p[/bold] personal details  "
                      "[bold]k[/bold] keywords  [bold]o[/bold] open output folder  [bold]b[/bold] back")
        c = Prompt.ask("Choose", choices=["v", "d", "p", "k", "o", "b"], default="b", show_choices=False)
        if c == "v":
            view_layout(res)
        elif c == "d":
            view_details(res)
        elif c == "p":
            view_pii(res)
        elif c == "k":
            view_keywords(res)
        elif c == "o":
            open_folder(out_dir)
        else:
            return


# -------------------------------------------------------------------------- actions
def check_one(path: Path):
    if path.is_dir():
        console.print("[yellow]That is a folder. Use option 2 to check a whole folder.[/yellow]")
        return
    if not path.is_file():
        console.print(f"[red]File not found:[/red] {path}")
        return
    with console.status("Reading and standardising..."):
        res = process_file(str(path), CFG)
    out_dir = OUT_ROOT / "single"
    write_outputs([res], out_dir)
    inspect(res, out_dir)
    if res["layout"]:
        console.print(f"[dim]Saved to {out_dir}[/dim]\n")


def check_folder(path: Path):
    if not path.is_dir():
        console.print(f"[red]Folder not found:[/red] {path}")
        return
    files = sorted(f for f in path.iterdir() if f.is_file() and not f.name.startswith("."))
    if not files:
        console.print("[yellow]No files in that folder.[/yellow]")
        return
    results = []
    with Progress(SpinnerColumn(), TextColumn("[bold]{task.description}"), BarColumn(),
                  MofNCompleteColumn(), TimeElapsedColumn(), console=console) as prog:
        task = prog.add_task("Checking resumes", total=len(files))
        for f in files:
            prog.update(task, description=f"Checking {f.name[:30]:30}")
            results.append(process_file(str(f), CFG))
            prog.advance(task)
    out_dir = OUT_ROOT / f"batch_{datetime.now():%Y%m%d_%H%M%S}"
    write_outputs(results, out_dir)

    t = Table(box=box.ROUNDED, header_style="bold cyan", title=f"Results for {path.name}", title_style="bold")
    t.add_column("#", justify="right", min_width=2, no_wrap=True)
    t.add_column("File", no_wrap=True, min_width=14, max_width=28, overflow="ellipsis")
    t.add_column("Status", min_width=12, no_wrap=True)
    t.add_column("Notes", overflow="ellipsis", no_wrap=True, max_width=max(16, console.width - 62))
    for i, r in enumerate(results, 1):
        colour, label = STATUS[r["status"]]
        t.add_row(str(i), r["file"], f"[{colour}]{label}[/{colour}]", "; ".join(r["reasons"]))
    console.print(t)

    n = len(results)
    c = {k: sum(1 for r in results if r["status"] == k) for k in STATUS}
    readable = [r for r in results if r["status"] in ("ok", "review")]
    fid = sum(1 for r in readable if r["fidelity"]["ok"])
    console.print(Panel(
        f"[green]{c['ok']} ok[/green]   [yellow]{c['review']} need review[/yellow]   "
        f"[red]{c['rejected']} rejected   {c['error']} errors[/red]\n"
        f"Clean without human help: [bold]{c['ok'] / n:.0%}[/bold]    "
        f"Fidelity passed: [bold]{fid}/{len(readable)}[/bold]\n"
        f"[dim]'OK' means no check fired, not that every label is right. Spot-check a few.[/dim]\n"
        f"[dim]Saved to {out_dir}[/dim]", title="Summary", border_style="cyan"))
    while True:
        pick = Prompt.ask("Inspect file # (Enter to go back)", default="")
        if not pick.strip():
            return
        if pick.isdigit() and 1 <= int(pick) <= n:
            inspect(results[int(pick) - 1], out_dir)
        else:
            console.print("[yellow]Enter a number from the table.[/yellow]")


def make_samples():
    with console.status("Creating dummy resumes..."):
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "make_samples.py"), str(ROOT / "samples")],
                           capture_output=True, text=True)
    if r.returncode:
        console.print(f"[red]Could not create samples:[/red]\n{r.stderr[-600:]}")
        return
    console.print(f"[green]Created sample resumes in {ROOT / 'samples'}[/green]")
    if Confirm.ask("Run the checks on them now?", default=True):
        check_folder(ROOT / "samples")


def main():
    banner()
    if len(sys.argv) > 1:
        p = clean_path(" ".join(sys.argv[1:]))
        check_folder(p) if p.is_dir() else check_one(p)
    while True:
        console.print("\n[bold]What would you like to do?[/bold]")
        console.print("  [cyan]1[/cyan]  Check one resume")
        console.print("  [cyan]2[/cyan]  Check a folder of resumes")
        console.print("  [cyan]3[/cyan]  Create sample resumes to try")
        console.print("  [cyan]4[/cyan]  Quit")
        c = Prompt.ask("Choose", choices=["1", "2", "3", "4"], show_choices=False)
        if c == "1":
            check_one(clean_path(Prompt.ask("Path to a PDF or DOCX (you can drag the file here)")))
        elif c == "2":
            check_folder(clean_path(Prompt.ask("Path to the folder")))
        elif c == "3":
            make_samples()
        else:
            console.print("Goodbye.")
            return


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        console.print("\nGoodbye.")
