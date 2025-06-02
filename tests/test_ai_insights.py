import pytest
from routes.ai_insights import calculate_campaign_metrics_from_row
from collections import namedtuple

# Define a mock row structure similar to what SQLAlchemy query result might provide
MockAggregatedRow = namedtuple('MockAggregatedRow',
                               ['total_spend', 'total_clicks', 'total_impressions', 'total_conversions', 'campaign_name_platform'])

def test_calculate_metrics_all_zeros():
    row = MockAggregatedRow(total_spend=0, total_clicks=0, total_impressions=0, total_conversions=0, campaign_name_platform="Test Campaign Zero")
    metrics = calculate_campaign_metrics_from_row(row)
    assert metrics['spend'] == 0
    assert metrics['clicks'] == 0
    assert metrics['impressions'] == 0
    assert metrics['conversions'] == 0
    assert metrics['ctr'] == 0.0
    assert metrics['cpc'] == 0.0
    assert metrics['cvr_clicks'] == 0.0
    assert metrics['cpa'] == 0.0
    assert metrics['campaign_name'] == "Test Campaign Zero"

def test_calculate_metrics_positive_values():
    row = MockAggregatedRow(total_spend=100.0, total_clicks=50, total_impressions=1000, total_conversions=5, campaign_name_platform="Test Campaign Positive")
    metrics = calculate_campaign_metrics_from_row(row)
    assert metrics['spend'] == 100.0
    assert metrics['clicks'] == 50
    assert metrics['impressions'] == 1000
    assert metrics['conversions'] == 5
    assert metrics['ctr'] == 5.0  # (50 / 1000) * 100
    assert metrics['cpc'] == 2.0  # 100.0 / 50
    assert metrics['cvr_clicks'] == 10.0 # (5 / 50) * 100
    assert metrics['cpa'] == 20.0 # 100.0 / 5
    assert metrics['campaign_name'] == "Test Campaign Positive"

def test_calculate_metrics_zero_impressions():
    row = MockAggregatedRow(total_spend=10.0, total_clicks=1, total_impressions=0, total_conversions=0, campaign_name_platform="Test Campaign No Impressions")
    metrics = calculate_campaign_metrics_from_row(row)
    assert metrics['ctr'] == 0.0

def test_calculate_metrics_zero_clicks():
    row = MockAggregatedRow(total_spend=10.0, total_clicks=0, total_impressions=100, total_conversions=0, campaign_name_platform="Test Campaign No Clicks")
    metrics = calculate_campaign_metrics_from_row(row)
    assert metrics['cpc'] == 0.0
    assert metrics['cvr_clicks'] == 0.0
    assert metrics['cpa'] == 0.0 # Also 0 since conversions is 0

def test_calculate_metrics_zero_conversions():
    row = MockAggregatedRow(total_spend=10.0, total_clicks=1, total_impressions=100, total_conversions=0, campaign_name_platform="Test Campaign No Conversions")
    metrics = calculate_campaign_metrics_from_row(row)
    assert metrics['cpa'] == 0.0

def test_calculate_metrics_none_row():
    # Test when no data is found for a period, and a campaign name override is provided
    metrics = calculate_campaign_metrics_from_row(None, campaign_name_override="Default Campaign Name")
    assert metrics['campaign_name'] == "Default Campaign Name"
    assert metrics['spend'] == 0
    assert metrics['ctr'] == 0.0
    assert metrics['cpc'] == 0.0
    assert metrics['cvr_clicks'] == 0.0
    assert metrics['cpa'] == 0.0

def test_calculate_metrics_none_row_no_override():
    # Test when no data is found and no override name
    metrics = calculate_campaign_metrics_from_row(None)
    assert metrics['campaign_name'] == "N/A (No data)"
    assert metrics['spend'] == 0

def test_calculate_metrics_row_with_none_values():
    # Test when the row object itself has None for metric fields
    row = MockAggregatedRow(total_spend=None, total_clicks=None, total_impressions=None, total_conversions=None, campaign_name_platform="Test Campaign Nones")
    metrics = calculate_campaign_metrics_from_row(row)
    assert metrics['spend'] == 0.0
    assert metrics['clicks'] == 0
    assert metrics['impressions'] == 0
    assert metrics['conversions'] == 0
    assert metrics['ctr'] == 0.0
    assert metrics['cpc'] == 0.0
    assert metrics['cvr_clicks'] == 0.0
    assert metrics['cpa'] == 0.0
    assert metrics['campaign_name'] == "Test Campaign Nones"

