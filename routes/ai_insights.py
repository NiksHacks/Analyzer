from flask import Blueprint, jsonify, current_app, request, render_template
from flask_login import login_required, current_user
from ..models import CampaignData, AdPlatformIntegration, PlatformNameEnum, IntegrationStatusEnum, db
from datetime import datetime, date, timedelta
from sqlalchemy import func
import numpy as np

# Blueprint for AI-powered insights and analysis routes.
# This blueprint groups functionalities related to automated data analysis,
# anomaly detection, trend identification, and forecasting.
ai_insights_bp = Blueprint('ai_insights', __name__, url_prefix='/ai-insights')

# --- Anomaly Detection Helper Function ---
# This function is responsible for the core logic of detecting anomalies in campaign spend.
def analyze_campaign_spend_for_anomalies(daily_spends_list, most_recent_day_spend, campaign_name, platform_name, anomaly_check_date):
    """
    Analyzes a list of historical daily spends for a specific campaign to detect
    if the most recent day's spend is anomalous (significantly higher or lower).

    The anomaly detection is based on comparing the most recent spend to the mean
    and standard deviation of a baseline period. A spend is considered anomalous if it
    exceeds a certain number of standard deviations from the mean, or if there's a
    significant percentage drop from a non-trivial mean spend.

    Args:
        daily_spends_list (list): A list of floats representing daily spend values
                                  for the baseline period (e.g., last 29 days).
        most_recent_day_spend (float): The spend value for the most recent day being checked
                                       (e.g., yesterday's spend).
        campaign_name (str): The name of the campaign being analyzed.
        platform_name (str): The platform of the campaign (e.g., 'GOOGLE_ADS', 'META_ADS').
        anomaly_check_date (date): The date for which the anomaly is being checked (i.e., the date
                                   of `most_recent_day_spend`).

    Returns:
        dict or None: A dictionary describing the anomaly if one is detected, otherwise None.
                      The dictionary structure for an anomaly includes:
                      - 'campaign_name': Name of the campaign.
                      - 'platform': Platform name.
                      - 'anomaly_date': Date of the anomalous spend (ISO format).
                      - 'value': The actual spend value on the anomaly_date.
                      - 'mean': Mean spend of the baseline period.
                      - 'stddev': Standard deviation of spend in the baseline period.
                      - 'direction': Direction of the anomaly ('high' or 'low').
                      - 'message': A descriptive message about the anomaly.
    """
    # Require a minimum number of data points (e.g., 7 days) for a meaningful statistical baseline.
    if len(daily_spends_list) < 7:
        current_app.logger.debug(f"Not enough baseline data ({len(daily_spends_list)} days) for campaign '{campaign_name}' to detect spend anomalies. Minimum 7 days required.")
        return None # Not enough data to perform analysis.

    # Calculate mean and standard deviation of the baseline spends using numpy for efficiency.
    mean_spend = np.mean(daily_spends_list)
    std_dev_spend = np.std(daily_spends_list)

    # Define the threshold for anomaly detection in terms of standard deviations.
    # A value of 2.5 means spends beyond 2.5 std devs from the mean are considered anomalous.
    threshold_std_devs = 2.5
    # Define a condition for what constitutes a "significant drop" in percentage terms,
    # especially if the standard deviation method doesn't catch it (e.g., for very stable spends that suddenly drop).
    # This checks if average spend was notable (e.g., > $10) and current spend is less than 10% of that average.
    is_significant_drop = mean_spend > 10 and most_recent_day_spend < (mean_spend * 0.1)

    message = None    # Initialize anomaly message.
    direction = None  # Initialize anomaly direction ('high' or 'low').

    # Anomaly detection logic:
    # Condition 1: Standard Deviation Method.
    # Check for deviations if standard deviation is itself somewhat significant (e.g., >5% of mean),
    # or if the mean spend is zero (in which case any spend is a deviation).
    # This helps avoid flagging tiny, insignificant deviations as anomalies for very stable, low-spend campaigns.
    if std_dev_spend > (mean_spend * 0.05) or mean_spend == 0:
        # Check for significantly higher spend.
        if most_recent_day_spend > mean_spend + (threshold_std_devs * std_dev_spend):
            direction = 'high'
            message = f"Significantly higher spend ({most_recent_day_spend:.2f}) on {anomaly_check_date.strftime('%b %d')} compared to the {len(daily_spends_list)}-day average ({mean_spend:.2f}). Historical standard deviation was {std_dev_spend:.2f}."
        # Check for significantly lower spend.
        elif most_recent_day_spend < mean_spend - (threshold_std_devs * std_dev_spend):
            direction = 'low'
            message = f"Significantly lower spend ({most_recent_day_spend:.2f}) on {anomaly_check_date.strftime('%b %d')} compared to the {len(daily_spends_list)}-day average ({mean_spend:.2f}). Historical standard deviation was {std_dev_spend:.2f}."
    # Condition 2: Significant Percentage Drop Method.
    # This catches cases where spend was stable (low std_dev_spend relative to mean) but then dropped drastically.
    elif is_significant_drop:
        direction = 'low'
        message = f"Spend dropped significantly to {most_recent_day_spend:.2f} on {anomaly_check_date.strftime('%b %d')} from an average of {mean_spend:.2f} over the prior {len(daily_spends_list)} days."

    # If an anomaly message was generated (meaning an anomaly was detected),
    # construct and return the anomaly details dictionary.
    if message:
        return {
            'campaign_name': campaign_name,
            'platform': platform_name,
            'anomaly_date': anomaly_check_date.isoformat(),
            'value': most_recent_day_spend,
            'mean': round(mean_spend, 2),
            'stddev': round(std_dev_spend, 2),
            'direction': direction,
            'message': message
        }
    return None # No anomaly detected based on the defined criteria.

