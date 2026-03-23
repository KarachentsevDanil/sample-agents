import os

from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


def apply_openai_env() -> None:
    os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY


def configure_openai_agents_sdk() -> None:
    apply_openai_env()
    try:
        from agents import set_default_openai_key
    except ImportError:
        return
    set_default_openai_key(OPENAI_API_KEY)
