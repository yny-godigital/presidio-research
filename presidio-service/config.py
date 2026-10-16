"""Load service defaults. Detection tuning lives in the registry YAML;
only the file location below is configurable, plus the NLP model name."""

ServiceConfig = dict

_DEFAULTS = {
    "nlp_model": "en_core_web_sm",
}


def load_config() -> ServiceConfig:
    return dict(_DEFAULTS)
