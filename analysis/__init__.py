"""
Módulo de análise do Projeto Córtex.

Camada cerebral do sistema — análise técnica quantitativa,
análise de sentimento NLP e motor de decisão autônomo.
"""

from analysis.decision import DecisionEngine
from analysis.sentiment import NewsItem, SentimentAnalyzer, SentimentResult
from analysis.technical import TechnicalAnalyzer, TechnicalResult, TrendSignal
from models.data_models import Action, Decision, Position

__all__ = [
    "TechnicalAnalyzer",
    "SentimentAnalyzer",
    "DecisionEngine",
    "TechnicalResult",
    "SentimentResult",
    "Decision",
    "Position",
    "TrendSignal",
    "Action",
    "NewsItem",
]
