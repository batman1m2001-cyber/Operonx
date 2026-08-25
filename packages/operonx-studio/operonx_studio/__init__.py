"""Visual workspace for operonx projects.

Reads the Project IR produced by ``operonx-project`` and renders it. The
toolkit stays headless so projects and CI can depend on it without a web
stack; everything visual lives here.
"""

from operonx_studio.layout import Layout, layout_graph
from operonx_studio.render import render_html, render_project

__version__ = "0.1.0"

__all__ = ["Layout", "layout_graph", "render_html", "render_project", "__version__"]
