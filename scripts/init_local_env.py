import os
from pathlib import Path
from secrets import token_urlsafe


def main() -> None:
    path = Path(__file__).resolve().parents[1] / ".env"
    keys = {
        "EAAF_GLOBAL_API_KEY": token_urlsafe(32),
        "EAAF_HR_API_KEY": token_urlsafe(32),
        "EAAF_IT_API_KEY": token_urlsafe(32),
        "EAAF_MARKETING_API_KEY": token_urlsafe(32),
        "LITELLM_MASTER_KEY": "sk-" + token_urlsafe(32),
        "EAAF_MOCK_API_KEY": "sk-" + token_urlsafe(32),
    }
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        with path.open("r+", encoding="utf-8") as env_file:
            existing = {
                line.partition("=")[0].strip().removeprefix("export ").strip()
                for line in env_file if "=" in line
            }
            if "EAAF_GLOBAL_API_KEY" not in existing:
                env_file.seek(0, os.SEEK_END)
                env_file.write(f"\nEAAF_GLOBAL_API_KEY={keys['EAAF_GLOBAL_API_KEY']}\n")
                print("Added the Global key to .env. Existing values were preserved.")
            else:
                print(".env already exists. Existing values were preserved.")
        return

    with os.fdopen(fd, "w", encoding="utf-8") as env_file:
        env_file.writelines(f"{name}={value}\n" for name, value in keys.items())
    print("Created .env with local keys.")


if __name__ == "__main__":
    main()
