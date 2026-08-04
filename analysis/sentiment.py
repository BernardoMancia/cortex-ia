"""
Motor de análise de sentimento do Projeto Córtex.

Analisa sentimento de notícias financeiras em português usando
dois modos: 'lightweight' (léxico de palavras-chave) e 'full'
(FinBERT-PT-BR via HuggingFace Transformers).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime ,timezone ,timedelta
from typing import Optional ,Protocol ,runtime_checkable

from utils .logger import get_logger
from utils .helpers import clamp

logger =get_logger ('analysis.sentiment')

BRT =timezone (timedelta (hours =-3 ))

@runtime_checkable
class NewsItem (Protocol ):
    """
    Protocolo para itens de notícia consumidos pelo analisador.

    Qualquer objeto com estas propriedades pode ser usado
    como entrada para análise de sentimento.
    """

    @property
    def title (self )->str :...

    @property
    def summary (self )->str :...

    @property
    def published_at (self )->datetime :...

    @property
    def source (self )->str :...

@dataclass
class SentimentResult :
    """Resultado consolidado da análise de sentimento para um ativo."""

    score :float
    label :str
    confidence :float
    news_count :int
    top_headline :str
    reasoning :str

POSITIVE_KEYWORDS :dict [str ,float ]={
'lucro':0.8 ,
'alta':0.6 ,
'crescimento':0.7 ,
'recorde':0.9 ,
'dividendo':0.7 ,
'valorização':0.8 ,
'superou':0.6 ,
'otimismo':0.7 ,
'recuperação':0.6 ,
'expansão':0.7 ,
'positivo':0.6 ,
'ganho':0.7 ,
'aprovação':0.5 ,
'investimento':0.4 ,
'demanda':0.5 ,
'produção':0.4 ,
'resultado positivo':0.9 ,
'acima do esperado':0.8 ,
'recomendação de compra':0.9 ,
'upgrade':0.7 ,
'perspectiva positiva':0.8 ,
'margem':0.4 ,
'eficiência':0.5 ,
}

NEGATIVE_KEYWORDS :dict [str ,float ]={
'prejuízo':-0.8 ,
'queda':-0.6 ,
'crise':-0.9 ,
'perda':-0.7 ,
'desvalorização':-0.8 ,
'rebaixamento':-0.7 ,
'pessimismo':-0.7 ,
'recessão':-0.9 ,
'inflação':-0.5 ,
'dívida':-0.5 ,
'negativo':-0.6 ,
'falência':-1.0 ,
'demissão':-0.6 ,
'multa':-0.6 ,
'investigação':-0.5 ,
'resultado negativo':-0.9 ,
'abaixo do esperado':-0.8 ,
'recomendação de venda':-0.9 ,
'downgrade':-0.7 ,
'risco':-0.4 ,
'volatilidade':-0.3 ,
'incerteza':-0.4 ,
}

class SentimentAnalyzer :
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

    RECENCY_DECAY :float =0.05

    def __init__ (self ,mode :str ='lightweight')->None :
        """
        Inicializa o analisador de sentimento.

        Args:
            mode: Modo de operação — 'lightweight' (léxico) ou 'full' (FinBERT).

        Raises:
            ValueError: Se o modo informado não for válido.
        """
        valid_modes =('lightweight','full')
        if mode not in valid_modes :
            raise ValueError (
            f"Modo inválido '{mode }'. Modos disponíveis: {valid_modes }"
            )

        self .mode =mode
        self ._pipeline =None

        logger .info ('SentimentAnalyzer inicializado — modo: %s',self .mode )

    def _load_finbert (self )->None :
        """
        Carrega o pipeline FinBERT-PT-BR de forma lazy.

        Importa transformers apenas quando necessário para evitar
        carregar torch/transformers no modo lightweight.

        Raises:
            ImportError: Se transformers não estiver instalado.
        """
        if self ._pipeline is not None :
            return

        logger .info ('Carregando modelo FinBERT-PT-BR...')
        try :
            from transformers import pipeline as hf_pipeline
            self ._pipeline =hf_pipeline (
            'text-classification',
            model ='lucas-leme/FinBERT-PT-BR',
            )
            logger .info ('FinBERT-PT-BR carregado com sucesso.')
        except ImportError :
            logger .error (
            'Pacotes transformers/torch não instalados. '
            'Instale com: pip install transformers torch'
            )
            raise ImportError (
            "Para usar o modo 'full', instale: pip install transformers torch"
            )
        except Exception as exc :
            logger .error ('Erro ao carregar FinBERT-PT-BR: %s',exc )
            raise

    def analyze_text (self ,text :str )->float :
        """
        Analisa o sentimento de um texto individual.

        Despacha para o método apropriado conforme o modo configurado.

        Args:
            text: Texto a ser analisado.

        Returns:
            Score de sentimento em [-1.0, 1.0].
            Positivo = otimista, Negativo = pessimista, 0.0 = neutro.
        """
        if not text or not text .strip ():
            return 0.0

        if self .mode =='full':
            return self ._analyze_text_finbert (text )
        return self ._analyze_text_lightweight (text )

    def _analyze_text_finbert (self ,text :str )->float :
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
        self ._load_finbert ()

        try :

            truncated =text [:2000 ]
            result =self ._pipeline (truncated )

            if not result :
                return 0.0

            prediction =result [0 ]
            label =prediction ['label'].upper ()
            confidence =float (prediction ['score'])

            if label =='POSITIVE':
                return confidence
            elif label =='NEGATIVE':
                return -confidence
            else :
                return 0.0

        except Exception as exc :
            logger .error ('Erro na análise FinBERT: %s',exc )
            return 0.0

    def _analyze_text_lightweight (self ,text :str ,is_title :bool =False )->float :
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
        if not text or not text .strip ():
            return 0.0

        text_lower =text .lower ()
        total_score =0.0
        match_count =0
        weight_multiplier =2.0 if is_title else 1.0

        all_keywords :dict [str ,float ]={}
        all_keywords .update (POSITIVE_KEYWORDS )
        all_keywords .update (NEGATIVE_KEYWORDS )

        sorted_keywords =sorted (all_keywords .keys (),key =len ,reverse =True )

        remaining_text =text_lower

        for keyword in sorted_keywords :
            count =remaining_text .count (keyword )
            if count >0 :
                score =all_keywords [keyword ]

                weighted =sum (score /(i +1 )for i in range (count ))
                total_score +=weighted *weight_multiplier
                match_count +=count

                remaining_text =remaining_text .replace (keyword ,' ')

        if match_count ==0 :
            return 0.0

        normalized =total_score /match_count
        return clamp (normalized ,-1.0 ,1.0 )

    def analyze_news_batch (self ,news_items :list [NewsItem ])->float :
        """
        Analisa sentimento de uma lista de notícias com ponderação por recência.

        Notícias mais recentes recebem peso maior usando decaimento
        exponencial: weight_i = exp(-decay * hours_since_publish).

        Args:
            news_items: Lista de itens de notícia (devem seguir o protocolo NewsItem).

        Returns:
            Score médio ponderado em [-1.0, 1.0].
        """
        if not news_items :
            logger .debug ('Nenhuma notícia para análise de sentimento em lote.')
            return 0.0

        now =datetime .now (BRT )
        weighted_sum =0.0
        total_weight =0.0

        for item in news_items :
            try :

                title_score =self ._analyze_single_text (item .title ,is_title =True )
                body_score =self ._analyze_single_text (item .summary ,is_title =False )

                combined_score =title_score *0.6 +body_score *0.4

                published =item .published_at
                if published .tzinfo is None :
                    published =published .replace (tzinfo =BRT )

                hours_since =max (0.0 ,(now -published ).total_seconds ()/3600.0 )
                weight =math .exp (-self .RECENCY_DECAY *hours_since )

                weighted_sum +=combined_score *weight
                total_weight +=weight

            except Exception as exc :
                logger .warning (
                'Erro ao analisar notícia "%s": %s',
                getattr (item ,'title','N/A')[:50 ],exc ,
                )
                continue

        if total_weight ==0.0 :
            return 0.0

        result =weighted_sum /total_weight
        logger .debug (
        'Sentimento em lote: %.3f (média ponderada de %d notícias)',
        result ,len (news_items ),
        )
        return clamp (result ,-1.0 ,1.0 )

    def _analyze_single_text (self ,text :str ,is_title :bool =False )->float :
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
        if not text or not text .strip ():
            return 0.0

        if self .mode =='full':
            return self ._analyze_text_finbert (text )
        return self ._analyze_text_lightweight (text ,is_title =is_title )

    def get_sentiment_for_ticker (
    self ,ticker :str ,news_items :list [NewsItem ]
    )->SentimentResult :
        """
        Gera resultado consolidado de sentimento para um ativo específico.

        Calcula score ponderado por recência e gera raciocínio
        explicativo em português.

        Args:
            ticker: Código do ativo (ex: 'PETR4').
            news_items: Lista de notícias relevantes ao ativo.

        Returns:
            SentimentResult com score, label, confiança e raciocínio.
        """
        logger .debug ('Analisando sentimento para %s (%d notícias)',ticker ,len (news_items ))

        if not news_items :
            return SentimentResult (
            score =0.0 ,
            label ='NEUTRO',
            confidence =0.0 ,
            news_count =0 ,
            top_headline ='Sem notícias disponíveis',
            reasoning =f'Nenhuma notícia encontrada para {ticker }. Sentimento indefinido.',
            )

        batch_score =self .analyze_news_batch (news_items )

        label =self ._score_to_label (batch_score )
        confidence =self ._calculate_confidence (batch_score ,len (news_items ))

        sorted_news =sorted (
        news_items ,
        key =lambda n :n .published_at ,
        reverse =True ,
        )
        top_headline =sorted_news [0 ].title if sorted_news else 'N/A'

        reasoning =self ._generate_reasoning (
        ticker ,batch_score ,label ,confidence ,len (news_items ),top_headline
        )

        result =SentimentResult (
        score =round (batch_score ,4 ),
        label =label ,
        confidence =round (confidence ,4 ),
        news_count =len (news_items ),
        top_headline =top_headline ,
        reasoning =reasoning ,
        )

        logger .info (
        'Sentimento %s: score=%.3f (%s), confiança=%.2f, %d notícias',
        ticker ,result .score ,result .label ,result .confidence ,result .news_count ,
        )

        return result

    @staticmethod
    def _score_to_label (score :float )->str :
        """
        Converte score numérico em label textual.

        Args:
            score: Score de sentimento em [-1.0, 1.0].

        Returns:
            'POSITIVO', 'NEGATIVO' ou 'NEUTRO'.
        """
        if score >=0.15 :
            return 'POSITIVO'
        elif score <=-0.15 :
            return 'NEGATIVO'
        return 'NEUTRO'

    @staticmethod
    def _calculate_confidence (score :float ,news_count :int )->float :
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

        magnitude_component =abs (score )

        volume_component =min (1.0 ,math .log2 (max (1 ,news_count ))/4.0 )

        if magnitude_component ==0.0 and volume_component ==0.0 :
            return 0.0

        confidence =magnitude_component *0.6 +volume_component *0.4
        return clamp (confidence ,0.0 ,1.0 )

    @staticmethod
    def _generate_reasoning (
    ticker :str ,
    score :float ,
    label :str ,
    confidence :float ,
    news_count :int ,
    top_headline :str ,
    )->str :
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
        parts :list [str ]=[]

        score_fmt =f'{score :+.2f}'.replace ('.',',')

        if label =='POSITIVO':
            parts .append (
            f'Sentimento de mercado para {ticker }: {score_fmt } (otimista).'
            )
        elif label =='NEGATIVO':
            parts .append (
            f'Sentimento de mercado para {ticker }: {score_fmt } (pessimista).'
            )
        else :
            parts .append (
            f'Sentimento de mercado para {ticker }: {score_fmt } (neutro).'
            )

        if news_count ==1 :
            parts .append ('Baseado em 1 notícia recente.')
        else :
            parts .append (f'Baseado em {news_count } notícias recentes.')

        if confidence >=0.7 :
            parts .append ('Alta confiança na análise.')
        elif confidence >=0.4 :
            parts .append ('Confiança moderada na análise.')
        else :
            parts .append ('Baixa confiança — poucas notícias ou sentimento fraco.')

        headline_truncated =(
        top_headline [:80 ]+'...'if len (top_headline )>80 else top_headline
        )
        parts .append (f"Última notícia relevante: '{headline_truncated }'.")

        return ' '.join (parts )