# API Endpoint for Spend Anomaly Detection by Campaign.
@ai_insights_bp.route('/api/anomaly/spend_by_campaign')
@login_required # User must be logged in to access this insight.
def get_spend_anomalies():
    """
    Identifies campaigns with anomalous spending on the most recent full day of data (yesterday)
    by comparing yesterday's spend to a baseline period (e.g., the 29 days prior to yesterday).

    This endpoint fetches relevant campaign data, groups it by campaign and platform,
    and then uses the `analyze_campaign_spend_for_anomalies` helper function to check each one.

    Returns:
        JSON: A list of anomaly objects. Each object details a detected spend anomaly,
              including campaign name, platform, date of anomaly, spend value, historical mean/stddev,
              direction of anomaly, and a descriptive message.
              Returns an empty list if no anomalies are found or if data is insufficient.
    """
    user_id = current_user.id # Get the ID of the currently logged-in user.
    today = date.today()

    # Define the date for which anomalies are being checked (typically yesterday for complete data).
    end_date_for_calc = today - timedelta(days=1)
    # Define the start date for the baseline period used for calculating historical mean/stddev.
    # E.g., if checking yesterday, baseline is the 29 days before that.
    start_date_for_baseline = end_date_for_calc - timedelta(days=29)

    anomalies_found = [] # Initialize an empty list to store detected anomalies.

    # --- Step 1: Fetch campaign data for the baseline period + the anomaly check date ---
    # Query the CampaignData table for all relevant entries for the current user's active integrations.
    # Data includes campaign identifiers, platform, date, and spend.
    # It's ordered by campaign and date to make subsequent grouping easier.
    campaigns_data = db.session.query(
        CampaignData.campaign_id_platform,
        CampaignData.campaign_name_platform,
        CampaignData.platform,
        CampaignData.date,
        CampaignData.spend
    ).join(AdPlatformIntegration).filter( # Join with AdPlatformIntegration to filter by user and status.
        AdPlatformIntegration.user_id == user_id,
        AdPlatformIntegration.status == IntegrationStatusEnum.ACTIVE, # Only consider active integrations.
        CampaignData.date >= start_date_for_baseline, # Filter for data within the defined date range.
        CampaignData.date <= end_date_for_calc
    ).order_by(CampaignData.campaign_id_platform, CampaignData.platform, CampaignData.date).all()

    # If no campaign data is found for the user in the specified period, return an empty list.
    if not campaigns_data:
        current_app.logger.info(f"No campaign data found for user {user_id} between {start_date_for_baseline} and {end_date_for_calc} for spend anomaly detection.")
        return jsonify([])

    # --- Step 2: Group the fetched data by campaign and platform ---
    # This creates a dictionary where each key is a (campaign_id_platform, platform_value) tuple,
    # and each value is a dictionary containing the campaign name, platform, and a list of its daily spend metrics.
    data_by_campaign = {}
    for row in campaigns_data:
        key = (row.campaign_id_platform, row.platform.value)
        if key not in data_by_campaign: # If this campaign-platform combination is new, initialize its entry.
            data_by_campaign[key] = {
                'name': row.campaign_name_platform,
                'platform': row.platform.value,
                'metrics': []
            }
        # Append the current row's date and spend to the metrics list for this campaign.
        data_by_campaign[key]['metrics'].append({'date': row.date, 'spend': float(row.spend)})

    # --- Step 3: Analyze each campaign's grouped data for anomalies ---
    for key, campaign_info in data_by_campaign.items():
        # The metrics should already be sorted by date due to the SQL query's ORDER BY clause.
        # If not, an explicit sort: metrics = sorted(campaign_info['metrics'], key=lambda x: x['date'])
        metrics = campaign_info['metrics']
        if not metrics: # Should not happen if data_by_campaign was populated correctly.
            continue

        # Extract the spend data for the specific day being checked for an anomaly (end_date_for_calc).
        most_recent_metric_day_data = None
        # Iterate backwards through metrics list for efficiency, assuming the target date is likely at the end.
        for m_idx in range(len(metrics) -1, -1, -1):
            if metrics[m_idx]['date'] == end_date_for_calc:
                most_recent_metric_day_data = metrics[m_idx]
                break

        # If no data exists for the campaign on the anomaly_check_date, skip this campaign.
        if not most_recent_metric_day_data:
            current_app.logger.debug(f"No spend data for campaign '{campaign_info['name']}' (Platform: {campaign_info['platform']}) on anomaly check date {end_date_for_calc}. Skipping anomaly check for this campaign.")
            continue

        recent_spend = most_recent_metric_day_data['spend'] # The spend value for the day being analyzed.
        # Collect spend values for the baseline period (all days *before* the anomaly_check_date).
        baseline_spends = [m['spend'] for m in metrics if m['date'] < end_date_for_calc]

        # Call the helper function to perform the anomaly analysis.
        anomaly = analyze_campaign_spend_for_anomalies(
            daily_spends_list=baseline_spends,
            most_recent_day_spend=recent_spend,
            campaign_name=campaign_info['name'],
            platform_name=campaign_info['platform'],
            anomaly_check_date=end_date_for_calc
        )
        if anomaly: # If the helper function returns an anomaly object, add it to our list.
            anomalies_found.append(anomaly)

    # Return the list of all detected anomalies as a JSON response.
    return jsonify(anomalies_found)


# --- Top Movers Helper ---
# This helper function calculates the absolute and percentage changes for a given metric
# for a list of campaigns, comparing data from two periods.
def calculate_metric_changes(all_campaign_keys, current_data_map, prev_data_map, metric_param_name):
    """
    Calculates absolute and percentage changes for a given metric across all specified campaigns
    between a current period and a previous period. This function is used by the 'get_top_movers'
    endpoint to determine which campaigns have changed the most.

    Args:
        all_campaign_keys (set): A set of unique (campaign_id_platform, platform_name_value) tuples.
                                 These represent all campaigns that were active in either the current
                                 or previous period, ensuring all relevant campaigns are considered.
        current_data_map (dict): A dictionary mapping campaign keys (tuple) to their metric data
                                 (e.g., {'name': 'Campaign A', 'total': 100.0}) for the current period.
        prev_data_map (dict): A dictionary similar to current_data_map, but for the previous period.
        metric_param_name (str): The name of the metric being analyzed (e.g., 'spend', 'clicks'),
                                 used for labeling in the output.

    Returns:
        list: A list of dictionaries. Each dictionary represents a campaign and includes:
              - 'campaign_id': The platform-specific ID of the campaign.
              - 'campaign_name': The name of the campaign.
              - 'platform': The advertising platform (e.g., 'GOOGLE_ADS').
              - 'current_value': The metric's total value in the current period.
              - 'previous_value': The metric's total value in the previous period.
              - 'absolute_change': The absolute difference (current - previous).
              - 'percentage_change': The percentage change from previous to current.
              - 'metric': The name of the metric analyzed.
    """
    changes = [] # Initialize a list to store the calculated changes for each campaign.

    # Iterate through all unique campaign-platform combinations found in either period.
    for key in all_campaign_keys:
        # Retrieve the total metric value for the current period, defaulting to 0 if the campaign had no data.
        current_val = current_data_map.get(key, {}).get('total', 0)
        # Retrieve the total metric value for the previous period, defaulting to 0.
        prev_val = prev_data_map.get(key, {}).get('total', 0)

        # Determine the campaign name. Prioritize name from current data, then previous, then use ID as fallback.
        campaign_name = current_data_map.get(key, {}).get('name') or \
                        prev_data_map.get(key, {}).get('name') or \
                        f"Campaign ID: {key[0]}" # key[0] is campaign_id_platform
        platform_name = key[1] # key[1] is platform.value

        # Calculate absolute change: current period value minus previous period value.
        abs_change = current_val - prev_val

        # Calculate percentage change.
        if prev_val != 0: # Standard percentage change formula if previous value is not zero.
            perc_change = (abs_change / prev_val) * 100
        elif current_val != 0: # If previous value was 0 and current is non-zero, it's a 100% increase (or effectively infinite if considering from 0).
            perc_change = 100.0
        else: # If both previous and current values are 0, there's no change.
            perc_change = 0.0

        # Append a dictionary containing all relevant details for this campaign's change.
        changes.append({
            'campaign_id': key[0],
            'campaign_name': campaign_name,
            'platform': platform_name,
            'current_value': round(current_val, 2),
            'previous_value': round(prev_val, 2),
            'absolute_change': round(abs_change, 2),
            'percentage_change': round(perc_change, 1),
            'metric': metric_param_name
        })
    return changes # Return the list of campaign changes.

