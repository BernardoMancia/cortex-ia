"""
Módulo de configuração do Projeto Córtex.

Exporta a instância singleton de Settings para uso em todo o sistema.
"""

from config.settings import settings, Settings

__version__ = "2.5.0"
__all__ = ["settings", "Settings", "__version__"]
