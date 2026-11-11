"""Registry construction: Presidio provider file plus the app-code recognizers."""

import os
from pathlib import Path
import yaml
from presidio_analyzer.recognizer_registry import RecognizerRegistryProvider

from recognizers import (
    GazetteerRecognizer,
    MalaysianNricRecognizer,
    MalaysianSsmRecognizer,
)
from abbreviate import compile_and_split
from alias_evaluator import run_alias_discovery

DEFAULT_REGISTRY_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "recognizers.yaml"
)
DEFAULT_GAZETTEER_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "gazetteer.yaml"
)

DEFAULT_TEXT_DIR = os.getenv("DEMO_TEXT_DIR", "demo/inputs/text")

def registry_file_from_env(env) -> str:
    return env.get("RECOGNIZER_REGISTRY_CONF_FILE", DEFAULT_REGISTRY_FILE)


def build_registry(
    conf_file: str = DEFAULT_REGISTRY_FILE, gazetteer_file: str = DEFAULT_GAZETTEER_FILE, text_dir: str = DEFAULT_TEXT_DIR
):
    for path in (conf_file, gazetteer_file):
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Recognizer config not found: {path}")
    # Call LLM to find aliases
    run_alias_discovery(
        gazetteer_path=Path(gazetteer_file),
        text_dir=Path(text_dir),
    )
    # Auto-compile and synchronize both YAML files on boot
    compile_and_split(Path(gazetteer_file), Path(conf_file))

    # Presidio loads all declarative regex recognizers directly from recognizers.yaml
    provider = RecognizerRegistryProvider(conf_file=conf_file)
    registry = provider.create_recognizer_registry()

    #registry.load_predefined_recognizers()

    with open(gazetteer_file, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    # 1. Standard company names: case-insensitive matching with dynamic affix lookahead
    company_names = list(raw.get("company_names", []))
    if company_names:
        registry.add_recognizer(
            GazetteerRecognizer(
                company_names,
                entity="ORGANIZATION",
                case_sensitive=False,
                allow_suffix_expansion=True,
                allow_prefix_expansion=True,
            )
        )

    # 2. Acronyms (e.g. "AS", "GME", "PCI"): STRICT case-sensitive matching + suffix lookahead
    company_acronyms = list(raw.get("company_acronyms", []))
    if company_acronyms:
        registry.add_recognizer(
            GazetteerRecognizer(
                company_acronyms,
                entity="ORGANIZATION",
                case_sensitive=True,
                allow_suffix_expansion=True,
                allow_prefix_expansion=False,
            )
        )

    # 3. Project Names: case-insensitive phrase matching without affix lookahead
    project_phrases = list(raw.get("project_names", []))
    if project_phrases:
        registry.add_recognizer(
            GazetteerRecognizer(
                project_phrases,
                entity="PROJECT_NAME",
                case_sensitive=False,
                allow_suffix_expansion=False,
                allow_prefix_expansion=False,
            )
        )

    location_phrases = list(raw.get("locations", []))
    if location_phrases:
        registry.add_recognizer(
            GazetteerRecognizer(location_phrases, entity="LOCATION")
        )

    registry.add_recognizer(MalaysianNricRecognizer())
    registry.add_recognizer(MalaysianSsmRecognizer())
    return registry