# API Endpoint for Top Movers (Campaigns with largest metric changes).
@ai_insights_bp.route('/api/top_movers')
@login_required # User must be logged in.
def get_top_movers():
    """
    Identifies campaigns with the largest positive (gainers) and negative (decliners)
    changes for a specified metric (e.g., spend, clicks, conversions).
    The comparison can be 'day-over-day' (dod) or 'week-over-week' (wow).

    Query Parameters:
        metric (str, optional): The metric to analyze. Defaults to 'spend'.
                                Supported: 'spend', 'clicks', 'impressions', 'conversions'.
        period (str, optional): The comparison period. 'dod' for Day-over-Day,
                                'wow' for Week-over-Week. Defaults to 'wow'.
    Returns:
        JSON: A dictionary containing:
              - 'gainers' (list): Top 5 campaigns with the largest positive absolute change.
              - 'decliners' (list): Top 5 campaigns with the largest negative absolute change.
              - 'metric' (str): The metric analyzed.
              - 'period' (str): The comparison period used.
    """
    user_id = current_user.id
    # --- Step 1: Get and validate request parameters ---
    metric_param = request.args.get('metric', 'spend') # Metric to analyze (e.g., 'spend').
    period_param = request.args.get('period', 'wow')   # Comparison period ('dod' or 'wow').

    # --- Step 2: Define current and previous date periods based on 'period_param' ---
    today = date.today()
    if period_param == 'dod': # Day-over-Day comparison.
        current_period_end = today - timedelta(days=1)    # Yesterday.
        current_period_start = current_period_end          # A single day period.
        prev_period_end = today - timedelta(days=2)      # The day before yesterday.
        prev_period_start = prev_period_end              # A single day period.
    else: # Default to Week-over-Week (wow).
        current_period_end = today - timedelta(days=1)                # End of the current week (yesterday).
        current_period_start = current_period_end - timedelta(days=6) # Start of the current week (7 days total).
        prev_period_end = current_period_start - timedelta(days=1)    # End of the previous week.
        prev_period_start = prev_period_end - timedelta(days=6)      # Start of the previous week (7 days total).

    # --- Step 3: Validate the requested metric and get its corresponding model attribute ---
    metric_attr = getattr(CampaignData, metric_param, None) # Safely get attribute from CampaignData model.
    if not metric_attr: # If the metric_param string doesn't match a valid attribute.
        err_msg = f"Invalid metric specified: '{metric_param}'. Valid options are 'spend', 'clicks', 'impressions', 'conversions'."
        current_app.logger.warning(f"Bad request to {request.path}: {err_msg} (Params: {request.args})")
        return jsonify({"error": err_msg}), 400 # Return 400 Bad Request.

    # --- Step 4: Helper function to query summed metric data for a given period ---
    def get_period_sum(start_dt, end_dt):
        """
        Queries the database for summed metric data for all campaigns of the current user
        within the specified date range.
        """
        return db.session.query(
            CampaignData.campaign_id_platform,
            CampaignData.campaign_name_platform,
            CampaignData.platform,
            func.sum(metric_attr).label('metric_total') # Sum the specified metric.
        ).join(AdPlatformIntegration).filter( # Ensure data is for the current user and active integrations.
            AdPlatformIntegration.user_id == user_id,
            AdPlatformIntegration.status == IntegrationStatusEnum.ACTIVE,
            CampaignData.date >= start_dt, # Apply date range filter.
            CampaignData.date <= end_dt
        ).group_by( # Group results by campaign to sum metrics per campaign.
            CampaignData.campaign_id_platform,
            CampaignData.campaign_name_platform,
            CampaignData.platform
        ).all()

    # --- Step 5: Fetch aggregated data for both current and previous periods ---
    current_period_data_rows = get_period_sum(current_period_start, current_period_end)
    prev_period_data_rows = get_period_sum(prev_period_start, prev_period_end)

    # --- Step 6: Transform query results into dictionaries (maps) for easier processing ---
    # The key is a tuple (campaign_id_platform, platform_value), value is a dict with name and total.
    current_data_map = {(row.campaign_id_platform, row.platform.value): {'name': row.campaign_name_platform, 'total': float(row.metric_total or 0)} for row in current_period_data_rows}
    prev_data_map = {(row.campaign_id_platform, row.platform.value): {'name': row.campaign_name_platform, 'total': float(row.metric_total or 0)} for row in prev_period_data_rows}

    # --- Step 7: Identify all unique campaign-platform combinations present in either period ---
    all_campaign_keys = set(current_data_map.keys()) | set(prev_data_map.keys())

    # --- Step 8: Calculate metric changes for all identified campaigns ---
    changes = calculate_metric_changes(all_campaign_keys, current_data_map, prev_data_map, metric_param)

    # --- Step 9: Sort campaigns by absolute change to find top gainers and decliners ---
    # Sort in descending order of absolute_change to easily pick top positive changes.
    sorted_by_abs_change = sorted(changes, key=lambda x: x['absolute_change'], reverse=True)

    # Top 5 gainers: campaigns with positive absolute change.
    gainers = [c for c in sorted_by_abs_change if c['absolute_change'] > 0][:5]
    # Top 5 decliners: campaigns with negative absolute change, sorted to show most negative first.
    decliners = sorted([c for c in sorted_by_abs_change if c['absolute_change'] < 0], key=lambda x: x['absolute_change'])[:5]

    # --- Step 10: Return the results in JSON format ---
    return jsonify({'gainers': gainers, 'decliners': decliners, 'metric': metric_param, 'period': period_param})

# Route for the main AI Insights page.
@ai_insights_bp.route('/')
@login_required # User must be logged in to access AI insights.
# @subscription_required(required_level_names=['Pro', 'Enterprise']) # Example: Could be restricted by subscription.
def insights_page():
    """
    Renders the main page for the AI Insights section.
    This page might display a dashboard of various AI-driven analyses or provide
    navigation to different insight tools.
    """
    # The template 'ai_insights/engine.html' is expected to contain the UI for this page.
    # It will likely make AJAX calls to the API endpoints within this blueprint to fetch and display insights.
    return render_template('ai_insights/engine.html')


# --- Campaign Scorecard Helper & Endpoint ---

# Helper function to calculate various performance metrics from aggregated campaign data.
# This function was likely refactored and commented previously, but ensuring its context here.
def calculate_campaign_metrics_from_row(aggregated_row_data, campaign_name_override=None):
    """
    Calculates standard advertising metrics (CTR, CPC, CVR, CPA) from a row of
    aggregated campaign data (spend, clicks, impressions, conversions).

    Args:
        aggregated_row_data (sqlalchemy.engine.row.Row or similar):
            A data object (e.g., from a SQLAlchemy query result) that should have attributes like
            'total_spend', 'total_clicks', 'total_impressions', 'total_conversions',
            and optionally 'campaign_name_platform'.
        campaign_name_override (str, optional):
            A name to use for the campaign if `aggregated_row_data` does not provide one
            or if an override is desired. Defaults to None.

    Returns:
        dict: A dictionary containing the calculated metrics:
              - 'campaign_name'
              - 'spend', 'clicks', 'impressions', 'conversions' (as provided or 0)
              - 'ctr' (Click-Through Rate, percentage)
              - 'cpc' (Cost Per Click)
              - 'cvr_clicks' (Conversion Rate from Clicks, percentage)
              - 'cpa' (Cost Per Acquisition/Conversion)
              All monetary values are rounded to 2 decimal places, rates to 2 decimal places.
    """
    # If no data row is provided, return a dictionary with default (zeroed or N/A) metric values.
    if not aggregated_row_data:
        return {
            "campaign_name": campaign_name_override or "N/A (No data provided)",
            "spend": 0, "clicks": 0, "impressions": 0, "conversions": 0,
            "ctr": 0, "cpc": 0, "cvr_clicks": 0, "cpa": 0
        }

    # Safely extract base metrics, defaulting to 0 if attribute is missing or None.
    spend = float(getattr(aggregated_row_data, 'total_spend', 0) or 0)
    clicks = int(getattr(aggregated_row_data, 'total_clicks', 0) or 0)
    impressions = int(getattr(aggregated_row_data, 'total_impressions', 0) or 0)
    conversions = int(getattr(aggregated_row_data, 'total_conversions', 0) or 0)

    # Determine campaign name using override, then data row, then a generic default.
    campaign_name = getattr(aggregated_row_data, 'campaign_name_platform', None) or \
                    campaign_name_override or \
                    "Unknown Campaign"

    # Calculate derived metrics, handling potential division by zero.
    # Click-Through Rate (CTR) = (Clicks / Impressions) * 100
    ctr = (clicks / impressions) * 100 if impressions > 0 else 0.0
    # Cost Per Click (CPC) = Spend / Clicks
    cpc = spend / clicks if clicks > 0 else 0.0
    # Conversion Rate (CVR) from Clicks = (Conversions / Clicks) * 100
    cvr_clicks = (conversions / clicks) * 100 if clicks > 0 else 0.0
    # Cost Per Acquisition/Conversion (CPA) = Spend / Conversions
    cpa = spend / conversions if conversions > 0 else 0.0

    # Return all metrics in a structured dictionary.
    return {
        "campaign_name": campaign_name,
        "spend": round(spend, 2), "clicks": clicks, "impressions": impressions, "conversions": conversions,
        "ctr": round(ctr, 2), "cpc": round(cpc, 2), "cvr_clicks": round(cvr_clicks, 2), "cpa": round(cpa, 2)
    }

