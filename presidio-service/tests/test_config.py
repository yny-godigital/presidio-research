from config import load_config
from recognizer_registry import registry_file_from_env


def test_config_exposes_nlp_model_default():
    assert load_config()["nlp_model"] == "en_core_web_sm"


def test_registry_file_defaults_to_packaged_yaml_and_env_overrides():
    import recognizer_registry

    assert registry_file_from_env({}) == recognizer_registry.DEFAULT_REGISTRY_FILE
    assert (
        registry_file_from_env({"RECOGNIZER_REGISTRY_CONF_FILE": "/tmp/custom.yaml"})
        == "/tmp/custom.yaml"
    )
