"""Regression: real PDFs produced 'Neo4j ,  enabling' and 'LLMs ,  RAG ,' (spaces before commas,
double spaces) so correct quotes were rejected and real skills were reported as gaps."""
from interview.evidence import resolve_quote, verify_extraction
from interview.schemas import ExtractionResult, Importance, JDSkill, OverallExperience
from interview.taxonomy import SkillTaxonomy
from interview.textutil import find_mention, quote_in_text, snap_quote

NOISY = (
    "PROFESSIONAL  EXPERIENCE\n"
    "\u25cf  Engineered  conversational  AI  systems  using  LangChain,  LlamaIndex,  and  Neo4j ,  enabling  contextual  retrieval  \n"
    "and\n \nreasoning\n \nover\n \nenterprise\n \nknowledge.\n"
    "TECHNICAL  SKILLS\n"
    "Generative  &  Agentic  AI:  LLMs ,  RAG ,  SFT ,  GRPO ,  LoRA ,  LangChain ,  Prompt  Engineering\n"
    "LLM  Infrastructure:  vLLM ,  Lit-GPT\n"
)


def test_quote_matches_despite_pdf_spacing_noise():
    assert quote_in_text("using LangChain, LlamaIndex, and Neo4j, enabling contextual retrieval and reasoning", NOISY)
    assert quote_in_text("Generative & Agentic AI: LLMs, RAG, SFT, GRPO, LoRA, LangChain, Prompt Engineering", NOISY)


def test_invented_quote_still_rejected():
    assert not quote_in_text("I have deep Kafka expertise and ran a 50-node cluster", NOISY)
    assert snap_quote("I have deep Kafka expertise and ran a 50-node cluster", NOISY) is None


def test_slightly_wrong_quote_snaps_to_the_real_line():
    wrong = "Engineered conversational AI systems using LangChain and Neo4j for contextual retrieval"
    real = resolve_quote(wrong, NOISY)
    assert real is not None and quote_in_text(real, NOISY)   # result is genuine source text


def test_mention_search_finds_listed_skill():
    assert "Lit-GPT" in find_mention(["lit-gpt"], NOISY)
    assert find_mention(["kubernetes", "k8s"], NOISY) is None
    assert find_mention(["lo"], NOISY) is None                # too short / not a whole word


def test_model_gives_no_evidence_but_resume_names_the_skill():
    jd = "Practical knowledge of LLM serving frameworks (vLLM, Lit-GPT) is required."
    ext = ExtractionResult(
        jd_skills=[
            JDSkill(skill_id="lit-gpt", name="Lit-GPT", importance=Importance.must_have,
                    required_experience="Not specified", jd_quote="Practical knowledge of LLM serving frameworks (vLLM, Lit-GPT)"),
            JDSkill(skill_id="kubernetes", name="Kubernetes", importance=Importance.must_have,
                    required_experience="Not specified", jd_quote="Practical knowledge of LLM serving frameworks"),
        ],
        evidence=[],
        overall_experience=OverallExperience(years=None, basis="unknown", quote=None),
    )
    v = verify_extraction(ext, jd, NOISY, SkillTaxonomy.from_yaml(), 15)
    assert v.evidence["litgpt"].listed_quote                  # id comes from the shared taxonomy; found by code search -> not a gap
    assert "kubernetes" not in v.evidence                      # really absent -> stays a gap
