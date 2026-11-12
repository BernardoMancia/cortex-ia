"""
Infraestrutura de dados do Projeto Córtex.

Gerenciamento de banco de dados, dados de mercado e scraping de notícias.
"""

from data.database import DatabaseManager
from data.market_data import MarketData
from data.news_scraper import NewsScraper, NewsItem

__all__ = ["DatabaseManager", "MarketData", "NewsScraper", "NewsItem"]