def test_calculate_metrics_row_with_none_campaign_name():
    row = MockAggregatedRow(total_spend=50.0, total_clicks=10, total_impressions=500, total_conversions=2, campaign_name_platform=None)
    metrics = calculate_campaign_metrics_from_row(row, campaign_name_override="Fallback Name")
    assert metrics['campaign_name'] == "Fallback Name"

def test_calculate_metrics_row_with_none_campaign_name_no_override():
    row = MockAggregatedRow(total_spend=50.0, total_clicks=10, total_impressions=500, total_conversions=2, campaign_name_platform=None)
    metrics = calculate_campaign_metrics_from_row(row)
    assert metrics['campaign_name'] == "Unknown Campaign"

def test_calculate_metrics_partial_none_values():
    # Spend and impressions are None, others are present
    row = MockAggregatedRow(total_spend=None, total_clicks=10, total_impressions=None, total_conversions=1, campaign_name_platform="Partial None Campaign")
    metrics = calculate_campaign_metrics_from_row(row)
    assert metrics['spend'] == 0.0
    assert metrics['clicks'] == 10
    assert metrics['impressions'] == 0
    assert metrics['conversions'] == 1
    assert metrics['ctr'] == 0.0 # Due to 0 impressions
    assert metrics['cpc'] == 0.0 # Due to 0 spend
    assert metrics['cvr_clicks'] == 10.0 # (1/10)*100
    assert metrics['cpa'] == 0.0 # Due to 0 spend
    assert metrics['campaign_name'] == "Partial None Campaign"


# --- Tests for analyze_campaign_spend_for_anomalies ---

# Import the refactored helper from the correct location
from routes.ai_insights import analyze_campaign_spend_for_anomalies
from datetime import date, timedelta

def test_analyze_spend_no_anomaly():
    baseline_spends = [100, 105, 95, 100, 102, 98, 100]
    recent_spend = 101
    anomaly = analyze_campaign_spend_for_anomalies(baseline_spends, recent_spend, "Campaign Stable", "MetaAds", date(2023,1,8))
    assert anomaly is None

def test_analyze_spend_high_anomaly():
    baseline_spends = [100, 105, 95, 100, 102, 98, 100] # mean approx 100
    recent_spend = 200 # Significantly higher
    anomaly = analyze_campaign_spend_for_anomalies(baseline_spends, recent_spend, "Campaign High Spend", "MetaAds", date(2023,1,8))
    assert anomaly is not None
    assert anomaly['direction'] == 'high'
    assert anomaly['value'] == 200

def test_analyze_spend_low_anomaly():
    baseline_spends = [100, 105, 95, 100, 102, 98, 100] # mean approx 100
    recent_spend = 10 # Significantly lower
    anomaly = analyze_campaign_spend_for_anomalies(baseline_spends, recent_spend, "Campaign Low Spend", "MetaAds", date(2023,1,8))
    assert anomaly is not None
    assert anomaly['direction'] == 'low'
    assert anomaly['value'] == 10

def test_analyze_spend_significant_drop_low_std():
    # Test the is_significant_drop condition: mean > 10 and recent_spend < (mean_spend * 0.1)
    baseline_spends = [100, 100, 100, 100, 100, 100, 100] # mean 100, std 0
    recent_spend = 5 # < 10% of mean
    anomaly = analyze_campaign_spend_for_anomalies(baseline_spends, recent_spend, "Campaign Drop", "MetaAds", date(2023,1,8))
    assert anomaly is not None
    assert anomaly['direction'] == 'low'
    assert "dropped significantly" in anomaly['message']

def test_analyze_spend_insufficient_baseline():
    baseline_spends = [100, 105, 95] # Only 3 days
    recent_spend = 100
    anomaly = analyze_campaign_spend_for_anomalies(baseline_spends, recent_spend, "Campaign Insufficient", "MetaAds", date(2023,1,8))
    assert anomaly is None

def test_analyze_spend_very_low_std_no_significant_drop():
    # std_dev will be very small, but recent_spend is not a significant drop relative to mean
    baseline_spends = [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0] # mean 10, std 0
    recent_spend = 9.8 # Small change, not a significant drop (>10% of mean)
    # The condition `std_dev_spend > (mean_spend * 0.05)` will be false (0 > 0.5 is false)
    # `is_significant_drop` will be false (mean_spend > 10 is false for mean=10, and 9.8 is not < 1)
    anomaly = analyze_campaign_spend_for_anomalies(baseline_spends, recent_spend, "Campaign Low Std", "MetaAds", date(2023,1,8))
    assert anomaly is None

