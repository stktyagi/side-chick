import os
import platform
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from jinja2 import Environment as JinjaEnvironment
from jinja2 import StrictUndefined


@dataclass(frozen=True, slots=True, kw_only=True)
class SystemPromptArgs:
    OS_KIND: str
    SHELL_NAME: str
    WORK_DIR: str
    WORK_DIR_LS: str


def _load_system_prompt(path: Path, builtin_args: SystemPromptArgs) -> str:
    system_prompt = path.read_text(encoding="utf-8").strip()
    env = JinjaEnvironment(
        keep_trailing_newline=True,
        lstrip_blocks=True,
        trim_blocks=True,
        variable_start_string="${",
        variable_end_string="}",
        undefined=StrictUndefined,
    )
    try:
        template = env.from_string(system_prompt)
        return template.render(asdict(builtin_args))
    except Exception as e:
        raise RuntimeError(f"Failed to render system prompt template: {e}") from e


def load_system_prompt(work_dir: str) -> str:

    os_kind = platform.system()
    shell_name = os.getenv("SHELL", "bash")
    work_dir_ls = "\n".join(os.listdir(work_dir))

    return _load_system_prompt(
        path=Path(__file__).parent / "system.md",
        builtin_args=SystemPromptArgs(
            OS_KIND=os_kind,
            SHELL_NAME=shell_name,
            WORK_DIR=work_dir,
            WORK_DIR_LS=work_dir_ls,
        ),
    )


def get_final_answer(text: str) -> str:
    matches = list(re.finditer(r"<final_answer>(.*?)</final_answer>", text, re.DOTALL))
    if not matches:
        return text
    return matches[-1].group(0)


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").strip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_dotenv() -> None:
    """Load .env from AIDE_ENV, then cwd/.env, then ~/.config/aide/{.env,env}."""
    explicit = os.getenv("AIDE_ENV")
    if explicit:
        _load_env_file(Path(explicit))
        return
    _load_env_file(Path(".env"))
    cfg_dir = Path.home() / ".config" / "aide"
    _load_env_file(cfg_dir / ".env")
    _load_env_file(cfg_dir / "env")


if __name__ == "__main__":
    print(load_system_prompt(os.getcwd()))
