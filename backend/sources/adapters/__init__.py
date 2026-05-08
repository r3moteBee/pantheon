"""Built-in source adapters.

Importing this package registers every adapter listed below. Add
new adapters by dropping a file in this folder and adding the
import here.
"""
from sources.adapters import youtube  # noqa: F401  (registers YouTube adapters)
from sources.adapters import blog     # noqa: F401  (registers Blog adapters)
from sources.adapters import pdf      # noqa: F401  (registers PDF adapters)
from sources.adapters import web      # noqa: F401  (registers Web adapters)
from sources.adapters import forum    # noqa: F401  (registers Forum adapters)
from sources.adapters import podcast  # noqa: F401  (registers Podcast adapter)
from sources.adapters import github   # noqa: F401  (registers GitHub adapters)
from sources.adapters import cfr      # noqa: F401  (registers CFR adapters)
