from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

# Источник правды — `pyproject.toml::version`, попадающий в wheel-METADATA при
# сборке. Жёстко зашитая константа раньше расходилась с релизными тегами после
# self-update и приводила к показу старой версии в UI.
try:
    __version__ = _pkg_version("waygate-agent")
except PackageNotFoundError:
    # Импорт из исходников без install (редко, в основном dev-edge): fallback,
    # чтобы не падать при импорте.
    __version__ = "0.0.0+source"