def test_analyze_spend_zero_mean_baseline():
    baseline_spends = [0, 0, 0, 0, 0, 0, 0]
    recent_spend = 50 # Should be a high anomaly if std_dev is 0
    anomaly = analyze_campaign_spend_for_anomalies(baseline_spends, recent_spend, "Campaign Zero Mean", "MetaAds", date(2023,1,8))
    assert anomaly is not None
    assert anomaly['direction'] == 'high'

def test_analyze_spend_recent_spend_zero_from_positive_mean():
    baseline_spends = [100, 105, 95, 100, 102, 98, 100]
    recent_spend = 0
    anomaly = analyze_campaign_spend_for_anomalies(baseline_spends, recent_spend, "Campaign Spend To Zero", "MetaAds", date(2023,1,8))
    assert anomaly is not None
    assert anomaly['direction'] == 'low'
    assert "dropped significantly" in anomaly['message'] or "Significantly lower spend" in anomaly['message']


# --- Tests for calculate_metric_changes ---
from routes.ai_insights import calculate_metric_changes

def test_calculate_metric_changes_basic():
    all_keys = {('camp1', 'MetaAds'), ('camp2', 'GoogleAds')}
    current_map = {
        ('camp1', 'MetaAds'): {'name': 'Campaign Alpha', 'total': 150.0},
        ('camp2', 'GoogleAds'): {'name': 'Campaign Beta', 'total': 50.0}
    }
    prev_map = {
        ('camp1', 'MetaAds'): {'name': 'Campaign Alpha', 'total': 100.0},
        ('camp2', 'GoogleAds'): {'name': 'Campaign Beta', 'total': 75.0}
    }
    changes = calculate_metric_changes(all_keys, current_map, prev_map, 'spend')

    assert len(changes) == 2
    for change in changes:
        if change['campaign_id'] == 'camp1':
            assert change['absolute_change'] == 50.0
            assert change['percentage_change'] == 50.0
        elif change['campaign_id'] == 'camp2':
            assert change['absolute_change'] == -25.0
            assert change['percentage_change'] == -33.3 # -25/75 * 100
        assert change['metric'] == 'spend'

def test_calculate_metric_changes_new_campaign(): # Grew from zero
    all_keys = {('camp_new', 'MetaAds')}
    current_map = {('camp_new', 'MetaAds'): {'name': 'New Campaign', 'total': 100.0}}
    prev_map = {}
    changes = calculate_metric_changes(all_keys, current_map, prev_map, 'spend')

    assert len(changes) == 1
    change = changes[0]
    assert change['campaign_id'] == 'camp_new'
    assert change['previous_value'] == 0
    assert change['current_value'] == 100.0
    assert change['absolute_change'] == 100.0
    assert change['percentage_change'] == 100.0 # Or specific handling for "new"

def test_calculate_metric_changes_dropped_campaign(): # Dropped to zero
    all_keys = {('camp_old', 'GoogleAds')}
    current_map = {}
    prev_map = {('camp_old', 'GoogleAds'): {'name': 'Old Campaign', 'total': 200.0}}
    changes = calculate_metric_changes(all_keys, current_map, prev_map, 'spend')

    assert len(changes) == 1
    change = changes[0]
    assert change['campaign_id'] == 'camp_old'
    assert change['previous_value'] == 200.0
    assert change['current_value'] == 0
    assert change['absolute_change'] == -200.0
    assert change['percentage_change'] == -100.0

def test_calculate_metric_changes_both_zero():
    all_keys = {('camp_zero', 'MetaAds')}
    current_map = {('camp_zero', 'MetaAds'): {'name': 'Zero Campaign', 'total': 0}}
    prev_map = {('camp_zero', 'MetaAds'): {'name': 'Zero Campaign', 'total': 0}}
    changes = calculate_metric_changes(all_keys, current_map, prev_map, 'spend')

    assert len(changes) == 1
    change = changes[0]
    assert change['absolute_change'] == 0
    assert change['percentage_change'] == 0

def test_calculate_metric_changes_name_priority():
    # Ensure current name is prioritized if different from previous
    all_keys = {('camp1', 'MetaAds')}
    current_map = {('camp1', 'MetaAds'): {'name': 'Campaign Alpha New Name', 'total': 150.0}}
    prev_map = {('camp1', 'MetaAds'): {'name': 'Campaign Alpha Old Name', 'total': 100.0}}
    changes = calculate_metric_changes(all_keys, current_map, prev_map, 'spend')
    assert changes[0]['campaign_name'] == 'Campaign Alpha New Name'

    # Ensure previous name is used if not in current
    all_keys = {('camp_old', 'MetaAds')}
    current_map = {}
    prev_map = {('camp_old', 'MetaAds'): {'name': 'Old Campaign Name Only', 'total': 100.0}}
    changes = calculate_metric_changes(all_keys, current_map, prev_map, 'spend')
    assert changes[0]['campaign_name'] == 'Old Campaign Name Only'


