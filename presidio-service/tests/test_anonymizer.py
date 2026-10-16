import pytest

from anonymizer import (
    DEFAULT_POLICY_FILE,
    TokenCollector,
    action_for,
    build_operators,
    load_policy,
    policy_file_from_env,
    restore_text,
)


def test_policy_file_env_override_wins():
    assert policy_file_from_env({"POLICY_CONF_FILE": "/tmp/p.yaml"}) == "/tmp/p.yaml"


def test_policy_file_default_points_at_repo_policy_file():
    # Local runs, tests, and the eval harness must read one shared file.
    assert DEFAULT_POLICY_FILE.endswith("policy/policy.yaml")


def test_load_policy_fails_fast_on_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_policy(tmp_path / "missing.yaml")


def test_load_policy_reads_all_three_actions(tmp_path):
    conf = tmp_path / "policy.yaml"
    conf.write_text(
        "entities:\n"
        "  PERSON:\n"
        "    action: pseudonymize\n"
        "  API_KEY:\n"
        "    action: block\n"
        "  SECRET:\n"
        "    action: redact\n"
    )
    assert load_policy(conf) == {
        "PERSON": "pseudonymize",
        "API_KEY": "block",
        "SECRET": "redact",
    }


def test_load_policy_rejects_unknown_action(tmp_path):
    conf = tmp_path / "policy.yaml"
    conf.write_text("entities:\n  PERSON:\n    action: scramble\n")
    with pytest.raises(ValueError, match="scramble"):
        load_policy(conf)


def test_action_for_defaults_to_pseudonymize():
    assert action_for({}, "PERSON") == "pseudonymize"
    assert action_for({"API_KEY": "block"}, "API_KEY") == "block"


def test_collector_numbers_per_type_and_dedupes_values():
    collector = TokenCollector()
    assert collector.token_for("PERSON", "John Tan") == "<PERSON_001>"
    assert collector.token_for("PERSON", "John Tan") == "<PERSON_001>"
    assert collector.token_for("PERSON", "Jane Doe") == "<PERSON_002>"
    assert collector.token_for("EMAIL_ADDRESS", "a@b.io") == "<EMAIL_ADDRESS_001>"
    assert collector.mapping == [
        {"token": "<PERSON_001>", "entity_type": "PERSON", "original": "John Tan"},
        {"token": "<PERSON_002>", "entity_type": "PERSON", "original": "Jane Doe"},
        {
            "token": "<EMAIL_ADDRESS_001>",
            "entity_type": "EMAIL_ADDRESS",
            "original": "a@b.io",
        },
    ]


def test_build_operators_token_lambda_numbers_each_type_independently():
    collector = TokenCollector()
    operators = build_operators({"PERSON", "EMAIL_ADDRESS"}, {}, "token", collector)
    assert operators["PERSON"].operator_name == "custom"
    # Separate counters per type: a shared counter would hand the email
    # <EMAIL_ADDRESS_002> just because a PERSON was replaced first.
    assert operators["PERSON"].params["lambda"]("John Tan") == "<PERSON_001>"
    assert operators["EMAIL_ADDRESS"].params["lambda"]("a@b.io") == (
        "<EMAIL_ADDRESS_001>"
    )
    assert operators["PERSON"].params["lambda"]("Jane Doe") == "<PERSON_002>"


def test_build_operators_redact_block_and_policy_default():
    policy = {"SECRET": "redact", "API_KEY": "block"}
    collector = TokenCollector()
    operators = build_operators(
        {"SECRET", "API_KEY", "PERSON"}, policy, "token", collector
    )
    assert operators["SECRET"].operator_name == "replace"
    assert operators["SECRET"].params == {"new_value": "[REDACTED]"}
    # block means "reject the request", so no operator ever applies to it.
    assert "API_KEY" not in operators
    # PERSON is absent from the policy and still gets a token operator.
    assert "PERSON" in operators
    assert collector.mapping == []


def test_collector_sorts_mapping_by_document_order():
    collector = TokenCollector()
    collector.mapping.extend(
        [
            {
                "token": "<EMAIL_ADDRESS_001>",
                "entity_type": "EMAIL_ADDRESS",
                "original": "a@b.io",
            },
            {
                "token": "<PERSON_001>",
                "entity_type": "PERSON",
                "original": "John Tan",
            },
        ]
    )
    collector.sort_by_document_order("John Tan paid a@b.io.")
    assert [entry["token"] for entry in collector.mapping] == [
        "<PERSON_001>",
        "<EMAIL_ADDRESS_001>",
    ]


def test_build_operators_placeholder_and_mask():
    collector = TokenCollector()
    placeholder = build_operators({"PERSON"}, {}, "placeholder", collector)
    assert placeholder["PERSON"].operator_name == "replace"
    assert placeholder["PERSON"].params == {}
    mask = build_operators({"PERSON"}, {}, "mask", collector)
    assert mask["PERSON"].operator_name == "custom"
    assert mask["PERSON"].params["lambda"]("John Tan") == "********"


def test_restore_returns_original_text():
    mapping = [
        {"token": "<PERSON_001>", "entity_type": "PERSON", "original": "John Tan"},
        {
            "token": "<EMAIL_ADDRESS_001>",
            "entity_type": "EMAIL_ADDRESS",
            "original": "john@example.com",
        },
    ]
    text = "<PERSON_001> paid <EMAIL_ADDRESS_001>."
    assert restore_text(text, mapping) == "John Tan paid john@example.com."


def test_restore_leaves_unmapped_token_untouched(caplog):
    with caplog.at_level("WARNING"):
        result = restore_text("keep <PERSON_009> as-is", [])
    assert result == "keep <PERSON_009> as-is"
    assert "PERSON_009" in caplog.text


def test_restore_rejects_conflicting_mapping():
    mapping = [
        {"token": "<PERSON_001>", "entity_type": "PERSON", "original": "John Tan"},
        {"token": "<PERSON_001>", "entity_type": "PERSON", "original": "Jane Doe"},
    ]
    with pytest.raises(ValueError, match="PERSON_001"):
        restore_text("<PERSON_001>", mapping)


def test_restore_rejects_malformed_mapping():
    with pytest.raises(ValueError):
        restore_text("<PERSON_001>", [{"token": "<PERSON_001>"}])
    with pytest.raises(ValueError):
        restore_text("<PERSON_001>", "not-a-list")
    with pytest.raises(ValueError):
        restore_text("<PERSON_001>", ["not-an-object"])
