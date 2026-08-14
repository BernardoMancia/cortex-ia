"""
Motor de análise de sentimento do Projeto Córtex.

Analisa sentimento de notícias financeiras em português usando
dois modos: 'lightweight' (léxico de palavras-chave) e 'full'
(FinBERT-PT-BR via HuggingFace Transformers).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional, Protocol, runtime_checkable
import json
import time

from utils.logger import get_logger
from utils.helpers import clamp
from config.settings import settings

try:
    from google import genai as google_genai
    from google.genai import types as genai_types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

logger = get_logger('analysis.sentiment')

# Timezone BRT (UTC-3)
BRT = timezone(timedelta(hours=-3))


# ─── Protocolo para itens de notícia ────────────────────────────────────────


@runtime_checkable
class NewsItem(Protocol):
    """
    Protocolo para itens de notícia consumidos pelo analisador.

    Qualquer objeto com estas propriedades pode ser usado
    como entrada para análise de sentimento.
    """

    @property
    def title(self) -> str: ...

    @property
    def summary(self) -> str: ...

    @property
    def published_at(self) -> datetime: ...

    @property
    def source(self) -> str: ...


# ─── Dataclasses ─────────────────────────────────────────────────────────────


@dataclass
class SentimentResult:
    """Resultado consolidado da análise de sentimento para um ativo."""

    score: float        # -1.0 a 1.0
    label: str          # 'POSITIVO', 'NEGATIVO', 'NEUTRO'
    confidence: float   # 0.0 a 1.0
    news_count: int
    top_headline: str
    reasoning: str      # Explicação em português


# ─── Léxico Financeiro em Português ─────────────────────────────────────────


POSITIVE_KEYWORDS: dict[str, float] = {
    # Multi-word (testados primeiro para match correto)
    'resultado positivo': 0.9,
    'acima do esperado': 0.8,
    'recomendação de compra': 0.9,
    'perspectiva positiva': 0.8,
    'recompra de ações': 0.8,
    'guidance positivo': 0.8,
    'destravar valor': 0.7,
    'geração de caixa': 0.6,
    'margem operacional': 0.5,
    'revisão para cima': 0.7,
    # Single-word
    'lucro': 0.8,
    'alta': 0.6,
    'crescimento': 0.7,
    'recorde': 0.9,
    'dividendo': 0.7,
    'valorização': 0.8,
    'superou': 0.6,
    'otimismo': 0.7,
    'recuperação': 0.6,
    'expansão': 0.7,
    'positivo': 0.6,
    'ganho': 0.7,
    'aprovação': 0.5,
    'investimento': 0.4,
    'demanda': 0.5,
    'produção': 0.4,
    'upgrade': 0.7,
    'margem': 0.4,
    'eficiência': 0.5,
    'recompra': 0.7,
    'buyback': 0.7,
    'aquisição': 0.5,
    'outperform': 0.7,
    'breakeven': 0.4,
    'sustentável': 0.4,
    'resiliência': 0.5,
    'desalavancagem': 0.6,
}

NEGATIVE_KEYWORDS: dict[str, float] = {
    # Multi-word (testados primeiro para match correto)
    'resultado negativo': -0.9,
    'abaixo do esperado': -0.8,
    'recomendação de venda': -0.9,
    'revisão para baixo': -0.7,
    'queima de caixa': -0.7,
    'recuperação judicial': -0.9,
    'oferta subsequente': -0.4,
    'aumento de capital': -0.3,
    # Single-word
    'prejuízo': -0.8,
    'queda': -0.6,
    'crise': -0.9,
    'perda': -0.7,
    'desvalorização': -0.8,
    'rebaixamento': -0.7,
    'pessimismo': -0.7,
    'recessão': -0.9,
    'inflação': -0.5,
    'dívida': -0.5,
    'negativo': -0.6,
    'falência': -1.0,
    'demissão': -0.6,
    'multa': -0.6,
    'investigação': -0.5,
    'downgrade': -0.7,
    'risco': -0.4,
    'volatilidade': -0.3,
    'incerteza': -0.4,
    'diluição': -0.6,
    'follow-on': -0.4,
    'inadimplência': -0.7,
    'alavancagem': -0.4,
    'sell-off': -0.8,
    'suspensão': -0.6,
    'default': -0.9,
    'impairment': -0.7,
    'provisão': -0.5,
    'underperform': -0.7,
}


# ─── Motor de Análise de Sentimento ─────────────────────────────────────────


class SentimentAnalyzer:
    """
    Motor de análise de sentimento do Córtex.

    Opera em dois modos:
        - 'lightweight' (padrão): Análise baseada em léxico de palavras-chave
          financeiras em português. Rápido, sem dependências pesadas.
        - 'full': Utiliza o modelo FinBERT-PT-BR via HuggingFace Transformers.
          Requer instalação de transformers e torch.

    O modelo FinBERT é carregado de forma lazy (apenas no primeiro uso)
    para minimizar consumo de memória quando não necessário.
    """

    # Taxa de decaimento exponencial para ponderação por recência
    RECENCY_DECAY: float = 0.05

    def __init__(self, mode: str = 'lightweight') -> None:
        """
        Inicializa o analisador de sentimento.

        Args:
            mode: Modo de operação — 'lightweight' (léxico) ou 'full' (FinBERT).

        Raises:
            ValueError: Se o modo informado não for válido.
        """
        valid_modes = ('lightweight', 'full', 'gemini')
        if mode not in valid_modes:
            raise ValueError(
                f"Modo inválido '{mode}'. Modos disponíveis: {valid_modes}"
            )

        self.mode = mode
        self._pipeline = None  # Lazy loading para FinBERT
        self._cache: dict[str, tuple[SentimentResult, float]] = {}
        self.cache_ttl: float = float(getattr(settings, 'SENTIMENT_CACHE_TTL', 1800))
        self._last_gemini_call: float = 0.0
        
        # Init Gemini Se disponível e selecionado
        if self.mode == 'gemini':
            if not GENAI_AVAILABLE:
                logger.warning("Modo gemini selecionado, mas 'google-genai' não está instalado. Fallback para lightweight.")
                self.mode = 'lightweight'
            elif not settings.GEMINI_API_KEY:
                logger.warning('Modo gemini selecionado, mas GEMINI_API_KEY não encontrada. Fallback para lightweight.')
                self.mode = 'lightweight'
            else:
                self._genai_client = google_genai.Client(api_key=settings.GEMINI_API_KEY)

        logger.info('SentimentAnalyzer inicializado — modo: %s (cache TTL: %ds)', self.mode, int(self.cache_ttl))

    def _load_finbert(self) -> None:
        """
        Carrega o pipeline FinBERT-PT-BR de forma lazy.

        Importa transformers apenas quando necessário para evitar
        carregar torch/transformers no modo lightweight.

        Raises:
            ImportError: Se transformers não estiver instalado.
        """
        if self._pipeline is not None:
            return

        logger.info('Carregando modelo FinBERT-PT-BR...')
        try:
            from transformers import pipeline as hf_pipeline  # type: ignore[import-untyped]
            self._pipeline = hf_pipeline(
                'text-classification',
                model='lucas-leme/FinBERT-PT-BR',
            )
            logger.info('FinBERT-PT-BR carregado com sucesso.')
        except ImportError:
            logger.error(
                'Pacotes transformers/torch não instalados. '
                'Instale com: pip install transformers torch'
            )
            raise ImportError(
                "Para usar o modo 'full', instale: pip install transformers torch"
            )
        except Exception as exc:
            logger.error('Erro ao carregar FinBERT-PT-BR: %s', exc)
            raise

    def analyze_text(self, text: str) -> float:
        """
        Analisa o sentimento de um texto individual.

        Despacha para o método apropriado conforme o modo configurado.

        Args:
            text: Texto a ser analisado.

        Returns:
            Score de sentimento em [-1.0, 1.0].
            Positivo = otimista, Negativo = pessimista, 0.0 = neutro.
        """
        if not text or not text.strip():
            return 0.0

        if self.mode == 'full':
            return self._analyze_text_finbert(text)
        return self._analyze_text_lightweight(text)

    def _analyze_text_finbert(self, text: str) -> float:
        """
        Analisa sentimento usando FinBERT-PT-BR.

        Mapeia os labels do modelo para scores numéricos:
            - POSITIVE → +confidence
            - NEGATIVE → -confidence
            - NEUTRAL  → 0.0

        Args:
            text: Texto a ser analisado.

        Returns:
            Score em [-1.0, 1.0].
        """
        self._load_finbert()

        try:
            # Trunca texto para limite do modelo (512 tokens ~= 2000 chars)
            truncated = text[:2000]
            result = self._pipeline(truncated)  # type: ignore[misc]

            if not result:
                return 0.0

            prediction = result[0]
            label = prediction['label'].upper()
            confidence = float(prediction['score'])

            if label == 'POSITIVE':
                return confidence
            elif label == 'NEGATIVE':
                return -confidence
            else:
                return 0.0

        except Exception as exc:
            logger.error('Erro na análise FinBERT: %s', exc)
            return 0.0

    def _analyze_text_lightweight(self, text: str, is_title: bool = False) -> float:
        """
        Analisa sentimento usando léxico de palavras-chave financeiras.

        Percorre o texto buscando ocorrências de termos do léxico.
        Expressões multi-palavra são verificadas primeiro para evitar
        contagem dupla. Palavras no título têm peso dobrado.

        Args:
            text: Texto a ser analisado.
            is_title: Se True, aplica peso dobrado (usado internamente).

        Returns:
            Score normalizado em [-1.0, 1.0].
        """
        if not text or not text.strip():
            return 0.0

        text_lower = text.lower()
        total_score = 0.0
        match_count = 0
        weight_multiplier = 2.0 if is_title else 1.0

        # Combinar ambos os léxicos, ordenando expressões multi-palavra primeiro
        # para evitar que 'resultado positivo' seja contado como apenas 'positivo'
        all_keywords: dict[str, float] = {}
        all_keywords.update(POSITIVE_KEYWORDS)
        all_keywords.update(NEGATIVE_KEYWORDS)

        # Ordenar por tamanho decrescente (multi-palavra primeiro)
        sorted_keywords = sorted(all_keywords.keys(), key=len, reverse=True)

        # Texto de trabalho — remove termos já contabilizados
        remaining_text = text_lower

        for keyword in sorted_keywords:
            count = remaining_text.count(keyword)
            if count > 0:
                score = all_keywords[keyword]
                # Peso decresce com repetições: 1ª = 100%, 2ª = 50%, 3ª = 33%...
                weighted = sum(score / (i + 1) for i in range(count))
                total_score += weighted * weight_multiplier
                match_count += count
                # Remove termos encontrados para evitar contagem dupla
                remaining_text = remaining_text.replace(keyword, ' ')

        if match_count == 0:
            return 0.0

        # Normalizar: divide pelo total de matches para manter na escala
        # e aplica clamp para garantir [-1.0, 1.0]
        normalized = total_score / match_count
        return clamp(normalized, -1.0, 1.0)

    def analyze_news_batch(self, news_items: list[NewsItem]) -> float:
        """
        Analisa sentimento de uma lista de notícias com ponderação por recência.

        Notícias mais recentes recebem peso maior usando decaimento
        exponencial: weight_i = exp(-decay * hours_since_publish).

        Args:
            news_items: Lista de itens de notícia (devem seguir o protocolo NewsItem).

        Returns:
            Score médio ponderado em [-1.0, 1.0].
        """
        if not news_items:
            logger.debug('Nenhuma notícia para análise de sentimento em lote.')
            return 0.0

        now = datetime.now(BRT)
        weighted_sum = 0.0
        total_weight = 0.0

        for item in news_items:
            try:
                # Calcular score do texto (título tem peso dobrado)
                title_score = self._analyze_single_text(item.title, is_title=True)
                body_score = self._analyze_single_text(item.summary, is_title=False)

                # Score combinado: título contribui 60%, corpo 40%
                combined_score = title_score * 0.6 + body_score * 0.4

                # Peso por recência (decaimento exponencial)
                published = item.published_at
                if published.tzinfo is None:
                    published = published.replace(tzinfo=BRT)

                hours_since = max(0.0, (now - published).total_seconds() / 3600.0)
                weight = math.exp(-self.RECENCY_DECAY * hours_since)

                weighted_sum += combined_score * weight
                total_weight += weight

            except Exception as exc:
                logger.warning(
                    'Erro ao analisar notícia "%s": %s',
                    getattr(item, 'title', 'N/A')[:50], exc,
                )
                continue

        if total_weight == 0.0:
            return 0.0

        result = weighted_sum / total_weight
        logger.debug(
            'Sentimento em lote: %.3f (média ponderada de %d notícias)',
            result, len(news_items),
        )
        return clamp(result, -1.0, 1.0)

    def _analyze_single_text(self, text: str, is_title: bool = False) -> float:
        """
        Analisa um único texto, despachando para o modo correto.

        No modo 'lightweight', passa o flag is_title para duplicar peso.
        No modo 'full', o FinBERT já pondera internamente.

        Args:
            text: Texto a analisar.
            is_title: Se True e modo lightweight, aplica peso dobrado.

        Returns:
            Score em [-1.0, 1.0].
        """
        if not text or not text.strip():
            return 0.0

        if self.mode == 'full':
            return self._analyze_text_finbert(text)
        return self._analyze_text_lightweight(text, is_title=is_title)

    def _get_sentiment_local(
        self, ticker: str, news_items: list[NewsItem]
    ) -> SentimentResult:
        """
        Calcula sentimento usando o analisador léxico/FinBERT local sobre notícias locais.
        """
        if not news_items:
            return SentimentResult(
                score=0.0,
                label='NEUTRO',
                confidence=0.0,
                news_count=0,
                top_headline='Sem notícias disponíveis',
                reasoning=f'Nenhuma notícia local encontrada para {ticker}. Sentimento neutro.',
            )

        batch_score = self.analyze_news_batch(news_items)
        label = self._score_to_label(batch_score)
        confidence = self._calculate_confidence(batch_score, len(news_items))
        sorted_news = sorted(
            news_items,
            key=lambda n: n.published_at,
            reverse=True,
        )
        top_headline = sorted_news[0].title if sorted_news else 'N/A'
        reasoning = self._generate_reasoning(
            ticker, batch_score, label, confidence, len(news_items), top_headline
        )
        return SentimentResult(
            score=round(batch_score, 4),
            label=label,
            confidence=round(confidence, 4),
            news_count=len(news_items),
            top_headline=top_headline,
            reasoning=reasoning,
        )

    def get_sentiment_for_ticker(
        self,
        ticker: str,
        news_items: list[NewsItem],
        force_refresh: bool = False,
        allow_gemini: bool = True,
    ) -> SentimentResult:
        """
        Gera resultado consolidado de sentimento para um ativo específico.

        Utiliza cache em memória com TTL (padrão 30 minutos) para evitar requisições
        excessivas às APIs e respeitar os limites de cota gratuita (15 RPM / 1500 RPD).

        Args:
            ticker: Código do ativo (ex: 'PETR4').
            news_items: Lista de notícias locais do scraper.
            force_refresh: Se True, ignora o cache e recalcula.
            allow_gemini: Se False, usa analisador local quando não houver cache válido.

        Returns:
            SentimentResult com score, label, confiança e raciocínio.
        """
        now_ts = time.time()

        # ── 1. Verificar Cache Válido ─────────────────────────────────
        if not force_refresh and ticker in self._cache:
            cached_result, cached_time = self._cache[ticker]
            elapsed = now_ts - cached_time
            if elapsed < self.cache_ttl:
                remaining_min = int((self.cache_ttl - elapsed) / 60)
                logger.debug(
                    'Sentimento %s: usando CACHE (restam %d min) — score=%.3f (%s)',
                    ticker, remaining_min, cached_result.score, cached_result.label,
                )
                return cached_result

        # ── 2. Análise via Gemini (com Grounding e Rate Limiter) ──────
        if self.mode == 'gemini' and GENAI_AVAILABLE and allow_gemini:
            # Rate Limiter: garantir pelo menos 4s entre chamadas consecutivas ao Gemini (máx 15 RPM)
            elapsed_gemini = now_ts - self._last_gemini_call
            if elapsed_gemini < 4.0:
                sleep_time = 4.0 - elapsed_gemini
                logger.debug('Rate limiter Gemini: aguardando %.2fs...', sleep_time)
                time.sleep(sleep_time)

            self._last_gemini_call = time.time()
            result = self._analyze_gemini(ticker, news_items)
        else:
            result = self._get_sentiment_local(ticker, news_items)

        # ── 3. Salvar no Cache ────────────────────────────────────────
        self._cache[ticker] = (result, time.time())

        logger.info(
            'Sentimento %s (%s): score=%.3f (%s), confiança=%.2f, %d notícias',
            ticker, self.mode, result.score, result.label, result.confidence, result.news_count,
        )
        return result

    def clear_cache(self, ticker: Optional[str] = None) -> None:
        """Limpa o cache de sentimento de um ativo específico ou de todos."""
        if ticker:
            self._cache.pop(ticker, None)
            logger.debug('Cache de sentimento limpo para %s', ticker)
        else:
            self._cache.clear()
            logger.debug('Cache de sentimento limpo para todos os ativos')

    @staticmethod
    def _score_to_label(score: float) -> str:
        """
        Converte score numérico em label textual.

        Args:
            score: Score de sentimento em [-1.0, 1.0].

        Returns:
            'POSITIVO', 'NEGATIVO' ou 'NEUTRO'.
        """
        if score >= 0.15:
            return 'POSITIVO'
        elif score <= -0.15:
            return 'NEGATIVO'
        return 'NEUTRO'

    @staticmethod
    def _calculate_confidence(score: float, news_count: int) -> float:
        """
        Calcula confiança na análise de sentimento.

        A confiança aumenta com:
            - Magnitude do score (sentimento forte)
            - Quantidade de notícias (mais dados = mais confiável)

        Args:
            score: Score de sentimento.
            news_count: Quantidade de notícias analisadas.

        Returns:
            Confiança entre 0.0 e 1.0.
        """
        # Componente de magnitude: score absoluto (0.0 a 1.0)
        magnitude_component = abs(score)

        # Componente de volume: logarítmica, satura em ~16 notícias
        # log2(1) = 0, log2(2) = 1, log2(8) = 3, log2(16) = 4
        volume_component = min(1.0, math.log2(max(1, news_count)) / 4.0)

        # Confiança combinada com pesos
        if magnitude_component == 0.0 and volume_component == 0.0:
            return 0.0

        confidence = magnitude_component * 0.6 + volume_component * 0.4
        return clamp(confidence, 0.0, 1.0)

    @staticmethod
    def _generate_reasoning(
        ticker: str,
        score: float,
        label: str,
        confidence: float,
        news_count: int,
        top_headline: str,
    ) -> str:
        """
        Gera texto explicativo em português sobre a análise de sentimento.

        Args:
            ticker: Código do ativo.
            score: Score consolidado.
            label: Label textual.
            confidence: Nível de confiança.
            news_count: Quantidade de notícias.
            top_headline: Manchete mais recente.

        Returns:
            Texto explicativo em português.
        """
        parts: list[str] = []

        # Descrição do sentimento
        score_fmt = f'{score:+.2f}'.replace('.', ',')

        if label == 'POSITIVO':
            parts.append(
                f'Sentimento de mercado para {ticker}: {score_fmt} (otimista).'
            )
        elif label == 'NEGATIVO':
            parts.append(
                f'Sentimento de mercado para {ticker}: {score_fmt} (pessimista).'
            )
        else:
            parts.append(
                f'Sentimento de mercado para {ticker}: {score_fmt} (neutro).'
            )

        # Volume de notícias
        if news_count == 1:
            parts.append('Baseado em 1 notícia recente.')
        else:
            parts.append(f'Baseado em {news_count} notícias recentes.')

        # Confiança
        if confidence >= 0.7:
            parts.append('Alta confiança na análise.')
        elif confidence >= 0.4:
            parts.append('Confiança moderada na análise.')
        else:
            parts.append('Baixa confiança — poucas notícias ou sentimento fraco.')

        # Manchete principal
        headline_truncated = (
            top_headline[:80] + '...' if len(top_headline) > 80 else top_headline
        )
        parts.append(f"Última notícia relevante: '{headline_truncated}'.")

        return ' '.join(parts)

    def _analyze_gemini(
        self, ticker: str, news_items: list[NewsItem]
    ) -> SentimentResult:
        """
        Analisa o sentimento usando o modelo do Google Gemini com Web Search Grounding.
        Em caso de falha, faz fallback para análise local (lightweight).
        """
        import re
        logger.info('Solicitando análise Gemini para %s com busca Web', ticker)
        
        try:
            # Formata notícias locais se existirem
            local_news_context = ""
            if news_items:
                local_news_context = "Notícias locais coletadas previamente:\n"
                for n in news_items[:5]:
                    local_news_context += f"- {n.title} (Fonte: {n.source})\n"

            prompt = f"""Você é o Córtex, um robô de investimentos autônomo operando na bolsa de valores brasileira (B3).
