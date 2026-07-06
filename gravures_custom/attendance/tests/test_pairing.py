"""Tests for attendance pairing logic."""
import unittest
from datetime import datetime
from gravures_custom.attendance.pairing import (
    pair_checkins,
    seconds_from_hours,
    format_hhmm,
)


def t(h, m=0):
    return datetime(2026, 5, 1, h, m)


def ci(h, m=0, name=None, raw=None):
    return {"time": t(h, m), "log_type": "IN", "log_type_raw": raw or "Check In", "checkin_name": name}


def co(h, m=0, name=None, raw=None):
    return {"time": t(h, m), "log_type": "OUT", "log_type_raw": raw or "Check Out", "checkin_name": name}


class TestPairing(unittest.TestCase):
    def test_simple_pair(self):
        ck = [ci(9), co(18)]
        paired, anomalies = pair_checkins(ck, standard_seconds=8 * 3600, period_year=2026, period_month=5)
        assert len(paired) == 1
        assert paired[0]["total_seconds"] == 9 * 3600
        assert paired[0]["overtime_seconds"] == 1 * 3600
        assert anomalies == []

    def test_two_pairs_same_day(self):
        ck = [ci(9), co(13), ci(14), co(18)]
        paired, anomalies = pair_checkins(ck, 8 * 3600, 2026, 5)
        assert len(paired) == 2
        assert paired[0]["total_seconds"] == 4 * 3600
        assert paired[1]["total_seconds"] == 4 * 3600
        assert sum(p["total_seconds"] for p in paired) == 8 * 3600
        assert sum(p["overtime_seconds"] for p in paired) == 0

    def test_first_record_out_skipped_as_carryover(self):
        ck = [co(2), ci(9), co(18)]
        paired, anomalies = pair_checkins(ck, 8 * 3600, 2026, 5)
        assert len(paired) == 1
        assert paired[0]["total_seconds"] == 9 * 3600
        assert any(a["reason"] == "previous_month_carryover" for a in anomalies)

    def test_overnight_shift(self):
        ck = [ci(22), {"time": datetime(2026, 5, 2, 6, 0), "log_type": "OUT", "log_type_raw": "Check Out", "checkin_name": None}]
        paired, _ = pair_checkins(ck, 8 * 3600, 2026, 5)
        assert len(paired) == 1
        assert paired[0]["total_seconds"] == 8 * 3600
        assert paired[0]["shift_date"] == datetime(2026, 5, 1).date()

    def test_only_check_in_unpaired(self):
        ck = [ci(9)]
        paired, anomalies = pair_checkins(ck, 8 * 3600, 2026, 5)
        assert paired == []
        assert any("unpaired" in a["reason"] for a in anomalies)

    def test_seconds_from_hours_string_hhmm(self):
        assert seconds_from_hours("8:30") == 8 * 3600 + 30 * 60

    def test_seconds_from_hours_decimal(self):
        assert seconds_from_hours(8.5) == 8 * 3600 + 30 * 60
        assert seconds_from_hours(8) == 8 * 3600

    def test_default_8h_no_ot_when_worked_8h(self):
        ck = [ci(9), co(17)]
        paired, _ = pair_checkins(ck, 8 * 3600, 2026, 5)
        assert paired[0]["overtime_seconds"] == 0

    def test_default_8h_ot_when_worked_10h(self):
        ck = [ci(9), co(19)]
        paired, _ = pair_checkins(ck, 8 * 3600, 2026, 5)
        assert paired[0]["overtime_seconds"] == 2 * 3600

    def test_format_hhmm(self):
        assert format_hhmm(9 * 3600) == "9:00"
        assert format_hhmm(9 * 3600 + 30 * 60) == "9:30"
        assert format_hhmm(0) == "0:00"

    def test_period_filter_excludes_other_months(self):
        apr_in = {"time": datetime(2026, 4, 30, 22, 0), "log_type": "IN", "log_type_raw": "Check In", "checkin_name": None}
        may_out = {"time": datetime(2026, 5, 1, 6, 0), "log_type": "OUT", "log_type_raw": "Check Out", "checkin_name": None}
        ck = [apr_in, may_out]
        paired, _ = pair_checkins(ck, 8 * 3600, 2026, 5)
        assert paired == []
