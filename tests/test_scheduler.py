"""
Testes do MarketScheduler do Projeto Córtex.

Valida horários de mercado, feriados, horário de verão
e janelas de manutenção.
"""

from __future__ import annotations

import sys
from datetime import date ,datetime ,time ,timedelta
from pathlib import Path
from unittest .mock import patch

import pytest

sys .path .insert (0 ,str (Path (__file__ ).resolve ().parent .parent ))

from core .scheduler import BRT_TZ ,MarketScheduler

@pytest .fixture
def scheduler ()->MarketScheduler :
    """Instância do MarketScheduler para testes."""
    return MarketScheduler ()

class TestIsMarketOpen :
    """Testes para verificação de mercado aberto."""

    def test_market_open_during_hours_weekday (self ,scheduler :MarketScheduler )->None :
        """Mercado deve estar aberto às 14:00 BRT em dia útil."""

        mock_now =datetime (2026 ,7 ,8 ,14 ,0 ,tzinfo =BRT_TZ )
        with patch ('core.scheduler.datetime')as mock_dt :
            mock_dt .now .return_value =mock_now
            mock_dt .side_effect =lambda *a ,**kw :datetime (*a ,**kw )
            assert scheduler .is_market_open ()is True

    def test_market_open_at_opening (self ,scheduler :MarketScheduler )->None :
        """Mercado deve estar aberto exatamente na abertura (10:00)."""
        mock_now =datetime (2026 ,7 ,8 ,10 ,0 ,tzinfo =BRT_TZ )
        with patch ('core.scheduler.datetime')as mock_dt :
            mock_dt .now .return_value =mock_now
            mock_dt .side_effect =lambda *a ,**kw :datetime (*a ,**kw )
            assert scheduler .is_market_open ()is True

    def test_market_closed_before_opening (self ,scheduler :MarketScheduler )->None :
        """Mercado deve estar fechado antes das 10:00."""
        mock_now =datetime (2026 ,7 ,8 ,9 ,59 ,tzinfo =BRT_TZ )
        with patch ('core.scheduler.datetime')as mock_dt :
            mock_dt .now .return_value =mock_now
            mock_dt .side_effect =lambda *a ,**kw :datetime (*a ,**kw )
            assert scheduler .is_market_open ()is False

    def test_market_closed_after_hours (self ,scheduler :MarketScheduler )->None :
        """Mercado deve estar fechado às 20:00."""
        mock_now =datetime (2026 ,7 ,8 ,20 ,0 ,tzinfo =BRT_TZ )
        with patch ('core.scheduler.datetime')as mock_dt :
            mock_dt .now .return_value =mock_now
            mock_dt .side_effect =lambda *a ,**kw :datetime (*a ,**kw )
            assert scheduler .is_market_open ()is False

    def test_market_closed_weekend_saturday (self ,scheduler :MarketScheduler )->None :
        """Mercado deve estar fechado no sábado."""

        mock_now =datetime (2026 ,7 ,11 ,14 ,0 ,tzinfo =BRT_TZ )
        with patch ('core.scheduler.datetime')as mock_dt :
            mock_dt .now .return_value =mock_now
            mock_dt .side_effect =lambda *a ,**kw :datetime (*a ,**kw )
            assert scheduler .is_market_open ()is False

    def test_market_closed_weekend_sunday (self ,scheduler :MarketScheduler )->None :
        """Mercado deve estar fechado no domingo."""

        mock_now =datetime (2026 ,7 ,12 ,14 ,0 ,tzinfo =BRT_TZ )
        with patch ('core.scheduler.datetime')as mock_dt :
            mock_dt .now .return_value =mock_now
            mock_dt .side_effect =lambda *a ,**kw :datetime (*a ,**kw )
            assert scheduler .is_market_open ()is False

