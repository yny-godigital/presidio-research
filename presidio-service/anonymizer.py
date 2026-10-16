"""Policy loading and token restore for the anonymization endpoints.

The policy file lives at the repository root (policy/policy.yaml) and is
shared with the eval harness, which resolves entity actions from the same
file. Both sides must agree or anonymization diverges silently.
"""

import logging
import os
import re

import yaml
from presidio_anonymizer.entities import OperatorConfig

DEFAULT_POLICY_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    os.pardir,
    "policy",
    "policy.yaml",
)

VALID_ACTIONS = ("pseudonymize", "block", "redact")


def policy_file_from_env(env) -> str:
    return env.get("POLICY_CONF_FILE", DEFAULT_POLICY_FILE)


def load_policy(conf_file) -> dict[str, str]:
    # Fail fast: serving without a policy would silently skip redact and block.
    if not os.path.isfile(conf_file):
        raise FileNotFoundError(f"Policy config not found: {conf_file}")
    with open(conf_file, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    entities = raw.get("entities") or {}
    policy = {}
    for entity_type, spec in entities.items():
        action = (spec or {}).get("action")
        if action not in VALID_ACTIONS:
            raise ValueError(
                f"Policy error: unknown action {action!r} for entity "
                f"{entity_type!r}; valid actions: {', '.join(VALID_ACTIONS)}"
            )
        policy[entity_type] = action
    return policy


def action_for(policy: dict, entity_type: str) -> str:
    # A newly added recognizer type has no policy entry yet; defaulting to
    # pseudonymize keeps it anonymized instead of leaking through untouched.
    return policy.get(entity_type, "pseudonymize")


logger = logging.getLogger("presidio-service")

# Matches service-issued tokens such as <PERSON_001>. The same shape the eval
# restorer (eval/restorer.ts) accepts, so either side can restore either text.
TOKEN_PATTERN = re.compile(r"<([A-Z][A-Z_]*_\d{3})>")


def restore_text(text: str, mapping) -> str:
    by_token = _validate_mapping(mapping)

    def replace(match):
        token = match.group(0)
        original = by_token.get(token)
        if original is None:
            # A token with no mapping entry is a placeholder, not PII.
            logger.warning("Unmapped token %s; leaving untouched", token)
            return token
        return original

    return TOKEN_PATTERN.sub(replace, text)


def _validate_mapping(mapping) -> dict:
    if not isinstance(mapping, list):
        raise ValueError("Field mapping must be a list")
    by_token = {}
    for entry in mapping:
        if not isinstance(entry, dict):
            raise ValueError("Each mapping entry must be an object")
        token = entry.get("token")
        entity_type = entry.get("entity_type")
        original = entry.get("original")
        if not all(isinstance(field, str) for field in (token, entity_type, original)):
            raise ValueError(
                "Mapping entries need string fields token, entity_type, original"
            )
        if token in by_token and by_token[token] != original:
            raise ValueError(f"Token {token} maps to two different originals")
        by_token[token] = original
    return by_token


class TokenCollector:
    """Per-request token state: counters, value dedupe, and the restore mapping."""

    def __init__(self):
        self._counters = {}
        self._by_value = {}
        self.mapping = []

    def token_for(self, entity_type: str, value: str) -> str:
        key = (entity_type, value)
        if key not in self._by_value:
            number = self._counters.get(entity_type, 0) + 1
            self._counters[entity_type] = number
            token = f"<{entity_type}_{number:03d}>"
            self._by_value[key] = token
            self.mapping.append(
                {"token": token, "entity_type": entity_type, "original": value}
            )
        return self._by_value[key]

    def sort_by_document_order(self, text: str) -> None:
        # The engine applies operators right-to-left, so tokens land in the
        # mapping in reverse document order. Stable-sort by each original's
        # first occurrence so the mapping reads in the same order as the text.
        self.mapping.sort(key=lambda entry: text.find(entry["original"]))


def _token_lambda(collector: TokenCollector, entity_type: str):
    # A factory, not a lambda written inline in the loop: the loop variable
    # would late-bind every operator to the last entity type processed.
    return lambda value: collector.token_for(entity_type, value)


def _mask_lambda(value: str) -> str:
    # Full-span masking: the built-in mask operator takes a fixed char count,
    # which cannot adapt to span length and would leak the tail of short values.
    return "*" * len(value)


def build_operators(
    entity_types, policy: dict, output_format: str, collector
) -> dict[str, OperatorConfig]:
    operators = {}
    for entity_type in sorted(entity_types):
        action = action_for(policy, entity_type)
        if action == "block":
            continue
        if action == "redact":
            # Literal marker so <TYPE_001> tokens only ever mean "reversible".
            operators[entity_type] = OperatorConfig(
                "replace", {"new_value": "[REDACTED]"}
            )
        elif output_format == "placeholder":
            # Empty params make the anonymizer engine substitute <ENTITY_TYPE>.
            operators[entity_type] = OperatorConfig("replace", {})
        elif output_format == "mask":
            operators[entity_type] = OperatorConfig("custom", {"lambda": _mask_lambda})
        else:
            operators[entity_type] = OperatorConfig(
                "custom", {"lambda": _token_lambda(collector, entity_type)}
            )
    return operators
