from phase1.schemas import ExtractedSkill, Importance
from phase1.taxonomy import UNMAPPED, normalise, normalise_skills


def sid(raw):
    return normalise(raw)[0]


def test_react_variants_map_to_react():
    for v in ["React", "ReactJS", "React.js", "react js", "REACT", " React JS "]:
        assert sid(v) == "react", v


def test_node_variants_map_to_nodejs():
    for v in ["Node.js", "NodeJS", "node js", "Node"]:
        assert sid(v) == "nodejs", v


def test_unknown_is_unmapped():
    assert sid("Zzzlang") == UNMAPPED
    assert sid("") == UNMAPPED


def test_similar_names_do_not_merge():
    assert sid("React Native") == "react_native"
    assert sid("Java") == "java" and sid("JavaScript") == "javascript"
    assert sid("C#") == "csharp" and sid("C++") == "cpp" and sid("C") == "c"
    assert sid(".NET") == "dotnet" and sid("ASP.NET Core") == "aspnet"
    assert sid("Angular") == "angular" and sid("AngularJS") == "angularjs"


def test_real_world_spellings():
    assert sid("Python (3.x)") == "python"
    assert sid("k8s") == "kubernetes" and sid("CI/CD") == "ci_cd"
    assert sid("UI/UX") == "ui_ux_design" and sid("Postgres") == "postgresql"
    assert sid("scikit-learn") == "scikit_learn"


def test_strict_fuzzy():
    assert normalise("Kubernetis") == ("kubernetes", "fuzzy")
    assert sid("Jav") == UNMAPPED


def test_deduplication_must_have_wins():
    demo = [
        ExtractedSkill(raw_text="React js", category="Framework", importance="nice_to_have",
                       evidence_quote="React js is a plus"),
        ExtractedSkill(raw_text="ReactJS", category="Framework", importance="must_have",
                       required_experience="2+ years", evidence_quote="ReactJS 2+ years"),
    ]
    out = normalise_skills(demo)
    assert len(out) == 1
    assert out[0].importance == Importance.MUST_HAVE
    assert out[0].raw_text == "ReactJS" and out[0].standard_id == "react"


# ---------- v3: bigger taxonomy, filler words, combined items ----------
from phase1.taxonomy import normalise_many  # noqa: E402


def many(raw):
    return [(sid) for _, sid, _ in normalise_many(raw)]


def test_ai_ml_basics_are_known():
    for raw, expected in [
        ("Matplotlib", "matplotlib"), ("Seaborn", "seaborn"), ("NumPy", "numpy"),
        ("Pandas", "pandas"), ("scikit-learn", "scikit_learn"), ("sklearn", "scikit_learn"),
        ("TensorFlow", "tensorflow"), ("PyTorch", "pytorch"), ("Keras", "keras"),
        ("OpenCV", "opencv"), ("NLP", "nlp"), ("Deep Learning", "deep_learning"),
        ("Random Forest", "random_forest"), ("Jupyter Notebook", "jupyter"),
    ]:
        assert sid(raw) == expected, raw


def test_filler_words_are_ignored():
    assert sid("Python programming") == "python"
    assert sid("Strong knowledge of SQL") == "sql"
    assert sid("Hands-on experience with Docker") == "docker"
    assert sid("React framework") == "react"
    assert sid("Experience in Java") == "java"
    assert sid("ML models") == "machine_learning"


def test_combined_items_are_split():
    assert many("Pandas/NumPy") == ["pandas", "numpy"]
    assert many("Matplotlib & Seaborn") == ["matplotlib", "seaborn"]
    assert many("HTML/CSS") == ["html", "css"]
    assert many("TensorFlow or PyTorch") == ["tensorflow", "pytorch"]
    assert many("C/C++") == ["c", "cpp"]
    assert many("AI/ML models") == ["artificial_intelligence", "machine_learning"]
    assert many("Problem solving and communication skills") == ["problem_solving", "communication"]


def test_known_slash_names_stay_whole():
    for raw, expected in [("CI/CD", "ci_cd"), ("UI/UX", "ui_ux_design"), ("A/B testing", "a_b_testing"),
                          ("TCP/IP", "tcp_ip"), ("PL/SQL", "plsql")]:
        assert many(raw) == [expected], raw


def test_split_does_not_invent_skills():
    assert many("Zzzlang/Qqqlang") == [UNMAPPED]
    assert many("R&D") == [UNMAPPED]                       # must not become the language R
    assert many("Data Structures and Algorithms") == ["dsa"]


def test_combined_item_in_full_flow():
    demo = [ExtractedSkill(raw_text="Pandas/NumPy", category="Framework", importance="must_have",
                           evidence_quote="Pandas/NumPy required")]
    out = normalise_skills(demo)
    assert [s.standard_id for s in out] == ["pandas", "numpy"]
    assert all(s.importance == Importance.MUST_HAVE for s in out)


def test_filler_only_at_the_edges_still_finds_full_names():
    assert sid("Strong analytical skills") == "analytical_thinking"
    assert sid("Good communication skills") == "communication"
    assert sid("Cloud platforms") == "cloud_computing"
    assert sid("Good coding practices") == UNMAPPED          # vague, stays Unmapped on purpose
