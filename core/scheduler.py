"""
Agendador de mercado do Projeto Córtex.

Controla horários de operação da B3, feriados, horário de verão
e janelas de manutenção do sistema. Todas as operações de data/hora
são timezone-aware (BRT = UTC-3, via zoneinfo).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger('cortex.scheduler')

# Timezone BRT via zoneinfo (correto para DST automático)
BRT_TZ = ZoneInfo('America/Sao_Paulo')

# Horários de mercado da B3
MARKET_OPEN_TIME = time(10, 0)
MARKET_CLOSE_SUMMER = time(16, 55)  # Horário de verão americano
MARKET_CLOSE_WINTER = time(17, 55)  # Horário de inverno americano

# Horário especial para Quarta-Feira de Cinzas (mercado abre às 13:00)
ASH_WEDNESDAY_OPEN_TIME = time(13, 0)

# Janelas de manutenção do sistema
MAINTENANCE_WINDOWS: list[time] = [time(6, 0), time(19, 0), time(23, 0)]
MAINTENANCE_TOLERANCE_MINUTES: int = 5

# Feriados fixos da B3 (dia, mês) — não inclui feriados móveis
B3_FIXED_HOLIDAYS: list[tuple[int, int]] = [
    (1, 1),    # Confraternização Universal
    (21, 4),   # Tiradentes
    (1, 5),    # Dia do Trabalhador
    (7, 9),    # Independência do Brasil
    (12, 10),  # Nossa Senhora Aparecida
    (2, 11),   # Finados
    (15, 11),  # Proclamação da República
    (20, 11),  # Dia da Consciência Negra
    (25, 12),  # Natal
    (31, 12),  # Véspera de Ano Novo (fechamento parcial → tratado como fechado)
]


def _easter(year: int) -> date:
    """
    Calcula a data da Páscoa usando o algoritmo de Butcher.

    Args:
        year: Ano para calcular a Páscoa.

    Returns:
        Data da Páscoa (domingo) para o ano informado.
    """
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _get_movable_holidays(year: int) -> list[date]:
    """
    Gera feriados móveis da B3 a partir da Páscoa.

    Feriados derivados:
    - Carnaval segunda-feira: Páscoa - 48 dias
    - Carnaval terça-feira: Páscoa - 47 dias
    - Sexta-feira Santa: Páscoa - 2 dias
    - Corpus Christi: Páscoa + 60 dias

    Args:
        year: Ano para gerar os feriados.

    Returns:
        Lista de datas dos feriados móveis.
    """
    easter = _easter(year)
    return [
        easter - timedelta(days=48),  # Carnaval segunda
        easter - timedelta(days=47),  # Carnaval terça
        easter - timedelta(days=2),   # Sexta-feira Santa
        easter + timedelta(days=60),  # Corpus Christi
    ]


def _get_ash_wednesday(year: int) -> date:
    """
    Retorna a data da Quarta-Feira de Cinzas (Páscoa - 46 dias).

    Na B3, Quarta-Feira de Cinzas é dia de pregão com horário
    especial — mercado abre às 13:00 em vez de 10:00.

    Args:
        year: Ano para calcular.

    Returns:
        Data da Quarta-Feira de Cinzas.
    """
    return _easter(year) - timedelta(days=46)


class MarketScheduler:
    """Agendador de horários de mercado da B3."""

    def __init__(self) -> None:
        """Inicializa o agendador de mercado."""
        logger.info('MarketScheduler inicializado — TZ: America/Sao_Paulo')

    def is_market_open(self) -> bool:
        """
        Verifica se o mercado B3 está aberto no momento atual.

        Considera:
        - Horário BRT atual entre abertura e fechamento.
        - Dia útil (não é fds nem feriado).
        - Horário de verão/inverno americano para fechamento.
        - Quarta-Feira de Cinzas: abertura às 13:00 em vez de 10:00.

        Returns:
            True se o mercado está aberto, False caso contrário.
        """
        now = datetime.now(BRT_TZ)
        current_time = now.time()
        current_date = now.date()

        # Verificar dia útil
        if not self.is_business_day(current_date):
            logger.debug('Mercado fechado: %s não é dia útil', current_date)
            return False

        # Verificar horário
        open_time = self.get_market_open_time(current_date)
        close_time = self.get_market_close_time()

        is_open = open_time <= current_time <= close_time

        if is_open:
            logger.debug(
                'Mercado ABERTO: %s (horário: %s–%s)',
                current_time.strftime('%H:%M'), open_time.strftime('%H:%M'),
                close_time.strftime('%H:%M'),
            )
        else:
            logger.debug(
                'Mercado FECHADO: %s (horário: %s–%s)',
                current_time.strftime('%H:%M'), open_time.strftime('%H:%M'),
                close_time.strftime('%H:%M'),
            )

        return is_open

    def is_business_day(self, check_date: date | None = None) -> bool:
        """
        Verifica se uma data é dia útil (não é fim de semana nem feriado B3).

        Args:
            check_date: Data a verificar (padrão: hoje em BRT).

        Returns:
            True se é dia útil.
        """
        if check_date is None:
            check_date = datetime.now(BRT_TZ).date()

        # Fim de semana: 5 = sábado, 6 = domingo
        if check_date.weekday() >= 5:
            return False

        # Feriados fixos
        day_month = (check_date.day, check_date.month)
        if day_month in B3_FIXED_HOLIDAYS:
            return False

        # Feriados móveis (calculados algoritmicamente)
        movable = _get_movable_holidays(check_date.year)
        if check_date in movable:
            return False

        return True

    def is_ash_wednesday(self, check_date: date | None = None) -> bool:
        """
        Verifica se a data é Quarta-Feira de Cinzas.

        Na B3, Quarta-Feira de Cinzas não é feriado, mas o mercado
        abre às 13:00 em vez de 10:00.

        Args:
            check_date: Data a verificar (padrão: hoje em BRT).

        Returns:
            True se é Quarta-Feira de Cinzas.
        """
        if check_date is None:
            check_date = datetime.now(BRT_TZ).date()
        return check_date == _get_ash_wednesday(check_date.year)

    def is_summer_schedule(self) -> bool:
        """
        Verifica se estamos no horário de verão americano (US DST).

        A B3 usa o horário de verão AMERICANO (não brasileiro) para
        definir o horário de fechamento:
        - US DST (2º domingo de março → 1º domingo de novembro): fecha 16:55
        - US Standard (1º domingo de novembro → 2º domingo de março): fecha 17:55

        Returns:
            True se estamos no horário de verão americano.
        """
        now = datetime.now(BRT_TZ)
        return self._is_us_dst(now.date())

    @staticmethod
    def _is_us_dst(check_date: date) -> bool:
        """
        Determina se uma data cai dentro do US DST.

        US DST: 2º domingo de março até 1º domingo de novembro.

        Args:
            check_date: Data a verificar.

        Returns:
            True se a data está dentro do US DST.
        """
        year = check_date.year

        # 2º domingo de março
        march_first = date(year, 3, 1)
        # Encontrar primeiro domingo de março
        days_to_sunday = (6 - march_first.weekday()) % 7
        first_sunday_march = march_first + timedelta(days=days_to_sunday)
        second_sunday_march = first_sunday_march + timedelta(days=7)

        # 1º domingo de novembro
        november_first = date(year, 11, 1)
        days_to_sunday_nov = (6 - november_first.weekday()) % 7
        first_sunday_november = november_first + timedelta(days=days_to_sunday_nov)

        return second_sunday_march <= check_date < first_sunday_november

    def get_market_open_time(self, check_date: date | None = None) -> time:
        """
        Retorna horário de abertura do mercado.

        Na Quarta-Feira de Cinzas, o mercado abre às 13:00.
        Nos demais dias úteis, abre às 10:00.

        Args:
            check_date: Data a verificar (padrão: hoje em BRT).

        Returns:
            Horário de abertura (13:00 na Quarta de Cinzas, 10:00 nos demais).
        """
        if check_date is None:
            check_date = datetime.now(BRT_TZ).date()

        if self.is_ash_wednesday(check_date):
            logger.debug(
                'Quarta-Feira de Cinzas (%s) — abertura às 13:00',
                check_date,
            )
            return ASH_WEDNESDAY_OPEN_TIME
        return MARKET_OPEN_TIME

    def get_market_close_time(self) -> time:
        """
        Retorna horário de fechamento do mercado baseado no
        horário de verão americano.

        Returns:
            16:55 no horário de verão, 17:55 no horário de inverno.
        """
        if self.is_summer_schedule():
            return MARKET_CLOSE_SUMMER
        return MARKET_CLOSE_WINTER

    def time_to_next_open(self) -> timedelta:
        """
        Calcula o tempo até a próxima abertura do mercado.

        Considera fins de semana e feriados para encontrar
        o próximo dia útil.

        Returns:
            timedelta até a próxima abertura.
        """
        now = datetime.now(BRT_TZ)
        today = now.date()
        open_time = self.get_market_open_time(today)

        # Se hoje é dia útil e ainda não abriu
        if self.is_business_day(today):
            today_open = datetime.combine(today, open_time, tzinfo=BRT_TZ)
            if now < today_open:
                return today_open - now

        # Encontrar próximo dia útil
        next_day = today + timedelta(days=1)
        safety_counter = 0
        while not self.is_business_day(next_day) and safety_counter < 15:
            next_day += timedelta(days=1)
            safety_counter += 1

        next_open_time = self.get_market_open_time(next_day)
        next_open = datetime.combine(next_day, next_open_time, tzinfo=BRT_TZ)
        delta = next_open - now

        logger.debug(
            'Próxima abertura: %s %s (em %s)',
            next_day, next_open_time, delta,
        )
        return delta

    def get_maintenance_times(self) -> list[time]:
        """
        Retorna horários das janelas de manutenção do sistema.

        Returns:
            Lista de horários: [06:00, 19:00, 23:00].
        """
        return list(MAINTENANCE_WINDOWS)

    def should_run_maintenance(self) -> bool:
        """
        Verifica se o sistema deve executar manutenção agora.

        Retorna True se o horário atual está dentro de 5 minutos
        de alguma janela de manutenção.

        Returns:
            True se está em janela de manutenção.
        """
        now = datetime.now(BRT_TZ)
        current_minutes = now.hour * 60 + now.minute

        for maint_time in MAINTENANCE_WINDOWS:
            maint_minutes = maint_time.hour * 60 + maint_time.minute
            diff = abs(current_minutes - maint_minutes)

            # Considerar virada de meia-noite
            diff = min(diff, 1440 - diff)

            if diff <= MAINTENANCE_TOLERANCE_MINUTES:
                logger.info(
                    'Janela de manutenção ativa: %s (±%d min)',
                    maint_time.strftime('%H:%M'), MAINTENANCE_TOLERANCE_MINUTES,
                )
                return True

        return False
