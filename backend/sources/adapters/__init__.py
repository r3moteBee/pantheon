"""Built-in source adapters.

Importing this package registers every adapter listed below. Add
new adapters by dropping a file in this folder and adding the
import here.
"""
from sources.adapters import youtube  # noqa: F401  (registers YouTube adapters)
from sources.adapters import blog     # noqa: F401  (registers Blog adapters)
from sources.adapters import pdf      # noqa: F401  (registers PDF adapters)
from sources.adapters import web      # noqa: F401  (registers Web adapters)