class TestIsBusinessDay :
    """Testes para verificação de dia útil."""

    def test_weekday_is_business_day (self ,scheduler :MarketScheduler )->None :
        """Dia de semana normal deve ser dia útil."""

        assert scheduler .is_business_day (date (2026 ,7 ,8 ))is True

    def test_saturday_is_not_business_day (self ,scheduler :MarketScheduler )->None :
        """Sábado não é dia útil."""
        assert scheduler .is_business_day (date (2026 ,7 ,11 ))is False

    def test_sunday_is_not_business_day (self ,scheduler :MarketScheduler )->None :
        """Domingo não é dia útil."""
        assert scheduler .is_business_day (date (2026 ,7 ,12 ))is False

    def test_christmas_is_holiday (self ,scheduler :MarketScheduler )->None :
        """Natal (25/12) é feriado B3."""
        assert scheduler .is_business_day (date (2026 ,12 ,25 ))is False

    def test_independence_day_is_holiday (self ,scheduler :MarketScheduler )->None :
        """Independência (7/9) é feriado B3."""
        assert scheduler .is_business_day (date (2026 ,9 ,7 ))is False

    def test_new_year_is_holiday (self ,scheduler :MarketScheduler )->None :
        """Ano Novo (1/1) é feriado B3."""
        assert scheduler .is_business_day (date (2026 ,1 ,1 ))is False

    def test_tiradentes_is_holiday (self ,scheduler :MarketScheduler )->None :
        """Tiradentes (21/4) é feriado B3."""
        assert scheduler .is_business_day (date (2026 ,4 ,21 ))is False

    def test_movable_holiday_carnival_2026 (self ,scheduler :MarketScheduler )->None :
        """Carnaval 2026 (16-17/02) é feriado móvel B3."""
        assert scheduler .is_business_day (date (2026 ,2 ,16 ))is False
        assert scheduler .is_business_day (date (2026 ,2 ,17 ))is False

    def test_movable_holiday_good_friday_2026 (self ,scheduler :MarketScheduler )->None :
        """Sexta-feira Santa 2026 (03/04) é feriado móvel B3."""
        assert scheduler .is_business_day (date (2026 ,4 ,3 ))is False

    def test_normal_weekday_not_holiday (self ,scheduler :MarketScheduler )->None :
        """Dia normal de semana não é feriado."""
        assert scheduler .is_business_day (date (2026 ,7 ,7 ))is True

class TestSummerSchedule :
    """Testes para detecção de horário de verão americano."""

    def test_summer_schedule_july (self ,scheduler :MarketScheduler )->None :
        """Julho (inverno BR, verão US) deve ser horário de verão americano."""

        mock_now =datetime (2026 ,7 ,9 ,14 ,0 ,tzinfo =BRT_TZ )
        with patch ('core.scheduler.datetime')as mock_dt :
            mock_dt .now .return_value =mock_now
            mock_dt .side_effect =lambda *a ,**kw :datetime (*a ,**kw )
            assert scheduler .is_summer_schedule ()is True

    def test_summer_close_time (self ,scheduler :MarketScheduler )->None :
        """No horário de verão americano, fechamento deve ser 16:55."""
        mock_now =datetime (2026 ,7 ,9 ,14 ,0 ,tzinfo =BRT_TZ )
        with patch ('core.scheduler.datetime')as mock_dt :
            mock_dt .now .return_value =mock_now
            mock_dt .side_effect =lambda *a ,**kw :datetime (*a ,**kw )
            assert scheduler .get_market_close_time ()==time (16 ,55 )

    def test_winter_schedule_december (self ,scheduler :MarketScheduler )->None :
        """Dezembro (fora do US DST) deve ser horário de inverno."""
        mock_now =datetime (2026 ,12 ,15 ,14 ,0 ,tzinfo =BRT_TZ )
        with patch ('core.scheduler.datetime')as mock_dt :
            mock_dt .now .return_value =mock_now
            mock_dt .side_effect =lambda *a ,**kw :datetime (*a ,**kw )
            assert scheduler .is_summer_schedule ()is False

    def test_winter_close_time (self ,scheduler :MarketScheduler )->None :
        """No horário de inverno, fechamento deve ser 17:55."""
        mock_now =datetime (2026 ,12 ,15 ,14 ,0 ,tzinfo =BRT_TZ )
        with patch ('core.scheduler.datetime')as mock_dt :
            mock_dt .now .return_value =mock_now
            mock_dt .side_effect =lambda *a ,**kw :datetime (*a ,**kw )
            assert scheduler .get_market_close_time ()==time (17 ,55 )

    def test_open_time_always_10 (self ,scheduler :MarketScheduler )->None :
        """Horário de abertura deve ser sempre 10:00."""
        assert scheduler .get_market_open_time ()==time (10 ,0 )

