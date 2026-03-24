def load_config_from_env(env: dict[str, str]) -> dict[str, str]:
    notion_token = (env.get("NOTION_TOKEN") or "").strip()
    github_token = (env.get("GITHUB_TOKEN") or "").strip()
    alphaxiv_token = (env.get("ALPHAXIV_TOKEN") or "").strip()
    huggingface_token = (env.get("HUGGINGFACE_TOKEN") or "").strip()
    database_id = (env.get("DATABASE_ID") or "").strip()

    missing = []
    if not notion_token:
        missing.append("NOTION_TOKEN")
    if not database_id:
        missing.append("DATABASE_ID")

    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"Missing required environment variables: {joined}")

    return {
        "notion_token": notion_token,
        "github_token": github_token,
        "alphaxiv_token": alphaxiv_token,
        "huggingface_token": huggingface_token,
        "database_id": database_id,
    }
