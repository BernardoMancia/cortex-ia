"""
Agendador de mercado do Projeto Córtex.

Controla horários de operação da B3, feriados fixos/móveis, fechamento
bancário de fim de ano, horário de verão americano (US DST), estabilização
de abertura, call de fechamento e janelas de manutenção.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from utils.helpers import BRT

logger = logging.getLogger('cortex.scheduler')

BRT_TZ: ZoneInfo = BRT

MARKET_OPEN_TIME = time(10, 0)
MARKET_CLOSE_SUMMER = time(16, 55)
MARKET_CLOSE_WINTER = time(17, 55)

ASH_WEDNESDAY_OPEN_TIME = time(13, 0)

MAINTENANCE_WINDOWS: list[time] = [time(6, 0), time(19, 0), time(23, 0)]
MAINTENANCE_TOLERANCE_MINUTES: int = 5

B3_FIXED_HOLIDAYS: list[tuple[int, int]] = [
    (1, 1),
    (21, 4),
    (1, 5),
    (7, 9),
    (12, 10),
    (2, 11),
    (15, 11),
    (20, 11),
    (24, 12),
    (25, 12),
    (31, 12),
]


def _easter(year: int) -> date:
    """
    Calcula a data da Páscoa usando o algoritmo de Butcher.
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
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _get_movable_holidays(year: int) -> list[date]:
    """
    Gera feriados móveis da B3 a partir da Páscoa:
    - Carnaval (segunda e terça-feira)
    - Sexta-feira Santa
    - Corpus Christi
    """
    easter = _easter(year)
    return [
        easter - timedelta(days=48),
        easter - timedelta(days=47),
        easter - timedelta(days=2),
        easter + timedelta(days=60),
    ]


def _get_ash_wednesday(year: int) -> date:
    """Retorna a data da Quarta-Feira de Cinzas (Páscoa - 46 dias)."""
    return _easter(year) - timedelta(days=46)