# API Endpoint for Campaign Scorecard.
# API Endpoint for Campaign Scorecard.
@ai_insights_bp.route('/api/campaign_scorecard')
@login_required # User must be logged in.
def get_campaign_scorecard():
    """
    Generates a "scorecard" for a specific campaign, comparing its performance
    over a recent period (e.g., last 14 days) against a baseline period
    (e.g., the 30 days prior to the recent period).
    It calculates key metrics (CTR, CPC, CVR, CPA) for both periods and provides
    observations on changes, highlighting significant positive or negative shifts.

    Query Parameters:
        campaign_id_platform (str): The platform-specific ID of the campaign to analyze. Required.
        platform (str): The ad platform ('MetaAds' or 'GoogleAds'). Required.

    Returns:
        JSON: A scorecard object containing:
              - Campaign name and platform.
              - Summaries of metrics for the recent and baseline periods.
              - A list of "observations" detailing changes in key metrics,
                their status (positive, negative, neutral), and a descriptive message.
    """
    user_id = current_user.id
    # --- Step 1: Get and validate required request parameters ---
    campaign_id_platform_str = request.args.get('campaign_id_platform')
    platform_str = request.args.get('platform')

    # Ensure both campaign ID and platform are provided.
    if not campaign_id_platform_str or not platform_str:
        err_msg = "'campaign_id_platform' and 'platform' parameters are required for the scorecard."
        current_app.logger.warning(f"Bad request to {request.path}: {err_msg} (Params: {request.args})")
        return jsonify({"error": err_msg}), 400 # Return 400 Bad Request.

    # Validate and convert platform string to PlatformNameEnum.
    try:
        platform_enum = PlatformNameEnum[platform_str.upper()]
    except KeyError: # Handle invalid platform string.
        err_msg = f"Invalid platform specified: '{platform_str}'. Use 'MetaAds' or 'GoogleAds'."
        current_app.logger.warning(f"Bad request to {request.path}: {err_msg} (Params: {request.args})")
        return jsonify({"error": err_msg}), 400

    # --- Step 2: Define date ranges for recent and baseline periods ---
    today = date.today()
    # Recent period: e.g., data from yesterday going back 14 days.
    recent_period_end = today - timedelta(days=1)
    recent_period_start = recent_period_end - timedelta(days=13) # 14-day recent period.

    # Baseline period: e.g., data for 30 days prior to the start of the recent period.
    baseline_period_end = recent_period_start - timedelta(days=1)
    baseline_period_start = baseline_period_end - timedelta(days=29) # 30-day baseline period.

    # --- Step 3: Helper function to fetch aggregated data for a specified period ---
    def fetch_aggregated_data_for_period(user_id_arg, platform_name_enum_arg, campaign_id_str_arg, start_date_obj_arg, end_date_obj_arg):
        """
        Queries the database for aggregated spend, clicks, impressions, and conversions
        for a specific campaign, platform, and date range.
        It uses 'overall' breakdown data for daily totals.
        """
        metrics_query = db.session.query(
            func.sum(CampaignData.spend).label('total_spend'),
            func.sum(CampaignData.clicks).label('total_clicks'),
            func.sum(CampaignData.impressions).label('total_impressions'),
            func.sum(CampaignData.conversions).label('total_conversions'),
            CampaignData.campaign_name_platform # Also fetch name for consistency.
        ).join(AdPlatformIntegration).filter( # Join to filter by user and active integrations.
            AdPlatformIntegration.user_id == user_id_arg,
            AdPlatformIntegration.status == IntegrationStatusEnum.ACTIVE,
            AdPlatformIntegration.platform_name == platform_name_enum_arg, # Ensure integration platform matches.
            CampaignData.platform == platform_name_enum_arg,           # Filter by platform in CampaignData.
            CampaignData.campaign_id_platform == campaign_id_str_arg, # Filter by specific campaign ID.
            CampaignData.date >= start_date_obj_arg,                 # Apply date range.
            CampaignData.date <= end_date_obj_arg,
            CampaignData.breakdown_type == 'overall' # Use 'overall' records for campaign totals.
        ).group_by(CampaignData.campaign_name_platform) # Group by name to ensure one row if name varies slightly over time (should not happen with good data).
        return metrics_query.first() # Expect one aggregated row or None.

    # --- Step 4: Fetch data for both recent and baseline periods ---
    raw_recent_metrics_row = fetch_aggregated_data_for_period(user_id, platform_enum, campaign_id_platform_str, recent_period_start, recent_period_end)
    raw_baseline_metrics_row = fetch_aggregated_data_for_period(user_id, platform_enum, campaign_id_platform_str, baseline_period_start, baseline_period_end)

    # --- Step 5: Determine the campaign name for the report ---
    # Try to get the most current campaign name from various sources.
    campaign_name_for_report = f"Campaign {campaign_id_platform_str}" # Default if no name found.
    # Attempt to get name from a recent CampaignData entry (could be more up-to-date).
    campaign_data_sample = CampaignData.query.join(AdPlatformIntegration).filter(
       AdPlatformIntegration.user_id == user_id,
       AdPlatformIntegration.platform_name == platform_enum,
       CampaignData.campaign_id_platform == campaign_id_platform_str,
       AdPlatformIntegration.status == IntegrationStatusEnum.ACTIVE
    ).order_by(CampaignData.date.desc()).first()

    if campaign_data_sample and campaign_data_sample.campaign_name_platform:
       campaign_name_for_report = campaign_data_sample.campaign_name_platform
    elif raw_recent_metrics_row and getattr(raw_recent_metrics_row, 'campaign_name_platform', None): # From recent period query.
        campaign_name_for_report = raw_recent_metrics_row.campaign_name_platform
    elif raw_baseline_metrics_row and getattr(raw_baseline_metrics_row, 'campaign_name_platform', None): # From baseline period query.
        campaign_name_for_report = raw_baseline_metrics_row.campaign_name_platform

    # --- Step 6: Calculate derived metrics for both periods using the helper ---
    recent_metrics = calculate_campaign_metrics_from_row(raw_recent_metrics_row, campaign_name_for_report)
    baseline_metrics = calculate_campaign_metrics_from_row(raw_baseline_metrics_row, campaign_name_for_report)

    # --- Step 7: Generate observations by comparing recent vs. baseline metrics ---
    observations = [] # List to store observations (good, bad, neutral changes).
    # Define which metrics to compare and which are better if lower.
    metrics_to_compare = ["ctr", "cpc", "cvr_clicks", "cpa"]
    better_if_lower = ["cpc", "cpa"] # For these metrics, a decrease is positive.

    for metric_key in metrics_to_compare:
        recent_val = recent_metrics.get(metric_key, 0)
        baseline_val = baseline_metrics.get(metric_key, 0)
        change_percent_str = "N/A" # Default if baseline is zero.
        status = "neutral" # Default observation status.
        message = f"{metric_key.upper()} is {recent_val:.2f} for the recent period."

        if baseline_val != 0: # If baseline is non-zero, calculate percentage change.
            change_percent = ((recent_val - baseline_val) / baseline_val) * 100
            change_percent_str = f"{change_percent:+.1f}%" # Format with sign and one decimal.
            message += f" The baseline was {baseline_val:.2f}. This is a {change_percent_str} change."

            significant_change_threshold = 20.0 # Define what % change is considered significant.
            # Determine status (positive, warning, neutral) based on change and metric type.
            if metric_key in better_if_lower: # For metrics like CPC, CPA where lower is better.
                if change_percent > significant_change_threshold: # Increased significantly (bad).
                    status = "warning"; message += " This change may require attention."
                elif change_percent < -significant_change_threshold: # Decreased significantly (good).
                    status = "positive"; message += " This is a positive improvement."
            else: # For metrics like CTR, CVR where higher is better.
                if change_percent < -significant_change_threshold: # Decreased significantly (bad).
                    status = "warning"; message += " This change may require attention."
                elif change_percent > significant_change_threshold: # Increased significantly (good).
                    status = "positive"; message += " This is a positive improvement."
        elif recent_val != 0: # If baseline was zero but recent is not.
            message += " The baseline was zero or not applicable."
            # If it's a "higher is better" metric and now it's non-zero, that's positive.
            if metric_key not in better_if_lower: status = "positive"
            # If it's a "lower is better" metric and now it's non-zero (and positive), that's a warning.
            elif metric_key in better_if_lower and recent_val > 0 : status = "warning"

        observations.append({
            "metric": metric_key.upper(),
            "recent_value": recent_val,
            "baseline_value": baseline_val,
            "change_percent": change_percent_str,
            "message": message,
            "status": status
        })

    # Add an observation for spend (absolute values and change).
    spend_recent = recent_metrics.get('spend',0)
    spend_baseline = baseline_metrics.get('spend',0)
    spend_change_percent_str = "N/A"
    if spend_baseline != 0:
        spend_change_percent = ((spend_recent - spend_baseline) / spend_baseline) * 100
        spend_change_percent_str = f"{spend_change_percent:+.1f}%"

    observations.append({
        "metric": "Spend", "recent_value": spend_recent, "baseline_value": spend_baseline,
        "change_percent": spend_change_percent_str,
        "message": f"Recent period spend is {spend_recent:.2f}. Baseline period spend was {spend_baseline:.2f}.",
        "status": "neutral" # Spend change is often neutral unless tied to CPA/ROAS goals.
    })

    # --- Step 8: Structure and return the final JSON response ---
    final_response = {
        "campaign_name": campaign_name_for_report,
        "platform": platform_str,
        "recent_period_summary": {"start_date": recent_period_start.isoformat(), "end_date": recent_period_end.isoformat(), **recent_metrics},
        "baseline_period_summary": {"start_date": baseline_period_start.isoformat(), "end_date": baseline_period_end.isoformat(), **baseline_metrics},
        "observations": observations # List of generated insights/observations.
    }
    return jsonify(final_response)

