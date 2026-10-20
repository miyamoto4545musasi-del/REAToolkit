"""REAToolkit - reverse-engineering and re-packaging kit for handheld ROM images.

The toolkit is split into four layers:

``reatk.formats``   parsers for container formats (NDS, GBA)
``reatk.analyze``   read-only inspection passes built on top of the parsers
``reatk.convert``   asset conversion (icons, titles, filesystem dumps)
``reatk.package``   emitters that wrap a title into an Android / iOS project
``reatk.plugins``   user-supplied transforms that run between the stages
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
