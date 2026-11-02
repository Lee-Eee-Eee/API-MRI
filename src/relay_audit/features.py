"""Optional research dependencies are independent of the everyday workbench."""
from importlib.util import find_spec

def research_available() -> bool:
    return all(find_spec(name) is not None for name in ('inspect_ai','torch','model_equality_testing'))

def require_research() -> None:
    if not research_available():
        raise ValueError('此功能需要研究扩展。源码环境安装：pip install ".[research,ui]"；日常检查不需要这些依赖。')
