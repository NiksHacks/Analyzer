import pytest
from datetime import date, timedelta, datetime # Ensure datetime is imported for strptime
from utils.helpers import parse_date_range
# No need for Flask app or MockRequestArgs if parse_date_range takes dict directly

def test_parse_date_range_last_7_days(app_context): # app_context is good practice
    today = date.today()
    expected_start = today - timedelta(days=6)
    expected_end = today
    args_dict = {'date_range': 'last_7_days'}

    start, end, err = parse_date_range(args_dict)
    assert err is None
    assert start == expected_start
    assert end == expected_end

def test_parse_date_range_last_30_days(app_context):
    today = date.today()
    expected_start = today - timedelta(days=29)
    expected_end = today
    args_dict = {'date_range': 'last_30_days'}
    start, end, err = parse_date_range(args_dict)
    assert err is None
    assert start == expected_start
    assert end == expected_end

def test_parse_date_range_custom_valid(app_context):
    start_str = "2023-01-01"
    end_str = "2023-01-15"
    expected_start = date(2023, 1, 1)
    expected_end = date(2023, 1, 15)
    args_dict = {'date_range': 'custom', 'start_date': start_str, 'end_date': end_str}
    start, end, err = parse_date_range(args_dict)
    assert err is None
    assert start == expected_start
    assert end == expected_end

def test_parse_date_range_custom_missing_dates(app_context):
    args_dict = {'date_range': 'custom'}
    start, end, err = parse_date_range(args_dict)
    assert err is not None
    assert err[0]['error'] == "Custom date range requires start_date and end_date."
    assert err[1] == 400

def test_parse_date_range_custom_invalid_format(app_context):
    args_dict = {'date_range': 'custom', 'start_date': 'invalid', 'end_date': '2023-01-15'}
    start, end, err = parse_date_range(args_dict)
    assert err is not None
    assert err[0]['error'] == "Invalid date format. Use YYYY-MM-DD."
    assert err[1] == 400

def test_parse_date_range_custom_start_after_end(app_context):
    args_dict = {'date_range': 'custom', 'start_date': '2023-01-15', 'end_date': '2023-01-01'}
    start, end, err = parse_date_range(args_dict)
    assert err is not None
    assert err[0]['error'] == "Start date cannot be after end date."
    assert err[1] == 400

def test_parse_date_range_invalid_range_str_defaults(app_context):
    today = date.today()
    expected_start_default = today - timedelta(days=6)
    expected_end_default = today
    args_dict = {'date_range': 'invalid_range_string'}
    start, end, err = parse_date_range(args_dict, default_range_str='last_7_days')
    assert err is None
    assert start == expected_start_default
    assert end == expected_end_default

def test_parse_date_range_previous_period_7_days(app_context):
    today = date.today()
    # For 'last_7_days', current period is (today-6, today)
    # Previous period should be (today-13, today-7)
    expected_prev_start = today - timedelta(days=13)
    expected_prev_end = today - timedelta(days=7)
    args_dict = {'date_range': 'last_7_days'}

    start, end, err = parse_date_range(args_dict, get_previous_period=True)
    assert err is None
    assert start == expected_prev_start
    assert end == expected_prev_end

def test_parse_date_range_previous_period_custom(app_context):
    # Custom current period: 2023-03-01 to 2023-03-10 (10 days)
    # Previous period should be 2023-02-19 to 2023-02-28 (10 days)
    args_dict = {'date_range': 'custom', 'start_date': '2023-03-01', 'end_date': '2023-03-10'}
    expected_prev_start = date(2023, 2, 19)
    expected_prev_end = date(2023, 2, 28)

    start, end, err = parse_date_range(args_dict, get_previous_period=True)
    assert err is None
    assert start == expected_prev_start
    assert end == expected_prev_end

def test_parse_date_range_previous_period_invalid_current(app_context):
    args_dict = {'date_range': 'custom', 'start_date': 'invalid'}
    start, end, err = parse_date_range(args_dict, get_previous_period=True)
    assert err is not None
    assert err[0]['error'] == "Cannot calculate previous period due to invalid current period dates."
    assert err[1] == 400