class TestUsDst :
    """Testes para detecção do US DST diretamente."""

    def test_us_dst_start_2026 (self )->None :
        """US DST 2026 inicia no 2º domingo de março (8 de março)."""

        assert MarketScheduler ._is_us_dst (date (2026 ,3 ,7 ))is False

        assert MarketScheduler ._is_us_dst (date (2026 ,3 ,8 ))is True

        assert MarketScheduler ._is_us_dst (date (2026 ,3 ,9 ))is True

    def test_us_dst_end_2026 (self )->None :
        """US DST 2026 termina no 1º domingo de novembro (1 de novembro)."""

        assert MarketScheduler ._is_us_dst (date (2026 ,10 ,31 ))is True

        assert MarketScheduler ._is_us_dst (date (2026 ,11 ,1 ))is False

    def test_january_is_not_dst (self )->None :
        """Janeiro está fora do US DST."""
        assert MarketScheduler ._is_us_dst (date (2026 ,1 ,15 ))is False

    def test_july_is_dst (self )->None :
        """Julho está dentro do US DST."""
        assert MarketScheduler ._is_us_dst (date (2026 ,7 ,15 ))is True

class TestTimeToNextOpen :
    """Testes para cálculo de tempo até próxima abertura."""

    def test_time_to_next_open_same_day (self ,scheduler :MarketScheduler )->None :
        """Antes da abertura no mesmo dia útil."""
        mock_now =datetime (2026 ,7 ,8 ,8 ,0 ,tzinfo =BRT_TZ )
        with patch ('core.scheduler.datetime')as mock_dt :
            mock_dt .now .return_value =mock_now
            mock_dt .side_effect =lambda *a ,**kw :datetime (*a ,**kw )
            mock_dt .combine =datetime .combine
            delta =scheduler .time_to_next_open ()
            assert delta .total_seconds ()==pytest .approx (7200 ,abs =60 )

    def test_time_to_next_open_after_close (self ,scheduler :MarketScheduler )->None :
        """Após o fechamento, deve retornar tempo para próximo dia útil."""

        mock_now =datetime (2026 ,7 ,8 ,20 ,0 ,tzinfo =BRT_TZ )
        with patch ('core.scheduler.datetime')as mock_dt :
            mock_dt .now .return_value =mock_now
            mock_dt .side_effect =lambda *a ,**kw :datetime (*a ,**kw )
            mock_dt .combine =datetime .combine
            delta =scheduler .time_to_next_open ()
            assert delta .total_seconds ()>0

class TestMaintenanceWindows :
    """Testes para janelas de manutenção."""

    def test_get_maintenance_times (self ,scheduler :MarketScheduler )->None :
        """Deve retornar as 3 janelas de manutenção."""
        times =scheduler .get_maintenance_times ()
        assert len (times )==3
        assert time (6 ,0 )in times
        assert time (19 ,0 )in times
        assert time (23 ,0 )in times

    def test_should_run_maintenance_at_window (self ,scheduler :MarketScheduler )->None :
        """Deve retornar True dentro de 5 min da janela de manutenção."""
        mock_now =datetime (2026 ,7 ,9 ,6 ,3 ,tzinfo =BRT_TZ )
        with patch ('core.scheduler.datetime')as mock_dt :
            mock_dt .now .return_value =mock_now
            mock_dt .side_effect =lambda *a ,**kw :datetime (*a ,**kw )
            assert scheduler .should_run_maintenance ()is True

    def test_should_not_run_maintenance_outside_window (
    self ,scheduler :MarketScheduler
    )->None :
        """Deve retornar False fora das janelas de manutenção."""
        mock_now =datetime (2026 ,7 ,9 ,14 ,30 ,tzinfo =BRT_TZ )
        with patch ('core.scheduler.datetime')as mock_dt :
            mock_dt .now .return_value =mock_now
            mock_dt .side_effect =lambda *a ,**kw :datetime (*a ,**kw )
            assert scheduler .should_run_maintenance ()is False
