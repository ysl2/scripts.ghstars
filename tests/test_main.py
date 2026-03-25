from shared.runtime import load_runtime_config


def test_load_runtime_config_reads_only_optional_tokens():
    config = load_runtime_config(
        {
            "GITHUB_TOKEN": "gh_token",
            "HUGGINGFACE_TOKEN": "hf_token",
            "ALPHAXIV_TOKEN": "ax_token",
        }
    )

    assert config == {
        "github_token": "gh_token",
        "huggingface_token": "hf_token",
        "alphaxiv_token": "ax_token",
    }


def test_load_runtime_config_defaults_missing_values_to_empty_strings():
    assert load_runtime_config({}) == {
        "github_token": "",
        "huggingface_token": "",
        "alphaxiv_token": "",
    }
