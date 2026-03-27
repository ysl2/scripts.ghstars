import inspect

from src.shared.settings import HF_EXACT_NO_REPO_RECHECK_DAYS


def load_runtime_config(env: dict[str, str]) -> dict[str, str]:
    return {
        "github_token": (env.get("GITHUB_TOKEN") or "").strip(),
        "huggingface_token": (env.get("HUGGINGFACE_TOKEN") or "").strip(),
        "alphaxiv_token": (env.get("ALPHAXIV_TOKEN") or "").strip(),
        "hf_exact_no_repo_recheck_days": _parse_positive_int(
            env.get("HF_EXACT_NO_REPO_RECHECK_DAYS"),
            default=HF_EXACT_NO_REPO_RECHECK_DAYS,
        ),
    }


def build_client(factory, session, **kwargs):
    parameters = inspect.signature(factory).parameters.values()
    if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters):
        accepted_kwargs = kwargs
    else:
        accepted_names = {parameter.name for parameter in parameters}
        accepted_kwargs = {key: value for key, value in kwargs.items() if key in accepted_names}

    return factory(session, **accepted_kwargs)


def _parse_positive_int(raw_value, *, default: int) -> int:
    text = str(raw_value or "").strip()
    if not text:
        return default

    try:
        value = int(text)
    except ValueError:
        return default

    if value <= 0:
        return default
    return value