# API Endpoint to get a list of user's campaigns for dropdowns/filters.
@ai_insights_bp.route('/api/user_campaign_list')
@login_required # User must be logged in.
def get_user_campaign_list():
    """
    Fetches a distinct list of campaign IDs, names, and their platforms
    associated with the current user's active integrations.
    This is typically used to populate dropdown menus or filters in the UI,
    allowing users to select specific campaigns for analysis.

    Returns:
        JSON: A list of campaign objects, where each object contains:
              - 'id': The platform-specific campaign ID.
              - 'name': The name of the campaign.
              - 'platform': The platform name (e.g., 'GoogleAds', 'MetaAds').
    """
    user_id = current_user.id # Get the ID of the logged-in user.

    # Query the CampaignData table to get distinct campaign information.
    # It joins with AdPlatformIntegration to filter by the current user and active integrations.
    campaigns_query = db.session.query(
        CampaignData.campaign_id_platform,    # Platform-specific campaign ID.
        CampaignData.campaign_name_platform,  # Name of the campaign.
        CampaignData.platform                 # Platform enum.
    ).join(AdPlatformIntegration).filter(
        AdPlatformIntegration.user_id == user_id,                 # Filter for the current user.
        AdPlatformIntegration.status == IntegrationStatusEnum.ACTIVE # Only include active integrations.
    ).distinct().order_by( # Ensure a consistent order for the list.
        CampaignData.platform,
        CampaignData.campaign_name_platform
    ).all()

    # Format the query results into a list of dictionaries for the JSON response.
    campaign_list = []
    for campaign in campaigns_query:
        campaign_list.append({
            "id": campaign.campaign_id_platform,
            # Use the campaign name if available, otherwise construct a name using the ID.
            "name": campaign.campaign_name_platform or f"Campaign ID: {campaign.campaign_id_platform}",
            "platform": campaign.platform.value # Get the string value of the platform enum.
        })
    return jsonify(campaign_list) # Return the list as JSON.

# --- Metric Trend Analysis Helper ---
# This function performs linear regression to determine the trend of a metric.
def analyze_data_trend(daily_metric_values_list):
    """
    Analyzes a list of daily metric values to determine its linear trend (upward, downward, or flat)
    using linear regression (numpy.polyfit).

    Args:
        daily_metric_values_list (list): A list of numerical daily metric values.
                                         The order implies chronological sequence.
    Returns:
        dict: A dictionary containing:
            'slope' (float): The slope of the fitted trend line.
            'intercept' (float): The intercept of the fitted trend line.
            'trend_direction' (str): 'upward', 'downward', 'flat', or 'insufficient_data'.
    """
    # Need at least 3 data points for a minimally reliable linear trend.
    if len(daily_metric_values_list) < 3:
        return {'slope': 0, 'intercept': 0, 'trend_direction': "insufficient_data"}

    y_values = np.array(daily_metric_values_list) # Dependent variable (metric values).
    x_values = np.arange(len(y_values))           # Independent variable (time steps, 0, 1, 2...).

    # Perform linear regression (degree 1 polynomial fit) to get slope and intercept.
    # `np.polyfit` returns [slope, intercept] for degree 1.
    slope, intercept = np.polyfit(x_values, y_values, 1)

    # Heuristic to determine if the trend is "flat":
    # The slope is considered flat if its absolute magnitude is less than a certain percentage
    # (e.g., 2%) of the average value of the metric. This helps avoid flagging minor fluctuations
    # as significant trends, especially for metrics with large average values.
    avg_value = np.mean(y_values) if len(y_values) > 0 else 0
    flat_threshold_ratio = 0.02 # Example: 2% of the average value. This may need tuning based on typical metric scales.

    trend_direction = "flat" # Default assumption for the trend direction.

    # Check if avg_value is very close to zero to prevent division by zero or skewed ratio when calculating relative threshold.
    if abs(avg_value) > 1e-6: # If average value is significantly non-zero.
        if slope > (abs(avg_value) * flat_threshold_ratio): # Positive slope greater than the dynamic threshold.
            trend_direction = "upward"
        elif slope < -(abs(avg_value) * flat_threshold_ratio): # Negative slope less than the negative dynamic threshold.
            trend_direction = "downward"
    # If average value is effectively zero, any discernible slope (not extremely close to zero) indicates a trend.
    elif slope > 1e-6:
        trend_direction = "upward"
    elif slope < -1e-6:
        trend_direction = "downward"

    return {'slope': round(slope, 2), 'intercept': round(intercept, 2), 'trend_direction': trend_direction}

