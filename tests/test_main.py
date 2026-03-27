from src.shared.runtime import load_runtime_config


def test_load_runtime_config_reads_only_optional_tokens():
    config = load_runtime_config(
        {
            "GITHUB_TOKEN": "gh_token",
            "HUGGINGFACE_TOKEN": "hf_token",
            "ALPHAXIV_TOKEN": "ax_token",
            "HF_EXACT_NO_REPO_THRESHOLD": "3",
        }
    )

    assert config == {
        "github_token": "gh_token",
        "huggingface_token": "hf_token",
        "alphaxiv_token": "ax_token",
        "hf_exact_no_repo_threshold": 3,
    }


def test_load_runtime_config_defaults_missing_values_to_empty_strings():
    assert load_runtime_config({}) == {
        "github_token": "",
        "huggingface_token": "",
        "alphaxiv_token": "",
        "hf_exact_no_repo_threshold": 10,
    }


def test_load_runtime_config_falls_back_to_default_threshold_for_invalid_value():
    assert load_runtime_config({"HF_EXACT_NO_REPO_THRESHOLD": "abc"})["hf_exact_no_repo_threshold"] == 10
