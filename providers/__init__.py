"""Provider module registry.

Provider profiles can live in two places:

1. Bundled plugins: ``plugins/model-providers/<name>/`` (shipped with stoa-agent)
2. User plugins: ``$STOA_HOME/plugins/model-providers/<name>/``

Each plugin directory contains:
  - ``__init__.py`` — calls ``register_provider(profile)`` at import
  - ``plugin.yaml`` — manifest (name, kind: model-provider, version, description)

Discovery is lazy: the first call to ``get_provider_profile()`` or
``list_providers()`` scans both locations and imports every plugin. User
plugins override bundled plugins on name collision (last-writer-wins), so
third parties can monkey-patch or replace any built-in profile without
editing the repo.

For backward compatibility, ``providers/*.py`` files (other than ``base.py``
and ``__init__.py``) are still discovered via ``pkgutil.iter_modules``.
This lets out-of-tree users drop a single-file profile into an editable
install without the plugin dir structure. New profiles should prefer the
plugin layout.

Usage::

    from providers import get_provider_profile
    profile = get_provider_profile("nvidia")   # ProviderProfile or None
    profile = get_provider_profile("kimi")     # checks name + aliases
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from pathlib import Path

from providers.base import OMIT_TEMPERATURE, ProviderProfile  # noqa: F401

logger = logging.getLogger(__name__)

_REGISTRY: dict[str, ProviderProfile] = {}
_ALIASES: dict[str, str] = {}
_discovered = False

# Audit deep-2026-05-29 CF-14 (F-C09-003 / F-C16-003): names registered by
# BUNDLED (first-party, shipped) provider plugins. A later registration from a
# USER plugin under $STOA_HOME/plugins/model-providers/ must not silently
# override a trusted bundled provider — that would let a dropped-in plugin
# hijack a first-party provider's routing + the credentials sent to it.
_TRUSTED_PROVIDERS: set[str] = set()
# Source context of the registration currently in flight ("bundled" | "user").
# Set by _import_plugin_dir around exec_module; direct/programmatic calls
# default to "bundled" (trusted) since they originate from first-party code.
_registering_source: str = "bundled"

# Repo-root ``plugins/model-providers/`` — populated at discovery time.
_BUNDLED_PLUGINS_DIR = (
    Path(__file__).resolve().parent.parent / "plugins" / "model-providers"
)


def register_provider(profile: ProviderProfile, *, allow_override: bool = False) -> None:
    """Register a provider profile by name and aliases.

    Bundled (first-party) registrations are recorded as trusted. A USER plugin
    registration that collides with a trusted bundled name is refused unless
    ``allow_override=True`` is passed explicitly — closing the hijack where a
    dropped-in user plugin silently takes over a bundled provider's routing and
    the credentials sent to it (CF-14 / F-C09-003). New (non-colliding)
    providers from user plugins register normally.
    """
    is_bundled = _registering_source == "bundled"
    if (
        profile.name in _TRUSTED_PROVIDERS
        and not is_bundled
        and not allow_override
    ):
        logger.warning(
            "register_provider: refusing to let an untrusted (user-plugin) "
            "registration override trusted bundled provider %r. Pass "
            "allow_override=True only if this override is intended.",
            profile.name,
        )
        return
    _REGISTRY[profile.name] = profile
    if is_bundled:
        _TRUSTED_PROVIDERS.add(profile.name)
    for alias in profile.aliases:
        # Don't let a user plugin silently re-point a trusted provider's alias.
        if alias in _ALIASES and _ALIASES[alias] in _TRUSTED_PROVIDERS and not is_bundled and not allow_override:
            logger.warning(
                "register_provider: refusing to repoint trusted alias %r "
                "(currently -> %r) from a user plugin.", alias, _ALIASES[alias]
            )
            continue
        _ALIASES[alias] = profile.name


def get_provider_profile(name: str) -> ProviderProfile | None:
    """Look up a provider profile by name or alias.

    Returns None if the provider has no profile (falls back to generic).
    """
    if not _discovered:
        _discover_providers()
    canonical = _ALIASES.get(name, name)
    return _REGISTRY.get(canonical)


def list_providers() -> list[ProviderProfile]:
    """Return all registered provider profiles (one per canonical name)."""
    if not _discovered:
        _discover_providers()
    # Deduplicate: _REGISTRY has canonical names; _ALIASES points to same objects
    seen: set[int] = set()
    result: list[ProviderProfile] = []
    for profile in _REGISTRY.values():
        pid = id(profile)
        if pid not in seen:
            seen.add(pid)
            result.append(profile)
    return result


def _user_plugins_dir() -> Path | None:
    """Return ``$STOA_HOME/plugins/model-providers/`` if it exists."""
    try:
        from stoa_constants import get_stoa_home

        d = get_stoa_home() / "plugins" / "model-providers"
        return d if d.is_dir() else None
    except Exception:
        return None


def _import_plugin_dir(plugin_dir: Path, source: str) -> None:
    """Import a single plugin directory so it self-registers.

    ``source`` is "bundled" or "user", used only for log messages.
    """
    init_file = plugin_dir / "__init__.py"
    if not init_file.exists():
        return

    # Give bundled plugins a stable import path (``plugins.model_providers.<name>``)
    # so relative imports within the plugin work. User plugins load via
    # ``importlib.util.spec_from_file_location`` with a unique module name so
    # multiple STOA_HOME profiles don't alias each other.
    safe_name = plugin_dir.name.replace("-", "_")
    if source == "bundled":
        module_name = f"plugins.model_providers.{safe_name}"
    else:
        module_name = f"_stoa_user_provider_{safe_name}"

    if module_name in sys.modules:
        return  # already imported

    global _registering_source
    _prev_source = _registering_source
    try:
        spec = importlib.util.spec_from_file_location(
            module_name, init_file, submodule_search_locations=[str(plugin_dir)]
        )
        if spec is None or spec.loader is None:
            return
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        # CF-14: tag every register_provider() call made during this plugin's
        # import with its trust source so a "user" plugin can't override a
        # trusted "bundled" provider.
        _registering_source = source
        spec.loader.exec_module(module)
    except Exception as exc:
        logger.warning(
            "Failed to load %s provider plugin %s: %s", source, plugin_dir.name, exc
        )
        sys.modules.pop(module_name, None)
    finally:
        _registering_source = _prev_source


def _discover_providers() -> None:
    """Populate the registry by importing every provider plugin.

    Order:
      1. Bundled plugins at ``<repo>/plugins/model-providers/<name>/``
      2. User plugins at ``$STOA_HOME/plugins/model-providers/<name>/``
      3. Legacy per-file modules at ``providers/<name>.py`` (back-compat)

    Each step imports its plugins, which call ``register_provider()`` at
    module-level. Later steps win on name collision.
    """
    global _discovered
    if _discovered:
        return
    _discovered = True

    # 1. Bundled plugins — shipped with stoa-agent.
    if _BUNDLED_PLUGINS_DIR.is_dir():
        for child in sorted(_BUNDLED_PLUGINS_DIR.iterdir()):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            _import_plugin_dir(child, "bundled")

    # 2. User plugins — under $STOA_HOME/plugins/model-providers/<name>/.
    #    These can override any bundled profile of the same name (last-writer-wins
    #    in register_provider()).
    user_dir = _user_plugins_dir()
    if user_dir is not None:
        for child in sorted(user_dir.iterdir()):
            if not child.is_dir() or child.name.startswith(("_", ".")):
                continue
            _import_plugin_dir(child, "user")

    # 3. Legacy single-file profiles at providers/<name>.py. Kept for
    #    back-compat — if someone drops a ``providers/foo.py`` into an
    #    editable install, it still works without the plugin layout.
    try:
        import pkgutil

        import providers as _pkg

        for _importer, modname, _ispkg in pkgutil.iter_modules(_pkg.__path__):
            if modname.startswith("_") or modname == "base":
                continue
            try:
                importlib.import_module(f"providers.{modname}")
            except ImportError as exc:
                logger.warning(
                    "Failed to import legacy provider module %s: %s", modname, exc
                )
    except Exception:
        pass