# --- Tests for analyze_data_trend ---
from routes.ai_insights import analyze_data_trend
import numpy as np # Make sure numpy is imported for tests if used in helper

def test_analyze_data_trend_upward():
    data = [10, 20, 30, 40, 50]
    result = analyze_data_trend(data)
    assert result['trend_direction'] == "upward"
    assert result['slope'] > 0

def test_analyze_data_trend_downward():
    data = [50, 40, 30, 20, 10]
    result = analyze_data_trend(data)
    assert result['trend_direction'] == "downward"
    assert result['slope'] < 0

# --- Tests for generate_naive_forecast ---
from routes.ai_insights import generate_naive_forecast

def test_generate_naive_forecast_basic():
    historical_values = [10, 12, 11, 13, 14] # Avg = 12
    projection_days = 3
    last_hist_date = date(2023, 1, 5)

    result = generate_naive_forecast(historical_values, projection_days, last_hist_date)

    assert result['average_daily_value_used'] == 12.0
    assert len(result['forecast_data']) == 3
    assert result['forecast_data'][0]['date'] == '2023-01-06'
    assert result['forecast_data'][0]['projected_value'] == 12.0
    assert result['forecast_data'][1]['date'] == '2023-01-07'
    assert result['forecast_data'][1]['projected_value'] == 12.0
    assert result['forecast_data'][2]['date'] == '2023-01-08'
    assert result['forecast_data'][2]['projected_value'] == 12.0
    assert "average of the last 5 days" in result['message']

def test_generate_naive_forecast_empty_history():
    historical_values = []
    projection_days = 5
    last_hist_date = date(2023, 1, 1) # This date doesn't matter much if history is empty

    result = generate_naive_forecast(historical_values, projection_days, last_hist_date)

    assert result['average_daily_value_used'] == 0
    assert len(result['forecast_data']) == 0 # Should return empty forecast if no history
    assert "No historical data" in result['message']

def test_generate_naive_forecast_one_historical_day():
    historical_values = [100]
    projection_days = 2
    last_hist_date = date(2023, 2, 1)

    result = generate_naive_forecast(historical_values, projection_days, last_hist_date)

    assert result['average_daily_value_used'] == 100.0
    assert len(result['forecast_data']) == 2
    assert result['forecast_data'][0]['date'] == '2023-02-02'
    assert result['forecast_data'][0]['projected_value'] == 100.0
    assert result['forecast_data'][1]['date'] == '2023-02-03'
    assert result['forecast_data'][1]['projected_value'] == 100.0

def test_generate_naive_forecast_zero_projection_days():
    historical_values = [10, 20, 30]
    projection_days = 0
    last_hist_date = date(2023, 3, 1)

    result = generate_naive_forecast(historical_values, projection_days, last_hist_date)

    assert result['average_daily_value_used'] == 20.0
    assert len(result['forecast_data']) == 0
    assert "average of the last 3 days" in result['message']

def test_analyze_data_trend_flat():
    data = [30, 30, 30, 30, 30]
    result = analyze_data_trend(data)
    assert result['trend_direction'] == "flat"
    assert abs(result['slope']) < 1e-5 # Slope should be very close to 0

def test_analyze_data_trend_flat_minor_variations():
    # avg is ~30. slope should be small. 2% of 30 is 0.6. Slope here will be << 0.6
    data = [29.8, 30.1, 29.9, 30.2, 30.0]
    result = analyze_data_trend(data)
    assert result['trend_direction'] == "flat"

def test_analyze_data_trend_insufficient_data():
    data = [10, 20]
    result = analyze_data_trend(data)
    assert result['trend_direction'] == "insufficient_data"
    assert result['slope'] == 0

def test_analyze_data_trend_zero_avg_upward():
    data = [-1, 0, 1] # Avg is 0
    result = analyze_data_trend(data)
    assert result['trend_direction'] == "upward"
    assert result['slope'] > 0

def test_analyze_data_trend_zero_avg_downward():
    data = [1, 0, -1] # Avg is 0
    result = analyze_data_trend(data)
    assert result['trend_direction'] == "downward"
    assert result['slope'] < 0