# API Endpoint for Metric Trend Analysis.
@ai_insights_bp.route('/api/metric_trends')
@login_required # User must be logged in.
def get_metric_trends():
    """
    Analyzes and returns the historical trend of a specified metric over a given period,
    optionally filtered by campaign and/or platform.
    It provides daily data points for the metric, a calculated trend line (if enough data),
    and a summary message describing the trend.

    Query Parameters:
        metric (str, optional): The metric to analyze (e.g., 'spend', 'ctr', 'cpc'). Defaults to 'spend'.
        period_days (int, optional): Number of past days to analyze for the trend. Defaults to 30.
        campaign_id_platform (str, optional): Filter by a specific campaign ID (platform-specific).
        platform (str, optional): Filter by platform ('MetaAds' or 'GoogleAds').
                                  Required if 'campaign_id_platform' is specified.
    Returns:
        JSON: A dictionary containing trend analysis results:
              - 'metric': The metric analyzed.
              - 'period_days': The number of days in the analysis period.
              - 'campaign_name': Name of the campaign (or "All Campaigns").
              - 'platform': Name of the platform (or "All Platforms").
              - 'trend_data': List of {date, value} for the daily metric values.
              - 'trend_line': List of two {date, value} points for plotting the trend line.
              - 'trend_direction': 'upward', 'downward', 'flat', or 'insufficient_data'/'nodata'.
              - 'slope': The calculated slope of the trend.
              - 'message': A descriptive summary of the trend.
    """
    user_id = current_user.id
    # --- Step 1: Get and validate request parameters from query string ---
    metric_key = request.args.get('metric', 'spend') # Default to 'spend' if not provided.
    try:
        period_days = int(request.args.get('period_days', 30)) # Default to 30 days.
    except ValueError:
        err_msg = "'period_days' must be an integer."
        current_app.logger.warning(f"Bad request to {request.path}: {err_msg} (Params: {request.args})")
        return jsonify({"error": err_msg}), 400 # Return 400 Bad Request.

    campaign_id_filter = request.args.get('campaign_id_platform', None) # Optional campaign ID filter.
    platform_filter_str = request.args.get('platform', None)           # Optional platform filter.
    platform_enum_filter = None # Will hold the PlatformNameEnum member if a valid platform is specified.

    # Validate period_days.
    if period_days <= 1:
        err_msg = "'period_days' must be greater than 1 to calculate a meaningful trend."
        current_app.logger.warning(f"Bad request to {request.path}: {err_msg} (Params: {request.args})")
        return jsonify({"error": err_msg}), 400

    # Validate that if a campaign_id_filter is provided, a platform_filter_str must also be provided.
    if campaign_id_filter and not platform_filter_str:
        err_msg = "'platform' is required if 'campaign_id_platform' is specified for trend analysis."
        current_app.logger.warning(f"Bad request to {request.path}: {err_msg} (Params: {request.args})")
        return jsonify({"error": err_msg}), 400

    # Validate and convert platform_filter_str to its Enum equivalent if provided.
    if platform_filter_str:
        try:
            platform_enum_filter = PlatformNameEnum[platform_filter_str.upper()]
        except KeyError: # Handle invalid platform string.
            err_msg = f"Invalid platform: '{platform_filter_str}'. Supported values are 'MetaAds' or 'GoogleAds'."
            current_app.logger.warning(f"Bad request to {request.path}: {err_msg} (Params: {request.args})")
            return jsonify({"error": err_msg}), 400

    # --- Step 2: Define the date range for fetching historical data ---
    end_date = date.today() - timedelta(days=1) # Data up to yesterday to ensure complete daily data.
    start_date = end_date - timedelta(days=period_days - 1) # Calculate start date based on period_days.

    # --- Step 3: Construct and execute the database query for daily metrics ---
    # Base query to sum core metrics (spend, clicks, impressions, conversions) grouped by date.
    # Uses 'overall' breakdown_type records, which represent daily totals for a campaign/platform.
    query = db.session.query(
        CampaignData.date,
        func.sum(CampaignData.spend).label('spend'),
        func.sum(CampaignData.clicks).label('clicks'),
        func.sum(CampaignData.impressions).label('impressions'),
        func.sum(CampaignData.conversions).label('conversions')
    ).join(AdPlatformIntegration).filter( # Join to filter by user and active integrations.
        AdPlatformIntegration.user_id == user_id,
        AdPlatformIntegration.status == IntegrationStatusEnum.ACTIVE,
        CampaignData.breakdown_type == 'overall', # Focus on overall daily data for trends.
        CampaignData.date >= start_date,          # Apply date range filter.
        CampaignData.date <= end_date
    )

    # Prepare display names for the response, to be updated if filters are applied.
    display_campaign_name = "All Campaigns"
    display_platform_name = "All Platforms"

    # Apply campaign and/or platform filters to the query if they were specified.
    if campaign_id_filter and platform_enum_filter: # Filter by specific campaign on a specific platform.
        query = query.filter(
            CampaignData.campaign_id_platform == campaign_id_filter,
            CampaignData.platform == platform_enum_filter
        )
        # Attempt to retrieve the actual campaign name for more descriptive output.
        campaign_obj = db.session.query(CampaignData.campaign_name_platform).filter(
            CampaignData.campaign_id_platform==campaign_id_filter,
            CampaignData.platform==platform_enum_filter
        ).distinct().first()
        display_campaign_name = campaign_obj.campaign_name_platform if campaign_obj and campaign_obj.campaign_name_platform else campaign_id_filter
        display_platform_name = platform_enum_filter.value
    elif platform_enum_filter: # Filter by platform only (all campaigns on that platform).
        query = query.filter(CampaignData.platform == platform_enum_filter)
        display_campaign_name = f"All Campaigns on {platform_enum_filter.value}"
        display_platform_name = platform_enum_filter.value

    # Group by date and order chronologically to get daily aggregated metrics.
    query = query.group_by(CampaignData.date).order_by(CampaignData.date.asc())
    daily_data_rows = query.all() # Execute the database query.

    # --- Step 4: Process daily data and calculate the requested derived or base metric ---
    if not daily_data_rows: # If query returns no data for the selected criteria.
        return jsonify({
            "metric": metric_key, "period_days": period_days, "campaign_name": display_campaign_name,
            "platform": display_platform_name, "trend_data": [], "trend_line": [],
            "trend_direction": "nodata", "slope": 0, # 'nodata' indicates no source data.
            "message": "Not enough historical data found for the selected criteria to calculate a trend."
        }), 200 # Return 200 OK as it's a valid request but with no data for analysis.

    processed_daily_data = [] # List to store {date, value} dicts for the target metric.
    for row_day in daily_data_rows:
        # Extract base metrics from each day's aggregated row.
        day_spend = float(row_day.spend or 0)
        day_clicks = int(row_day.clicks or 0)
        day_impressions = int(row_day.impressions or 0)
        day_conversions = int(row_day.conversions or 0)

        # Calculate the target metric's value for the current day.
        # This handles both base metrics (like 'spend') and derived metrics (like 'ctr', 'cpc').
        value = 0.0 # Default value for the metric.
        if metric_key == 'spend': value = day_spend
        elif metric_key == 'clicks': value = day_clicks
        elif metric_key == 'impressions': value = day_impressions
        elif metric_key == 'conversions': value = day_conversions
        elif metric_key == 'ctr': value = (day_clicks / day_impressions) * 100 if day_impressions > 0 else 0.0
        elif metric_key == 'cpc': value = day_spend / day_clicks if day_clicks > 0 else 0.0
        elif metric_key == 'cvr_clicks': value = (day_conversions / day_clicks) * 100 if day_clicks > 0 else 0.0
        elif metric_key == 'cpa': value = day_spend / day_conversions if day_conversions > 0 else 0.0
        else: # Handle unsupported metric keys.
            err_msg = f"Unsupported metric for trend analysis: '{metric_key}'. Please choose from spend, clicks, impressions, conversions, ctr, cpc, cvr_clicks, cpa."
            current_app.logger.warning(f"Bad request to {request.path}: {err_msg} (Params: {request.args})")
            return jsonify({"error": err_msg}), 400

        processed_daily_data.append({'date': row_day.date.isoformat(), 'value': round(value, 2)})

    # --- Step 5: Analyze the trend of the processed daily data and generate trend line points ---
    message = "" # Initialize a message to describe the trend.
    if len(processed_daily_data) < 3 : # If fewer than 3 data points, trend is considered insufficient.
        trend_info = {'slope': 0, 'intercept': 0, 'trend_direction': "insufficient_data"}
        trend_line_data = [] # No trend line can be drawn.
        message = "Not enough data points (minimum 3 required) to calculate a reliable trend for the selected metric and period."
    else:
        # Extract the 'value' list for trend analysis.
        y_values = [item['value'] for item in processed_daily_data]
        trend_info = analyze_data_trend(y_values) # Call the helper function.

        # Create a user-friendly message summarizing the trend.
        metric_display_name = metric_key.replace('_', ' ').title() # Format metric name for display.
        message = f"The trend for {metric_display_name} is {trend_info['trend_direction']}."
        if trend_info['trend_direction'] not in ["flat", "insufficient_data"]: # Add slope details if trend is not flat or data insufficient.
            message += f" The average change is approximately {trend_info['slope']:.2f} per day over the analyzed period."

        # Generate two points (start and end) for plotting the trend line on a chart.
        trend_line_data = [
            {'date': processed_daily_data[0]['date'], 'value': round(trend_info['intercept'],2)}, # Start point of trend line.
            {'date': processed_daily_data[-1]['date'], 'value': round(trend_info['slope'] * (len(y_values)-1) + trend_info['intercept'],2)} # End point.
        ] if trend_info['trend_direction'] != "insufficient_data" else []

    # --- Step 6: Return the complete trend analysis in JSON format ---

    return jsonify({
        "metric": metric_key,
        "period_days": period_days,
        "campaign_name": display_campaign_name,
        "platform": display_platform_name,
        "trend_data": processed_daily_data,
        "trend_line": trend_line_data,
        "trend_direction": trend_info['trend_direction'],
        "slope": trend_info['slope'],
        "message": message
    })


