"""Registry construction: Presidio provider file plus the app-code recognizers.

The provider owns every declarative recognizer. Two things it cannot supply
are added here instead: the gazetteer needs its phrase list at construction
time, and the NRIC/SSM recognizers need validate_result() structural checks
that no YAML pattern can perform. One function builds the whole registry so
tests and the server share the identical path.
"""

import os

import yaml
from presidio_analyzer.recognizer_registry import RecognizerRegistryProvider

from recognizers import (
    GazetteerRecognizer,
    MalaysianNricRecognizer,
    MalaysianSsmRecognizer,
)

DEFAULT_REGISTRY_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "recognizers.yaml"
)
DEFAULT_GAZETTEER_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "gazetteer.yaml"
)


def registry_file_from_env(env) -> str:
    return env.get("RECOGNIZER_REGISTRY_CONF_FILE", DEFAULT_REGISTRY_FILE)


def build_registry(
    conf_file: str = DEFAULT_REGISTRY_FILE, gazetteer_file: str = DEFAULT_GAZETTEER_FILE
):
    # The provider falls back silently on a missing file. Fail fast instead:
    # serving default recognizers with no company lists is a silent miss.
    for path in (conf_file, gazetteer_file):
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Recognizer config not found: {path}")
    provider = RecognizerRegistryProvider(conf_file=conf_file)
    registry = provider.create_recognizer_registry()
    with open(gazetteer_file, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    phrases = list(raw.get("company_names", [])) + list(raw.get("company_acronyms", []))
    if phrases:
        registry.add_recognizer(GazetteerRecognizer(phrases, entity="ORGANIZATION"))
    project_phrases = list(raw.get("project_names", []))
    if project_phrases:
        registry.add_recognizer(
            GazetteerRecognizer(project_phrases, entity="PROJECT_NAME")
        )
    # LOCATION is a SpacyRecognizer entity, but NER tags a place by context:
    # it reads "Ringgit Malaysia" as PERSON, so the phrase list guarantees
    # coverage the model cannot.
    location_phrases = list(raw.get("locations", []))
    if location_phrases:
        registry.add_recognizer(
            GazetteerRecognizer(location_phrases, entity="LOCATION")
        )
    registry.add_recognizer(MalaysianNricRecognizer())
    registry.add_recognizer(MalaysianSsmRecognizer())
    return registry