class MarketScheduler:
    """Agendador de horários de mercado da B3."""

    def __init__(self) -> None:
        """Inicializa o agendador de mercado."""
        self._completed_maintenance_windows: set[tuple[date, time]] = set()
        logger.info('MarketScheduler inicializado — TZ: America/Sao_Paulo')

    @staticmethod
    def _resolve_brt(dt: datetime | None = None) -> datetime:
        if dt is None:
            return datetime.now(BRT_TZ)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=BRT_TZ)
        return dt.astimezone(BRT_TZ)

    def is_market_open(self) -> bool:
        """
        Verifica se o mercado B3 está aberto no momento atual.
        """
        now = datetime.now(BRT_TZ)
        current_time = now.time()
        current_date = now.date()

        if not self.is_business_day(current_date):
            logger.debug('Mercado fechado: %s não é dia útil', current_date)
            return False

        open_time = self.get_market_open_time(current_date)
        close_time = self.get_market_close_time(current_date)

        is_open = open_time <= current_time <= close_time

        if is_open:
            logger.debug(
                'Mercado ABERTO: %s (horário: %s–%s)',
                current_time.strftime('%H:%M'),
                open_time.strftime('%H:%M'),
                close_time.strftime('%H:%M'),
            )
        else:
            logger.debug(
                'Mercado FECHADO: %s (horário: %s–%s)',
                current_time.strftime('%H:%M'),
                open_time.strftime('%H:%M'),
                close_time.strftime('%H:%M'),
            )

        return is_open

    def is_business_day(self, check_date: date | None = None) -> bool:
        """
        Verifica se uma data é dia de pregão na B3 (exclui fins de semana,
        feriados fixos/móveis, Véspera de Natal e último dia útil bancário do ano
        quando 31/12 cai no sábado ou domingo).
        """
        if check_date is None:
            check_date = datetime.now(BRT_TZ).date()

        if check_date.weekday() >= 5:
            return False

        day_month = (check_date.day, check_date.month)
        if day_month in B3_FIXED_HOLIDAYS:
            return False

        if check_date.month == 12:
            dec_31 = date(check_date.year, 12, 31)
            if dec_31.weekday() == 5 and check_date.day == 30:
                return False
            if dec_31.weekday() == 6 and check_date.day == 29:
                return False

        movable = _get_movable_holidays(check_date.year)
        if check_date in movable:
            return False

        return True

    def is_ash_wednesday(self, check_date: date | None = None) -> bool:
        """Verifica se a data é Quarta-Feira de Cinzas (abertura às 13:00)."""
        if check_date is None:
            check_date = datetime.now(BRT_TZ).date()
        return check_date == _get_ash_wednesday(check_date.year)

    def is_summer_schedule(self, check_date: date | None = None) -> bool:
        """
        Verifica se estamos no horário de verão americano (US DST):
        - US DST (2º domingo de março → 1º domingo de novembro): fecha 16:55
        - US Standard (1º domingo de novembro → 2º domingo de março): fecha 17:55
        """
        if check_date is None:
            check_date = datetime.now(BRT_TZ).date()
        return self._is_us_dst(check_date)

    @staticmethod
    def _is_us_dst(check_date: date) -> bool:
        """Determina se uma data cai dentro do US DST."""
        year = check_date.year

        march_first = date(year, 3, 1)
        days_to_sunday = (6 - march_first.weekday()) % 7
        first_sunday_march = march_first + timedelta(days=days_to_sunday)
        second_sunday_march = first_sunday_march + timedelta(days=7)

        november_first = date(year, 11, 1)
        days_to_sunday_nov = (6 - november_first.weekday()) % 7
        first_sunday_november = november_first + timedelta(days=days_to_sunday_nov)

        return second_sunday_march <= check_date < first_sunday_november

    def get_market_open_time(self, check_date: date | None = None) -> time:
        """Retorna horário de abertura do mercado (13:00 na Quarta de Cinzas, 10:00 nos demais)."""
        if check_date is None:
            check_date = datetime.now(BRT_TZ).date()

        if self.is_ash_wednesday(check_date):
            logger.debug(
                'Quarta-Feira de Cinzas (%s) — abertura às 13:00',
                check_date,
            )
            return ASH_WEDNESDAY_OPEN_TIME
        return MARKET_OPEN_TIME

    def get_market_close_time(self, check_date: date | None = None) -> time:
        """Retorna horário de fechamento do mercado baseado no US DST da data informada."""
        if check_date is None:
            check_date = datetime.now(BRT_TZ).date()
        if self._is_us_dst(check_date):
            return MARKET_CLOSE_SUMMER
        return MARKET_CLOSE_WINTER

    def is_opening_stabilization(self, dt: datetime | None = None, minutes: int | None = None) -> bool:
        """
        Retorna True nos primeiros `minutes` minutos após a abertura do pregão,
        permitindo evitar novas ordens de compra com cotações de leilão/volatilidade inicial.
        """
        if minutes is None:
            from config.settings import settings
            minutes = int(getattr(settings, "OPENING_COOLDOWN_MINUTES", 5))
        if int(minutes) <= 0:
            return False
        now = self._resolve_brt(dt)
        if not self.is_business_day(now.date()):
            return False
        open_t = self.get_market_open_time(now.date())
        open_sec = open_t.hour * 3600 + open_t.minute * 60 + open_t.second
        now_sec = now.hour * 3600 + now.minute * 60 + now.second
        return open_sec <= now_sec < (open_sec + max(0, int(minutes)) * 60)

    def add_business_days(self, start_date: date, business_days: int = 2) -> date:
        """
        Avança `business_days` dias úteis de pregão da B3 a partir de `start_date`
        (ex: liquidação financeira D+2 de vendas de ações na B3), pulando fins de
        semana, feriados fixos/móveis e fechamentos bancários de fim de ano.
        """
        curr = start_date
        # Se a operação ocorreu em data não útil (ex: registro retroativo), alinha ao próximo pregão
        safety = 0
        while not self.is_business_day(curr) and safety < 15:
            curr += timedelta(days=1)
            safety += 1

        remaining = max(0, int(business_days))
        safety = 0
        while remaining > 0 and safety < 60:
            curr += timedelta(days=1)
            safety += 1
            if self.is_business_day(curr):
                remaining -= 1
        return curr

    def is_in_closing_call(self, dt: datetime | None = None, minutes: int = 5) -> bool:
        """
        Retorna True nos últimos `minutes` minutos antes do fechamento contínuo da B3.
        """
        now = self._resolve_brt(dt)
        if not self.is_business_day(now.date()):
            return False
        close_t = self.get_market_close_time(now.date())
        close_sec = close_t.hour * 3600 + close_t.minute * 60 + close_t.second
        now_sec = now.hour * 3600 + now.minute * 60 + now.second
        return (close_sec - max(0, int(minutes)) * 60) <= now_sec <= close_sec

    def time_to_next_open(self) -> timedelta:
        """Calcula o tempo até a próxima abertura do mercado."""
        now = datetime.now(BRT_TZ)
        today = now.date()
        open_time = self.get_market_open_time(today)

        if self.is_business_day(today):
            today_open = datetime.combine(today, open_time, tzinfo=BRT_TZ)
            if now < today_open:
                return today_open - now

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
        """Retorna horários das janelas de manutenção do sistema: [06:00, 19:00, 23:00]."""
        return list(MAINTENANCE_WINDOWS)

    def get_active_maintenance_window(self, dt: datetime | None = None) -> tuple[date, time] | None:
        """Retorna a chave `(data, horário_janela)` se estiver dentro da tolerância de manutenção."""
        now = self._resolve_brt(dt)
        current_minutes = now.hour * 60 + now.minute

        for maint_time in MAINTENANCE_WINDOWS:
            maint_minutes = maint_time.hour * 60 + maint_time.minute
            diff = abs(current_minutes - maint_minutes)
            diff = min(diff, 1440 - diff)
            if diff <= MAINTENANCE_TOLERANCE_MINUTES:
                return (now.date(), maint_time)
        return None

    def should_run_maintenance(self, dt: datetime | None = None) -> bool:
        """
        Verifica de forma pura/stateless se o horário informado está dentro de ±5 min
        de alguma janela de manutenção.
        """
        win = self.get_active_maintenance_window(dt)
        if win is not None:
            logger.info(
                'Janela de manutenção ativa: %s (±%d min)',
                win[1].strftime('%H:%M'),
                MAINTENANCE_TOLERANCE_MINUTES,
            )
            return True
        return False

    def mark_maintenance_done(self, dt: datetime | None = None) -> None:
        """Marca a janela de manutenção atual como executada para evitar repetição."""
        win = self.get_active_maintenance_window(dt)
        if win is not None:
            today_d = win[0]
            self._completed_maintenance_windows = {
                w for w in self._completed_maintenance_windows if w[0] == today_d
            }
            self._completed_maintenance_windows.add(win)

    def is_maintenance_pending(self, dt: datetime | None = None) -> bool:
        """Retorna True se estiver em janela de manutenção ainda não concluída hoje."""
        win = self.get_active_maintenance_window(dt)
        if win is None:
            return False
        return win not in self._completed_maintenance_windows