# --- Simple Forecast Helper ---
# This function generates a basic forecast by averaging past data.
def generate_naive_forecast(historical_values_list, projection_days_count, last_historical_date_obj):
    """
    Generates a naive forecast by averaging historical daily values and projecting
    this average forward for a specified number of days. This is a simple forecasting
    method and does not account for seasonality, trends, or other complex factors.

    Args:
        historical_values_list (list): List of numerical daily metric values from the historical period.
        projection_days_count (int): Number of future days to project.
        last_historical_date_obj (date): The last date of the historical data period.
                                         The forecast will start from the day after this date.

    Returns:
        dict: A dictionary containing:
              - 'average_daily_value_used' (float): The calculated average of historical values.
              - 'forecast_data' (list): A list of dictionaries, where each dict has 'date' (ISO format)
                                        and 'projected_value' for the forecast period.
              - 'message' (str): A message describing the forecast method.
              Returns default values if historical data is insufficient.
    """
    # If no historical data is provided, a forecast cannot be generated.
    if not historical_values_list:
        return {
            "average_daily_value_used": 0,
            "forecast_data": [],
            "message": "No historical data was available to generate a forecast."
        }

    # Calculate the simple average of the historical daily values.
    avg_daily_value = sum(historical_values_list) / len(historical_values_list)

    forecasted_data = [] # Initialize a list to store the projected data points.

    # Generate projected values for each day in the specified projection period.
    for i in range(projection_days_count):
        # Calculate the future date for this projection point.
        forecast_date = (last_historical_date_obj + timedelta(days=i + 1)).isoformat()
        # The projected value for each future day is simply the calculated average.
        forecasted_data.append({'date': forecast_date, 'projected_value': round(avg_daily_value, 2)})

    return {
        "average_daily_value_used": round(avg_daily_value, 2),
        "forecast_data": forecasted_data,
        "message": f"Simple forecast based on the average daily value from the last {len(historical_values_list)} days."
    }

