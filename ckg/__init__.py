"""code-kg: a clang + tree-sitter code knowledge graph builder.

Typical use::

    from ckg import ProjectConfig, KnowledgeGraph
    from ckg.pipeline import build_from_path

    build_from_path(r"C:/path/to/project")      # detects everything
"""

__version__ = "2.0.0"

from .config import ProjectConfig
from .graph_builder import KnowledgeGraph
from .pipeline import Pipeline, build_from_path

__all__ = [
    "ProjectConfig",
    "KnowledgeGraph",
    "Pipeline",
    "build_from_path",
    "__version__",
]
