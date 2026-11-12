"""The repo was re-organised from three folders into one. These checks prove every path still resolves."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_roots_point_at_the_repo():
    import phase1.settings as s1
    import resume_std.taxonomy as t2
    assert s1.ROOT == REPO and t2.ROOT == REPO


def test_config_files_exist():
    for f in ("config.yaml", "skills_taxonomy.yaml"):
        assert (REPO / "config" / f).is_file(), f


def test_both_phases_read_the_same_single_taxonomy():
    from phase1.settings import path as p1_path
    from resume_std.config import DEFAULT_PATH, load_config
    cfg = load_config()
    assert DEFAULT_PATH == REPO / "config" / "config.yaml"
    assert p1_path("taxonomy") == REPO / "config" / "skills_taxonomy.yaml"
    assert (REPO / cfg["keywords"]["taxonomy_path"]).resolve() == p1_path("taxonomy").resolve()
    import phase1.taxonomy as t1
    from resume_std.taxonomy import get_taxonomy
    assert set(t1.TAXONOMY.skills) == set(get_taxonomy(cfg).skills)       # same ids, same skills


def test_phase1_output_folders_stay_inside_the_repo():
    from phase1.settings import path as p1_path
    for key in ("results_dir", "unmapped_queue"):
        assert REPO in p1_path(key).parents


def test_no_secrets_or_real_documents_are_tracked_by_default():
    ignore = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    for rule in (".env", ".venv/", ".cache/", "out/", "output/", "*.pdf", "*.docx"):
        assert rule in ignore, rule
    assert (REPO / ".env.example").is_file() and not (REPO / ".env").exists()
