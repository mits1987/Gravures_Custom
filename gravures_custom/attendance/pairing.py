"""
Pure pairing logic for Employee Shifts.
Testable without Frappe context.
Mirrors user's existing pandas script logic exactly.
"""
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional


def seconds_from_hours(hours_value) -> int:
    """Convert '8:30' or 8.5 or 8 to seconds. Mirrors load_employee_hours() in user's script."""
    if isinstance(hours_value, str) and ':' in hours_value:
        h, m = map(int, hours_value.split(':'))
        return h * 3600 + m * 60
    elif isinstance(hours_value, str):
        return int(float(hours_value) * 3600)
    else:
        return int(hours_value * 3600)


def pair_checkins(
    sorted_checkins: List[Dict],
    standard_seconds: int,
    period_year: int,
    period_month: int,
) -> Tuple[List[Dict], List[Dict]]:
    """
    Pair IN/OUT checkins alternately. Mirrors process_attendance_data().

    sorted_checkins: list of dicts with keys:
        - time: datetime
        - log_type: "IN" or "OUT"
        - checkin_name: str

    Returns: (paired_shifts, anomalies)

    Each paired_shift dict:
        - check_in_time, check_out_time, total_seconds, overtime_seconds,
          shift_date, check_in_name, check_out_name

    Each anomaly dict:
        - time, log_type, reason, checkin_name
    """
    paired = []
    anomalies = []

    # FIRST-RECORD-OUT rule: skip first if it's OUT (carryover from prev month)
    if sorted_checkins and sorted_checkins[0]["log_type"] == "OUT":
        skipped = sorted_checkins.pop(0)
        anomalies.append({
            "time": skipped["time"],
            "log_type": skipped["log_type"],
            "reason": "previous_month_carryover",
            "checkin_name": skipped.get("checkin_name"),
        })
        if not sorted_checkins:
            return paired, anomalies

    # pair alternately
    i = 0
    while i < len(sorted_checkins) - 1:
        cur = sorted_checkins[i]
        nxt = sorted_checkins[i + 1]

        if cur["log_type"] == "IN" and nxt["log_type"] == "OUT":
            check_in = cur["time"]
            check_out = nxt["time"]

            # Only include if IN is in target month (matches user's `if check_in.month == process_month`)
            if check_in.year == period_year and check_in.month == period_month:
                total_seconds = int((check_out - check_in).total_seconds())
                overtime_seconds = max(0, total_seconds - standard_seconds)
                paired.append({
                    "check_in_time": check_in,
                    "check_out_time": check_out,
                    "check_in_name": cur.get("checkin_name"),
                    "check_out_name": nxt.get("checkin_name"),
                    "total_seconds": total_seconds,
                    "overtime_seconds": overtime_seconds,
                    "shift_date": check_in.date(),
                })
            i += 2
        else:
            anomalies.append({
                "time": cur["time"],
                "log_type": cur["log_type"],
                "reason": f"unpaired_current_{cur['log_type']}",
                "checkin_name": cur.get("checkin_name"),
            })
            i += 1

    # Last unpaired record
    if i < len(sorted_checkins):
        last = sorted_checkins[i]
        anomalies.append({
            "time": last["time"],
            "log_type": last["log_type"],
            "reason": f"unpaired_final_{last['log_type'].lower()}",
            "checkin_name": last.get("checkin_name"),
        })

    return paired, anomalies


def format_hhmm(seconds: int) -> str:
    """Format seconds as HH:MM (no seconds)."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{hours}:{minutes:02d}"