Sua tarefa é analisar o sentimento de mercado atual e as perspectivas de curto prazo para a ação {ticker}.

Use sua ferramenta de busca para pesquisar as notícias financeiras mais recentes, relatórios corporativos, contexto macroeconômico, resultados de balanços, ou eventos relevantes sobre a empresa "{ticker}" ou o seu setor no mercado brasileiro hoje.

{local_news_context}

Com base na sua pesquisa e no contexto atual do mercado brasileiro, responda SOMENTE com um JSON válido neste formato exato (sem texto adicional antes ou depois):
{{{{
  "score": <float entre -1.0 e 1.0>,
  "confidence": <float entre 0.0 e 1.0>,
  "reasoning": "<breve parágrafo em português explicando a decisão>",
  "top_headline": "<a manchete mais importante encontrada>"
}}}}"""
            
            response = self._genai_client.models.generate_content(
                model="gemini-flash-latest",
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    tools=[
                        genai_types.Tool(
                            google_search=genai_types.GoogleSearch()
                        )
                    ],
                ),
            )
            text_response = response.text
            
            # Extrai JSON com regex para robustez
            json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text_response, re.DOTALL)
            if not json_match:
                raise ValueError(f'Resposta do Gemini não contém JSON válido: {text_response[:200]}')
            
            data = json.loads(json_match.group())
            
            try:
                score = clamp(float(data.get('score', 0.0)), -1.0, 1.0)
            except (TypeError, ValueError):
                score = 0.0
            
            try:
                confidence = clamp(float(data.get('confidence', 0.0)), 0.0, 1.0)
            except (TypeError, ValueError):
                confidence = 0.0
            
            label = self._score_to_label(score)
            reasoning = str(data.get('reasoning', f'O modelo analisou {ticker} mas não retornou justificativa.'))
            top_headline = str(data.get('top_headline', 'Manchete não especificada pelo modelo.'))
            
            logger.info('Gemini Score para %s: %.3f (Conf: %.2f) - %s', ticker, score, confidence, top_headline)
            
            return SentimentResult(
                score=score,
                label=label,
                confidence=confidence,
                news_count=len(news_items) + 5,
                top_headline=top_headline,
                reasoning=reasoning
            )
            
        except Exception as e:
            logger.error('Erro na análise Gemini para %s: %s', ticker, e)
            logger.warning('Fallback: Usando analisador local')
            return self._get_sentiment_local(ticker, news_items)
