from dataclasses import dataclass

from .config import Settings, load_settings
from .llm import LLMClient, MafLLMClient
from .store import JsonFileStore, NullStore
from .taxonomy import SkillTaxonomy


@dataclass
class Services:
    cfg: Settings
    llm: LLMClient
    taxonomy: SkillTaxonomy
    store: JsonFileStore | NullStore


def build_services(
    cfg: Settings | None = None,
    llm: LLMClient | None = None,
    store: JsonFileStore | NullStore | None = None,
    save: bool = True,
) -> Services:
    """save=False -> nothing is written to output/scaffolds (the result is only returned)."""
    cfg = cfg or load_settings()
    if store is None:
        store = JsonFileStore(cfg.storage.output_dir) if save else NullStore()
    return Services(
        cfg=cfg,
        llm=llm or MafLLMClient(cfg.llm),
        taxonomy=SkillTaxonomy.from_yaml(),
        store=store,
    )