# API Endpoint for Simple Naive Forecasting.
@ai_insights_bp.route('/api/simple_forecast')
@login_required # User must be logged in.
def get_simple_forecast():
    """
    Generates a simple forecast for a specified metric by averaging historical data from
    a defined period and projecting this average into the future for a number of days.
    This endpoint can be filtered by campaign and/or platform.

    Query Parameters:
        metric (str, optional): The metric to forecast (e.g., 'spend', 'clicks', 'ctr'). Defaults to 'spend'.
        history_days (int, optional): Number of past days of data to use for calculating the historical average. Defaults to 14.
        projection_days (int, optional): Number of future days to project. Defaults to 7.
        campaign_id_platform (str, optional): Filter by a specific campaign ID (platform-specific).
        platform (str, optional): Filter by platform ('MetaAds' or 'GoogleAds').
                                  Required if 'campaign_id_platform' is specified.
    Returns:
        JSON: A dictionary containing:
              - 'metric', 'history_days', 'projection_days', 'campaign_name', 'platform' (reflecting inputs).
              - 'historical_data' (list): Daily values of the metric for the historical period.
              - 'average_daily_value_used' (float): The average value calculated from historical data.
              - 'forecast_data' (list): Projected daily values for the future period.
              - 'message' (str): A summary message about the forecast.
    """
    user_id = current_user.id
    # --- Step 1: Get and validate request parameters from query string ---
    metric_key = request.args.get('metric', 'spend') # Default to 'spend'.
    try:
        history_days = int(request.args.get('history_days', 14))     # Default to 14 days history.
        projection_days = int(request.args.get('projection_days', 7)) # Default to 7 days projection.
    except ValueError:
        err_msg = "'history_days' and 'projection_days' must be integers."
        current_app.logger.warning(f"Bad request to {request.path}: {err_msg} (Params: {request.args})")
        return jsonify({"error": err_msg}), 400 # Return 400 Bad Request.

    campaign_id_filter = request.args.get('campaign_id_platform', None) # Optional campaign filter.
    platform_filter_str = request.args.get('platform', None)           # Optional platform filter.
    platform_enum_filter = None # Will hold Enum value if platform is valid.

    # Validate that history_days and projection_days are positive.
    if history_days < 1 or projection_days < 1:
        err_msg = "'history_days' and 'projection_days' must be at least 1."
        current_app.logger.warning(f"Bad request to {request.path}: {err_msg} (Params: {request.args})")
        return jsonify({"error": err_msg}), 400

    # If filtering by campaign, platform must also be specified.
    if campaign_id_filter and not platform_filter_str:
        err_msg = "'platform' is required if 'campaign_id_platform' is specified for forecasting."
        current_app.logger.warning(f"Bad request to {request.path}: {err_msg} (Params: {request.args})")
        return jsonify({"error": err_msg}), 400

    # Validate and convert platform string to Enum if provided.
    if platform_filter_str:
        try:
            platform_enum_filter = PlatformNameEnum[platform_filter_str.upper()]
        except KeyError: # Handle invalid platform string.
            err_msg = f"Invalid platform: '{platform_filter_str}'. Supported values are 'MetaAds' or 'GoogleAds'."
            current_app.logger.warning(f"Bad request to {request.path}: {err_msg} (Params: {request.args})")
            return jsonify({"error": err_msg}), 400

    # --- Step 2: Define date range for fetching historical data ---
    hist_end_date = date.today() - timedelta(days=1) # Historical data up to yesterday.
    hist_start_date = hist_end_date - timedelta(days=history_days - 1) # Calculate start date based on history_days.

    # --- Step 3: Construct and execute database query for daily historical metrics ---
    # Query sums base metrics per day, using 'overall' breakdown type for daily totals.
    query = db.session.query(
        CampaignData.date,
        func.sum(CampaignData.spend).label('spend'),
        func.sum(CampaignData.clicks).label('clicks'),
        func.sum(CampaignData.impressions).label('impressions'),
        func.sum(CampaignData.conversions).label('conversions')
    ).join(AdPlatformIntegration).filter( # Filter by user and active integrations.
        AdPlatformIntegration.user_id == user_id,
        AdPlatformIntegration.status == IntegrationStatusEnum.ACTIVE,
        CampaignData.breakdown_type == 'overall',
        CampaignData.date >= hist_start_date, # Apply date range.
        CampaignData.date <= hist_end_date
    )

    # Prepare display names for the response, to be updated if filters are applied.
    display_campaign_name = "All Campaigns"
    display_platform_name = "All Platforms"

    # Apply campaign and/or platform filters to the query.
    if campaign_id_filter and platform_enum_filter: # Filter by specific campaign on a specific platform.
        query = query.filter(
            CampaignData.campaign_id_platform == campaign_id_filter,
            CampaignData.platform == platform_enum_filter
        )
        # Attempt to retrieve the actual campaign name for display.
        campaign_obj_result = db.session.query(CampaignData.campaign_name_platform).filter(
            CampaignData.campaign_id_platform == campaign_id_filter,
            CampaignData.platform == platform_enum_filter
        ).distinct().first()
        display_campaign_name = campaign_obj_result.campaign_name_platform if campaign_obj_result and campaign_obj_result.campaign_name_platform else campaign_id_filter
        display_platform_name = platform_enum_filter.value
    elif platform_enum_filter: # Filter by platform only.
        query = query.filter(CampaignData.platform == platform_enum_filter)
        display_campaign_name = f"All Campaigns on {platform_enum_filter.value}"
        display_platform_name = platform_enum_filter.value

    # Group by date and order chronologically to get daily aggregated metrics.
    query = query.group_by(CampaignData.date).order_by(CampaignData.date.asc())
    daily_historical_rows = query.all() # Execute the query.

    # --- Step 4: Process historical data and prepare for forecasting ---
    if not daily_historical_rows: # If no data for the selected criteria.
        return jsonify({
            "metric": metric_key, "history_days": history_days, "projection_days": projection_days,
            "campaign_name": display_campaign_name, "platform": display_platform_name,
            "historical_data": [], "average_daily_value_used": 0, "forecast_data": [],
            "message": "Not enough historical data found for the selected criteria to generate a forecast."
        }), 200 # 200 OK, valid request but no data to process.

    historical_metric_data_for_charting = [] # For displaying the historical part of the chart.
    historical_values_for_forecast = []   # Raw values used for calculating the average.

    # Calculate the target metric for each day in the historical period.
    for row_day in daily_historical_rows:
        day_spend = float(row_day.spend or 0)
        day_clicks = int(row_day.clicks or 0)
        day_impressions = int(row_day.impressions or 0)
        day_conversions = int(row_day.conversions or 0)

        value = 0.0 # Value of the target metric.
        # Handle base and derived metrics.
        if metric_key == 'spend': value = day_spend
        elif metric_key == 'clicks': value = day_clicks
        elif metric_key == 'impressions': value = day_impressions
        elif metric_key == 'conversions': value = day_conversions
        elif metric_key == 'ctr': value = (day_clicks / day_impressions) * 100 if day_impressions > 0 else 0.0
        elif metric_key == 'cpc': value = day_spend / day_clicks if day_clicks > 0 else 0.0
        elif metric_key == 'cvr_clicks': value = (day_conversions / day_clicks) * 100 if day_clicks > 0 else 0.0
        elif metric_key == 'cpa': value = day_spend / day_conversions if day_conversions > 0 else 0.0
        else: # Handle unsupported metric.
            err_msg = f"Unsupported metric for forecast: '{metric_key}'. Valid: spend, clicks, impressions, conversions, ctr, cpc, cvr_clicks, cpa."
            current_app.logger.warning(f"Bad request to {request.path}: {err_msg} (Params: {request.args})")
            return jsonify({"error": err_msg}), 400

        historical_metric_data_for_charting.append({'date': row_day.date.isoformat(), 'value': round(value, 2)})
        historical_values_for_forecast.append(value)

    # If, after processing, all historical values were zero or invalid, leading to an empty list for forecasting.
    if not historical_values_for_forecast:
        return jsonify({
            "metric": metric_key, "history_days": history_days, "projection_days": projection_days,
            "campaign_name": display_campaign_name, "platform": display_platform_name,
            "historical_data": [], "average_daily_value_used": 0, "forecast_data": [],
            "message": "Not enough processed historical data (e.g., all values were zero) to generate a forecast."
        }), 200

    # --- Step 5: Generate the naive forecast using the helper function ---
    # Determine the last date from historical data to start the forecast from the subsequent day.
    last_hist_date = daily_historical_rows[-1].date if daily_historical_rows else hist_end_date
    forecast_result = generate_naive_forecast(historical_values_for_forecast, projection_days, last_hist_date)

    # --- Step 6: Return the complete forecast results in JSON format ---
    return jsonify({
        "metric": metric_key,
        "history_days": history_days,
        "projection_days": projection_days,
        "campaign_name": display_campaign_name,
        "platform": display_platform_name,
        "historical_data": historical_metric_data_for_charting,
        "average_daily_value_used": forecast_result["average_daily_value_used"],
        "forecast_data": forecast_result["forecast_data"],
        "message": forecast_result["message"]
    })
