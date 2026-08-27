"""
Módulo de análise do Projeto Córtex.

Camada cerebral do sistema — análise técnica quantitativa,
análise de sentimento NLP e motor de decisão autônomo.
"""

from analysis.technical import TechnicalAnalyzer, TechnicalResult, TrendSignal
from analysis.sentiment import SentimentAnalyzer, SentimentResult, NewsItem
from analysis.decision import DecisionEngine, Decision, Action

__all__ = [
    'TechnicalAnalyzer',
    'SentimentAnalyzer',
    'DecisionEngine',
    'TechnicalResult',
    'SentimentResult',
    'Decision',
    'TrendSignal',
    'Action',
    'NewsItem',
]
