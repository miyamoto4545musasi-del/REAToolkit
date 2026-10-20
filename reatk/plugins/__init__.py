"""User plugins: the extension point for your own conversion features.

A plugin is a subclass of :class:`Plugin` exposed by any module under
``reatk.plugins.builtin`` or by a file passed to ``--plugin-dir``.  Each hook
is optional; the pipeline calls whichever ones a plugin defines.

Hooks, in pipeline order::

    on_rom(rom, ctx)         after parsing, before anything is written
    on_asset(entry, data, ctx) -> bytes | None   per NitroFS file
    on_manifest(manifest, ctx) -> dict           before the project is emitted
    on_project(project_dir, ctx)                 after files land on disk

``ctx`` is a :class:`PluginContext`: options from the command line plus a
scratch ``state`` dict that persists across hooks within one run.
"""

from __future__ import annotations

import dataclasses
import importlib
import importlib.util
import inspect
import pkgutil
import sys
from pathlib import Path
from typing import Any, Iterable


@dataclasses.dataclass
class PluginContext:
    """Everything a hook is allowed to rely on."""

    options: dict[str, Any] = dataclasses.field(default_factory=dict)
    state: dict[str, Any] = dataclasses.field(default_factory=dict)
    log: list[str] = dataclasses.field(default_factory=list)

    def note(self, message: str) -> None:
        """Record a line for the run summary."""
        self.log.append(message)

    def option(self, name: str, default: Any = None) -> Any:
        return self.options.get(name, default)


class Plugin:
    """Base class. Override ``name`` and at least one hook."""

    name = "unnamed"
    description = ""

    def on_rom(self, rom, ctx: PluginContext) -> None:  # pragma: no cover - interface
        return None

    def on_asset(self, entry, data: bytes, ctx: PluginContext) -> bytes | None:
        """Return replacement bytes, or ``None`` to leave the asset alone."""
        return None

    def on_manifest(self, manifest: dict, ctx: PluginContext) -> dict | None:
        """Return a replacement manifest, or ``None`` to keep it."""
        return None

    def on_project(self, project_dir: Path, ctx: PluginContext) -> None:
        return None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Plugin {self.name}>"


class PluginRegistry:
    """Holds the active plugins and fans each hook out to them in order."""

    def __init__(self, plugins: Iterable[Plugin] = ()):
        self.plugins: list[Plugin] = list(plugins)

    def add(self, plugin: Plugin) -> None:
        self.plugins.append(plugin)

    def names(self) -> list[str]:
        return [p.name for p in self.plugins]

    # -- hook dispatch ---------------------------------------------------

    def run_rom(self, rom, ctx: PluginContext) -> None:
        for plugin in self.plugins:
            plugin.on_rom(rom, ctx)

    def run_asset(self, entry, data: bytes, ctx: PluginContext) -> bytes:
        for plugin in self.plugins:
            replacement = plugin.on_asset(entry, data, ctx)
            if replacement is not None:
                data = replacement
        return data

    def run_manifest(self, manifest: dict, ctx: PluginContext) -> dict:
        for plugin in self.plugins:
            replacement = plugin.on_manifest(manifest, ctx)
            if replacement is not None:
                manifest = replacement
        return manifest

    def run_project(self, project_dir: Path, ctx: PluginContext) -> None:
        for plugin in self.plugins:
            plugin.on_project(project_dir, ctx)


def _plugins_in_module(module) -> list[Plugin]:
    found = []
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if issubclass(obj, Plugin) and obj is not Plugin and obj.__module__ == module.__name__:
            found.append(obj())
    return found


def discover(extra_dirs: Iterable[str | Path] = (), enabled: Iterable[str] | None = None) -> PluginRegistry:
    """Collect built-in plugins plus any found in *extra_dirs*.

    *enabled* filters by plugin name; ``None`` means "every one discovered".
    """
    from . import builtin

    found: list[Plugin] = []
    for info in pkgutil.iter_modules(builtin.__path__):
        module = importlib.import_module(f"{builtin.__name__}.{info.name}")
        found += _plugins_in_module(module)

    for directory in extra_dirs:
        directory = Path(directory)
        for path in sorted(directory.glob("*.py")):
            spec = importlib.util.spec_from_file_location(f"reatk_plugin_{path.stem}", path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            found += _plugins_in_module(module)

    if enabled is not None:
        wanted = set(enabled)
        found = [p for p in found if p.name in wanted]
    return PluginRegistry(found)


__all__ = ["Plugin", "PluginContext", "PluginRegistry", "discover"]
