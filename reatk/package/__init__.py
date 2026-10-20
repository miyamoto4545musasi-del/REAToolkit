"""Emitters that turn a parsed title into a buildable mobile project."""

from .common import PackageOptions, build_manifest
from .android import emit_android
from .ios import emit_ios

__all__ = ["PackageOptions", "build_manifest", "emit_android", "emit_ios", "emit"]

_EMITTERS = {"apk": emit_android, "android": emit_android, "ipa": emit_ios, "ios": emit_ios}


def emit(target: str, rom, options, registry=None, ctx=None):
    """Emit *target* (``apk``/``android`` or ``ipa``/``ios``)."""
    try:
        emitter = _EMITTERS[target.lower()]
    except KeyError:
        raise ValueError(f"unknown target {target!r}; expected apk or ipa") from None
    return emitter(rom, options, registry=registry, ctx=ctx)
