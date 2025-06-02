from flask import Blueprint, jsonify, request, current_app
from flask_login import login_required, current_user
from ..models import CampaignData, PlatformNameEnum, AdPlatformIntegration, IntegrationStatusEnum, db
from datetime import datetime, date, timedelta
from sqlalchemy import func
from ..utils.helpers import parse_date_range

# Blueprint for dashboard-related routes.
# This blueprint handles all routes that provide data for the user-facing dashboards.
dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')

# API endpoint to fetch Key Performance Indicators (KPIs).
@dashboard_bp.route('/api/kpis')
@login_required # Ensures only logged-in users can access this endpoint.
# @subscription_required(required_level_names=['Basic', 'Pro', 'Enterprise']) # Example for future subscription-based access.
def get_kpis():
    """
    Calculates and returns key performance indicators (KPIs) like total spend,
    total conversions, and cost per conversion for a selected date range and platform.
    It also calculates the percentage change of these KPIs compared to the
    immediately preceding period of the same duration.

    Query Parameters:
        platform (str, optional): Filter by ad platform ('MetaAds', 'GoogleAds', or 'all'). Defaults to 'all'.
        date_range (str, optional): Predefined date range (e.g., 'last_7_days', 'last_30_days', 'this_month')
                                    or 'custom' if start_date and end_date are provided.
        start_date (str, optional): Custom start date (YYYY-MM-DD), required if date_range is 'custom'.
        end_date (str, optional): Custom end date (YYYY-MM-DD), required if date_range is 'custom'.
    Returns:
        JSON: A JSON object containing calculated KPIs and their percentage changes.
    """
    # Get 'platform' filter from request arguments, default to 'all' if not provided.
    platform_filter = request.args.get('platform', 'all')

    # Parse the date range from request arguments using a helper function.
    # This handles predefined ranges (e.g., 'last_7_days') and custom date ranges.
    start_date, end_date, error_response = parse_date_range(request.args, default_range_str='last_7_days')
    if error_response: # If date parsing fails, return the error response.
        error_message, status_code = error_response
        current_app.logger.warning(f"Bad request to {request.path}: {error_message.get('error')} (Params: {request.args})")
        return jsonify(error_message), status_code

    # --- Query for the current period ---
    # Base SQLAlchemy query to aggregate core metrics from CampaignData.
    query = db.session.query(
        func.sum(CampaignData.spend).label('total_spend'),           # Sum of all spending.
        func.sum(CampaignData.conversions).label('total_conversions'), # Sum of all conversions.
        func.sum(CampaignData.clicks).label('total_clicks'),           # Sum of all clicks.
        func.sum(CampaignData.impressions).label('total_impressions')  # Sum of all impressions.
    ).join(AdPlatformIntegration).filter( # Join with AdPlatformIntegration to filter by user and status.
        AdPlatformIntegration.user_id == current_user.id,           # Filter for the current user's integrations.
        AdPlatformIntegration.status == IntegrationStatusEnum.ACTIVE, # Only include active integrations.
        CampaignData.date >= start_date,                            # Filter by the selected date range.
        CampaignData.date <= end_date
    )

    platform_enum_val = None # To store the enum value if a specific platform is filtered.
    # Apply platform filter if 'platform' is not 'all'.
    if platform_filter != 'all':
        try:
            # Convert platform string to its Enum equivalent (e.g., 'MetaAds' -> PlatformNameEnum.META_ADS).
            platform_enum_val = PlatformNameEnum[platform_filter.upper()]
            query = query.filter(CampaignData.platform == platform_enum_val) # Add platform filter to the query.
        except KeyError: # Handle invalid platform string.
            err_msg = f"Invalid platform: '{platform_filter}'. Supported values are 'MetaAds', 'GoogleAds', or 'all'."
            current_app.logger.warning(f"Bad request to {request.path}: {err_msg} (Params: {request.args})")
            return jsonify({"error": err_msg}), 400 # Return 400 Bad Request.

    # Execute the query for the current period. .one() expects exactly one result row.
    results = query.one()

    # Extract results, defaulting to 0 if None (e.g., no data for the period).
    total_spend = results.total_spend or 0
    total_conversions = results.total_conversions or 0

    # Calculate Cost Per Conversion (CPC). Avoid division by zero.
    cost_per_conversion = (total_spend / total_conversions) if total_conversions > 0 else 0

    # --- Query for the previous period to calculate percentage changes ---
    # Determine the duration of the current period.
    current_duration_days = (end_date - start_date).days + 1 # Add 1 because ranges are inclusive.
    # Calculate start and end dates for the immediately preceding period of the same duration.
    prev_end_date = start_date - timedelta(days=1)
    prev_start_date = prev_end_date - timedelta(days=current_duration_days - 1)

    prev_total_spend, prev_total_conversions = 0, 0 # Initialize previous period metrics.
    # Ensure the calculated previous period dates are valid.
    if prev_start_date and prev_end_date:
        # Construct a similar query for the previous period.
        prev_query = db.session.query(
            func.sum(CampaignData.spend).label('total_spend'),
            func.sum(CampaignData.conversions).label('total_conversions')
        ).join(AdPlatformIntegration).filter(
            AdPlatformIntegration.user_id == current_user.id,
            AdPlatformIntegration.status == IntegrationStatusEnum.ACTIVE,
            CampaignData.date >= prev_start_date,
            CampaignData.date <= prev_end_date
        )
        # Apply platform filter to previous period query if a specific platform was selected.
        if platform_enum_val:
            prev_query = prev_query.filter(CampaignData.platform == platform_enum_val)

        prev_results = prev_query.one() # Execute query for previous period.
        prev_total_spend = prev_results.total_spend or 0
        prev_total_conversions = prev_results.total_conversions or 0

    # Helper function to calculate percentage change between two values.
    def calculate_percentage_change(current_value, previous_value):
        """Calculates percentage change. Returns formatted string and raw float value."""
        if previous_value == 0:
            # If previous value is 0, change is undefined or infinite.
            # Return "N/A" if current is also 0, otherwise +100% (or some indicator of significant change).
            return "N/A" if current_value == 0 else "+100.0%", 100.0 if current_value != 0 else 0.0
        change_val = ((current_value - previous_value) / previous_value) * 100
        # Format with a leading '+' for positive changes.
        return f"{'+' if change_val >= 0 else ''}{change_val:.1f}%", round(change_val,1)

    # Calculate percentage changes for major KPIs.
    total_spend_change_str, total_spend_change_val = calculate_percentage_change(total_spend, prev_total_spend)
    total_conversions_change_str, total_conversions_change_val = calculate_percentage_change(total_conversions, prev_total_conversions)

    prev_cost_per_conversion = (prev_total_spend / prev_total_conversions) if prev_total_conversions > 0 else 0
    cpc_change_str, cpc_change_val = calculate_percentage_change(cost_per_conversion, prev_cost_per_conversion)

    # Structure the KPIs for the JSON response.
    kpis = {
        'total_spend': float(total_spend),
        'total_conversions': int(total_conversions),
        'cost_per_conversion': float(cost_per_conversion),
        'total_spend_change': total_spend_change_str, # Formatted string for display
        'total_spend_change_val': total_spend_change_val if total_spend_change_str != "N/A" else None, # Raw float for conditional styling/logic
        'total_conversions_change': total_conversions_change_str,
        'total_conversions_change_val': total_conversions_change_val if total_conversions_change_str != "N/A" else None,
        'cost_per_conversion_change': cpc_change_str,
        'cost_per_conversion_change_val': cpc_change_val if cpc_change_str != "N/A" else None,
        # Consider adding clicks and impressions KPIs if needed on the frontend.
    }
    return jsonify(kpis)

@dashboard_bp.route('/api/budget_status')
@login_required
def get_budget_status():
    # Purpose: Provide a quick overview of the current month's budget status.
    # Note: This is a simplified example. A real application might involve more complex budget models,
    # user-settable budgets per platform or campaign, etc.

    # Get the first day of the current month and today's date for filtering.
    today = date.today()
    current_month_start = today.replace(day=1)

    # Query to sum total spend for the current user's active integrations for the current month to date.
    current_spend_result = db.session.query(
        func.sum(CampaignData.spend) # Sum of the 'spend' column.
    ).join(AdPlatformIntegration).filter( # Join to filter by user and integration status.
        AdPlatformIntegration.user_id == current_user.id,
        AdPlatformIntegration.status == IntegrationStatusEnum.ACTIVE,
        CampaignData.date >= current_month_start, # Filter for records from the start of the current month.
        CampaignData.date <= today                # Filter for records up to today.
    ).scalar() or 0 # .scalar() returns the first element of the first result or None; default to 0 if None.

    # Placeholder for total budget. In a real app, this would likely come from user settings or a dedicated budget model.
    total_budget_placeholder = 20000 # Example: $20,000 monthly budget.

    # Calculate the percentage of the budget utilized.
    budget_utilized_percentage = (current_spend_result / total_budget_placeholder) * 100 if total_budget_placeholder > 0 else 0

    # Return the budget status data as JSON.
    return jsonify({
        'total_budget': total_budget_placeholder,
        'current_spend': float(current_spend_result), # Ensure spend is a float.
        'budget_utilized_percentage': round(budget_utilized_percentage, 2) # Round to 2 decimal places.
    })

# API endpoint to fetch performance data over time for charts (e.g., line charts).
@dashboard_bp.route('/api/performance_breakdown')
@login_required # User must be logged in.
def get_performance_breakdown():
    """
    Provides time-series data for a specified metric (e.g., spend, clicks)
    over a selected date range, broken down by ad platform.
    This data is formatted for easy use with charting libraries like Chart.js.

    Query Parameters:
        metric (str, optional): The metric to plot (e.g., 'spend', 'clicks', 'impressions', 'conversions'). Defaults to 'spend'.
        platform (str, optional): Filter by platform ('MetaAds', 'GoogleAds', 'all'). Defaults to 'all'.
        date_range, start_date, end_date: As defined in get_kpis. Defaults to 'last_30_days'.
    Returns:
        JSON: Data formatted for Chart.js, including labels (dates) and datasets (one per platform).
    """
    # Get 'metric' to plot from request args, default to 'spend'.
    metric_to_plot = request.args.get('metric', 'spend')

    # Parse date range from request arguments.
    start_date, end_date, error_response = parse_date_range(request.args, default_range_str='last_30_days')
    if error_response: # Handle date parsing errors.
        error_message, status_code = error_response
        current_app.logger.warning(f"Bad request to {request.path}: {error_message.get('error')} (Params: {request.args})")
        return jsonify(error_message), status_code

    # Dynamically get the SQLAlchemy column attribute for the selected metric from CampaignData model.
    metric_column = getattr(CampaignData, metric_to_plot, None)
    # Validate if the metric exists in the model.
    if metric_column is None:
        # Specific check for 'cost_per_conversion' as it's a calculated metric not directly queryable here.
        if metric_to_plot == "cost_per_conversion":
            err_msg = f"Calculated metric '{metric_to_plot}' is not directly supported by this endpoint for time-series breakdown. Please choose a base metric like spend or conversions."
            current_app.logger.warning(f"Bad request to {request.path}: {err_msg} (Params: {request.args})")
            return jsonify({"error": err_msg}), 400
        err_msg = f"Invalid metric: '{metric_to_plot}'. Please choose from spend, clicks, impressions, or conversions."
        current_app.logger.warning(f"Bad request to {request.path}: {err_msg} (Params: {request.args})")
        return jsonify({"error": err_msg}), 400 # Return 400 for invalid metric.

    # Get 'platform' filter from request args.
    platform_filter_param = request.args.get('platform', 'all')

    # Base query to fetch daily aggregated data for the selected metric.
    # Data is fetched from 'overall' breakdown records, which represent daily totals per campaign.
    query = db.session.query(
        CampaignData.date,                  # Date of the data entry.
        CampaignData.platform,              # Ad platform (MetaAds, GoogleAds).
        func.sum(metric_column).label('metric_value') # Sum of the selected metric for that day and platform.
    ).join(AdPlatformIntegration).filter(
        AdPlatformIntegration.user_id == current_user.id,           # Current user's data.
        AdPlatformIntegration.status == IntegrationStatusEnum.ACTIVE, # Active integrations only.
        CampaignData.breakdown_type == 'overall', # Use 'overall' records for daily totals.
        CampaignData.date >= start_date,        # Date range filter.
        CampaignData.date <= end_date
    )

    # Apply platform filter if a specific platform is chosen.
    if platform_filter_param != 'all':
        try:
            platform_enum_val_breakdown = PlatformNameEnum[platform_filter_param.upper()]
            query = query.filter(CampaignData.platform == platform_enum_val_breakdown)
        except KeyError: # Handle invalid platform string.
            err_msg = f"Invalid platform filter: '{platform_filter_param}'."
            current_app.logger.warning(f"Bad request to {request.path}: {err_msg} (Params: {request.args})")
            return jsonify({"error": err_msg}), 400

    # Group by date and platform, then order by date for time-series charts.
    query = query.group_by(CampaignData.date, CampaignData.platform).order_by(CampaignData.date.asc())
    results = query.all() # Execute the query.

    # --- Prepare data for Chart.js ---
    # Initialize structure for chart data: labels (dates) and datasets (one per platform).
    chart_data = {'labels': [], 'datasets': {}}

    # Populate labels with all dates in the selected range to ensure consistent chart x-axis.
    current_loop_date = start_date
    while current_loop_date <= end_date:
        chart_data['labels'].append(current_loop_date.isoformat()) # Format date as YYYY-MM-DD string.
        current_loop_date += timedelta(days=1)

    # Determine which platforms to include in the datasets.
    platforms_to_render = []
    if platform_filter_param != 'all': # If a specific platform is filtered.
        try:
            platforms_to_render.append(PlatformNameEnum[platform_filter_param.upper()])
        except KeyError: # Should have been caught earlier, but defensive check.
            pass
    else: # If 'all' platforms, include all known platforms from Enum.
        platforms_to_render = list(PlatformNameEnum)

    # Initialize datasets for each platform to be rendered.
    # Each dataset will have a label (platform name) and data points (initialized to 0 for all dates).
    for platform_enum in platforms_to_render:
        platform_name = platform_enum.value # Get string value of platform (e.g., "GOOGLE_ADS").
        chart_data['datasets'][platform_name] = {
            'label': platform_name.replace("_", " "), # User-friendly label.
            'data': [0.0] * len(chart_data['labels']) # Initialize data array with zeros.
        }

    # Populate dataset values with actual data from the query results.
    for row in results:
        try:
            # Find the index of the date in our pre-generated labels list.
            date_index = chart_data['labels'].index(row.date.isoformat())
            platform_name = row.platform.value # Get platform name from the query result.
            # If this platform is being rendered, update its data for the specific date.
            if platform_name in chart_data['datasets']:
                metric_val = row.metric_value
                chart_data['datasets'][platform_name]['data'][date_index] = float(metric_val) if metric_val is not None else 0.0
        except ValueError:
            # This might happen if a date from the database is somehow outside the generated label range (should be rare).
            current_app.logger.warning(f"Date {row.date.isoformat()} from query result not found in pre-generated labels for performance breakdown chart.")
            pass

    # Convert the datasets dictionary to a list, as expected by Chart.js.
    chart_data['datasets'] = list(chart_data['datasets'].values())

    return jsonify(chart_data)

@dashboard_bp.route('/api/breakdown/device')
@login_required
def get_device_breakdown():
    start_date, end_date, error_response = parse_date_range(request.args, default_range_str='last_30_days')
    if error_response:
        error_message, status_code = error_response
        current_app.logger.warning(f"Bad request to /api/breakdown/device: {error_message.get('error')} Params: {request.args}")
        return jsonify(error_message), status_code

    """
    Provides aggregated data for a specified metric, broken down by device type
    (e.g., mobile, desktop, tablet) over a selected date range and for a chosen platform.
    Formatted for use in bar charts (e.g., via Chart.js).

    Query Parameters:
        platform (str, optional): Filter by platform. Defaults to 'all'.
        metric (str, optional): Metric to display (spend, clicks, etc.). Defaults to 'spend'.
        date_range, start_date, end_date: As in get_kpis. Defaults to 'last_30_days'.
    Returns:
        JSON: Data formatted for a Chart.js bar chart.
    """
    # Parse date range from request arguments.
    start_date, end_date, error_response = parse_date_range(request.args, default_range_str='last_30_days')
    if error_response: # Handle date parsing errors.
        error_message, status_code = error_response
        current_app.logger.warning(f"Bad request to /api/breakdown/device: {error_message.get('error')} (Params: {request.args})")
        return jsonify(error_message), status_code

    # Get platform filter and metric key from request arguments.
    platform_filter_str = request.args.get('platform', 'all')
    metric_key = request.args.get('metric', 'spend')

    # Dynamically select the metric column from CampaignData model based on metric_key.
    if metric_key == 'spend': metric_column = CampaignData.spend
    elif metric_key == 'clicks': metric_column = CampaignData.clicks
    elif metric_key == 'impressions': metric_column = CampaignData.impressions
    elif metric_key == 'conversions': metric_column = CampaignData.conversions
    else: # Handle invalid metric key.
        err_msg = f"Invalid metric: '{metric_key}'. Supported metrics are spend, clicks, impressions, conversions."
        current_app.logger.warning(f"Bad request to /api/breakdown/device: {err_msg} (Params: {request.args})")
        return jsonify({"error": err_msg}), 400

    # Base query to aggregate the selected metric, grouped by device.
    query = db.session.query(
        CampaignData.breakdown_value.label('device'), # The device value (e.g., 'mobile', 'desktop').
        CampaignData.platform,                        # Platform for potential multi-platform aggregation.
        func.sum(metric_column).label('total_metric_value') # Sum of the selected metric for each device.
    ).join(AdPlatformIntegration).filter(
        AdPlatformIntegration.user_id == current_user.id,           # Current user's data.
        AdPlatformIntegration.status == IntegrationStatusEnum.ACTIVE, # Active integrations.
        CampaignData.breakdown_type == 'device', # Crucial filter for device-specific records.
        CampaignData.date >= start_date,        # Date range filter.
        CampaignData.date <= end_date
    )

    # Apply platform filter if not 'all'.
    if platform_filter_str != 'all':
        try:
            platform_enum = PlatformNameEnum[platform_filter_str.upper()]
            query = query.filter(CampaignData.platform == platform_enum)
        except KeyError: # Handle invalid platform string.
            err_msg = f"Invalid platform: '{platform_filter_str}'."
            current_app.logger.warning(f"Bad request to /api/breakdown/device: {err_msg} (Params: {request.args})")
            return jsonify({"error": err_msg}), 400

    # Group by device value and platform, then order by device value.
    query = query.group_by(CampaignData.breakdown_value, CampaignData.platform).order_by(CampaignData.breakdown_value)
    results = query.all() # Execute the query.

    # --- Prepare data for Chart.js bar chart ---
    chart_labels = [] # Labels for the x-axis (device types).
    chart_data_values = [] # Corresponding metric values for each device.
    dataset_label = "" # Label for the dataset in the chart legend.

    aggregated_results = {} # Used if 'all' platforms are selected to sum data across platforms for each device.

    # If 'all' platforms, aggregate results from different platforms for the same device type.
    if platform_filter_str == 'all':
        for row in results:
            device = row.device if row.device else "Unknown" # Handle null device values.
            current_val = aggregated_results.get(device, 0)
            # Add the metric value from this row to the aggregated total for the device.
            aggregated_results[device] = current_val + (float(row.total_metric_value) if row.total_metric_value else 0)
        # Sort device labels alphabetically for consistent chart display.
        chart_labels = sorted(list(aggregated_results.keys()))
        chart_data_values = [aggregated_results[label] for label in chart_labels]
        dataset_label = f"{metric_key.replace('_',' ').title()} by Device (All Platforms)"
    else: # If a specific platform is selected, directly use the query results.
        for row in results:
            device = row.device if row.device else "Unknown"
            chart_labels.append(device)
            chart_data_values.append(float(row.total_metric_value) if row.total_metric_value else 0)
        dataset_label = f"{metric_key.replace('_',' ').title()} by Device ({platform_filter_str})"

    # Predefined background colors for chart bars.
    background_colors = ["#3B82F6", "#10B981", "#F59E0B", "#EF4444", "#8B5CF6", "#EC4899"]

    # Structure the final chart data object for Chart.js.
    final_chart_data = {
        "labels": chart_labels,
        "datasets": [{
            "label": dataset_label,
            "data": chart_data_values,
            "backgroundColor": background_colors[:len(chart_labels)] # Use a subset of colors if fewer labels.
        }]
    }

    # Handle cases where no data is found.
    if not results and not chart_labels:
         final_chart_data = {"labels": [], "datasets": [{"label": f"No {metric_key.replace('_',' ')} data for selected device breakdown", "data": [], "backgroundColor": []}]}

    return jsonify(final_chart_data)

@dashboard_bp.route('/api/breakdown/audience')
@login_required
def get_audience_breakdown():
    start_date, end_date, error_response = parse_date_range(request.args, default_range_str='last_30_days')
    if error_response:
        error_message, status_code = error_response
        current_app.logger.warning(f"Bad request to /api/breakdown/audience: {error_message.get('error')} Params: {request.args}")
        return jsonify(error_message), status_code

    """
    Provides aggregated data for a specified metric, broken down by audience dimension
    (age range or gender) over a selected date range and for a chosen platform.
    Formatted for use in bar charts.

    Query Parameters:
        platform (str, optional): Filter by platform. Defaults to 'all'.
        metric (str, optional): Metric to display. Defaults to 'spend'.
        dimension (str, optional): Audience dimension ('age_range' or 'gender'). Defaults to 'age_range'.
        date_range, start_date, end_date: As in get_kpis. Defaults to 'last_30_days'.
    Returns:
        JSON: Data formatted for a Chart.js bar chart.
    """
    # Parse date range from request arguments.
    start_date, end_date, error_response = parse_date_range(request.args, default_range_str='last_30_days')
    if error_response: # Handle date parsing errors.
        error_message, status_code = error_response
        current_app.logger.warning(f"Bad request to /api/breakdown/audience: {error_message.get('error')} (Params: {request.args})")
        return jsonify(error_message), status_code

    # Get platform filter, metric key, and audience dimension from request arguments.
    platform_filter_str = request.args.get('platform', 'all')
    metric_key = request.args.get('metric', 'spend')
    dimension = request.args.get('dimension', 'age_range') # Default to 'age_range' if not specified.

    # Validate the requested audience dimension.
    if dimension not in ['age_range', 'gender']:
        current_app.logger.warning(f"Bad request to /api/breakdown/audience: Invalid dimension '{dimension}'.")
        return jsonify({"error": f"Invalid dimension: '{dimension}'. Supported dimensions are 'age_range' or 'gender'."}), 400

    # Dynamically select the metric column from CampaignData model.
    if metric_key == 'spend': metric_column = CampaignData.spend
    elif metric_key == 'clicks': metric_column = CampaignData.clicks
    elif metric_key == 'impressions': metric_column = CampaignData.impressions
    elif metric_key == 'conversions': metric_column = CampaignData.conversions
    else: # Handle invalid metric key.
        current_app.logger.warning(f"Bad request to /api/breakdown/audience: Invalid metric '{metric_key}'.")
        return jsonify({"error": f"Invalid metric: '{metric_key}'. Supported: spend, clicks, impressions, conversions."}), 400

    # Base query to aggregate the selected metric, grouped by the chosen audience dimension.
    query = db.session.query(
        CampaignData.breakdown_value.label('segment_value'), # The audience segment value (e.g., '18-24', 'female').
        CampaignData.platform,                               # Platform for potential multi-platform aggregation.
        func.sum(metric_column).label('total_metric_value')  # Sum of the metric for each segment.
    ).join(AdPlatformIntegration).filter(
        AdPlatformIntegration.user_id == current_user.id,           # Current user's data.
        AdPlatformIntegration.status == IntegrationStatusEnum.ACTIVE, # Active integrations.
        CampaignData.breakdown_type == dimension, # Filter by the specified audience dimension ('age_range' or 'gender').
        CampaignData.date >= start_date,        # Date range filter.
        CampaignData.date <= end_date
    )

    # Apply platform filter if not 'all'.
    if platform_filter_str != 'all':
        try:
            platform_enum = PlatformNameEnum[platform_filter_str.upper()]
            query = query.filter(CampaignData.platform == platform_enum)
        except KeyError: # Handle invalid platform string.
            current_app.logger.warning(f"Bad request to /api/breakdown/audience: Invalid platform '{platform_filter_str}'.")
            return jsonify({"error": f"Invalid platform: '{platform_filter_str}'."}), 400

    # Group by segment value and platform, then order by segment value.
    query = query.group_by(CampaignData.breakdown_value, CampaignData.platform).order_by(CampaignData.breakdown_value)
    results = query.all() # Execute the query.

    # --- Prepare data for Chart.js bar chart ---
    chart_labels = []       # Labels for x-axis (audience segments).
    chart_data_values = []  # Corresponding metric values.

    # Create a display-friendly dataset label.
    platform_filter_str_display = platform_filter_str.replace("Ads"," Ads") if platform_filter_str != "all" else "All Platforms"
    dimension_display_name = dimension.replace('_',' ').title() # e.g., 'Age Range' or 'Gender'.
    dataset_label = f"{metric_key.replace('_',' ').title()} by {dimension_display_name} ({platform_filter_str_display})"

    aggregated_results = {} # Used if 'all' platforms to sum data across platforms for each segment.

    # Aggregate results if 'all' platforms are selected.
    if platform_filter_str == 'all':
        for row in results:
            segment_val = row.segment_value if row.segment_value else "Unknown" # Handle null segment values.
            current_val = aggregated_results.get(segment_val, 0)
            aggregated_results[segment_val] = current_val + (float(row.total_metric_value) if row.total_metric_value else 0)
        chart_labels = sorted(list(aggregated_results.keys())) # Sort segment labels.
        chart_data_values = [aggregated_results[label] for label in chart_labels]
    else: # If a specific platform is selected.
        for row in results:
            segment_val = row.segment_value if row.segment_value else "Unknown"
            chart_labels.append(segment_val)
            chart_data_values.append(float(row.total_metric_value) if row.total_metric_value else 0)

    # Predefined background colors for chart bars.
    background_colors = ["#4A5568", "#A0AEC0", "#718096", "#E2E8F0", "#CBD5E0", "#A0AEC0", "#718096", "#4FD1C5", "#68D391"]

    # Structure final chart data.
    final_chart_data = {
        "labels": chart_labels,
        "datasets": [{
            "label": dataset_label,
            "data": chart_data_values,
            "backgroundColor": background_colors[:len(chart_labels)] # Use a subset of colors.
        }]
    }

    # Handle cases where no data is found.
    if not results and not chart_labels:
        final_chart_data = {"labels": [], "datasets": [{"label": f"No {metric_key.replace('_',' ')} data for selected {dimension_display_name.lower()} breakdown", "data": [], "backgroundColor": []}]}

    return jsonify(final_chart_data)

@dashboard_bp.route('/api/breakdown/country')
@login_required
def get_country_breakdown():
    start_date, end_date, error_response = parse_date_range(request.args, default_range_str='last_30_days')
    if error_response:
        error_message, status_code = error_response
        current_app.logger.warning(f"Bad request to /api/breakdown/country: {error_message.get('error')} Params: {request.args}")
        return jsonify(error_message), status_code

    """
    Provides aggregated data for a specified metric, broken down by country
    over a selected date range and for a chosen platform.
    Formatted for use in bar charts.

    Query Parameters:
        platform (str, optional): Filter by platform. Defaults to 'all'.
        metric (str, optional): Metric to display. Defaults to 'spend'.
        date_range, start_date, end_date: As in get_kpis. Defaults to 'last_30_days'.
    Returns:
        JSON: Data formatted for a Chart.js bar chart.
    """
    # Parse date range from request arguments.
    start_date, end_date, error_response = parse_date_range(request.args, default_range_str='last_30_days')
    if error_response: # Handle date parsing errors.
        error_message, status_code = error_response
        current_app.logger.warning(f"Bad request to /api/breakdown/country: {error_message.get('error')} (Params: {request.args})")
        return jsonify(error_message), status_code

    # Get platform filter and metric key from request arguments.
    platform_filter_str = request.args.get('platform', 'all')
    metric_key = request.args.get('metric', 'spend')

    # Dynamically select the metric column from CampaignData model.
    if metric_key == 'spend': metric_column = CampaignData.spend
    elif metric_key == 'clicks': metric_column = CampaignData.clicks
    elif metric_key == 'impressions': metric_column = CampaignData.impressions
    elif metric_key == 'conversions': metric_column = CampaignData.conversions
    else: # Handle invalid metric key.
        err_msg = f"Invalid metric: '{metric_key}'. Supported metrics are spend, clicks, impressions, conversions."
        current_app.logger.warning(f"Bad request to /api/breakdown/country: {err_msg} (Params: {request.args})")
        return jsonify({"error": err_msg}), 400

    # Base query to aggregate the selected metric, grouped by country.
    query = db.session.query(
        CampaignData.breakdown_value.label('country_code'), # The country code (e.g., 'US', 'CA').
        CampaignData.platform,                             # Platform for multi-platform aggregation.
        func.sum(metric_column).label('total_metric_value')# Sum of the metric for each country.
    ).join(AdPlatformIntegration).filter(
        AdPlatformIntegration.user_id == current_user.id,           # Current user's data.
        AdPlatformIntegration.status == IntegrationStatusEnum.ACTIVE, # Active integrations.
        CampaignData.breakdown_type == 'country', # Filter for country-specific breakdown records.
        CampaignData.date >= start_date,        # Date range filter.
        CampaignData.date <= end_date
    )

    # Apply platform filter if not 'all'.
    if platform_filter_str != 'all':
        try:
            platform_enum = PlatformNameEnum[platform_filter_str.upper()]
            query = query.filter(CampaignData.platform == platform_enum)
        except KeyError: # Handle invalid platform string.
            err_msg = f"Invalid platform: '{platform_filter_str}'."
            current_app.logger.warning(f"Bad request to /api/breakdown/country: {err_msg} (Params: {request.args})")
            return jsonify({"error": err_msg}), 400

    # Group by country code and platform, then order by country code.
    query = query.group_by(CampaignData.breakdown_value, CampaignData.platform).order_by(CampaignData.breakdown_value)
    results = query.all() # Execute the query.

    # --- Prepare data for Chart.js bar chart ---
    chart_labels = []       # Labels for x-axis (country codes).
    chart_data_values = []  # Corresponding metric values.
    dataset_label = ""      # Label for the dataset in the chart legend.

    aggregated_results = {} # Used if 'all' platforms to sum data across platforms for each country.

    # Aggregate results if 'all' platforms are selected.
    if platform_filter_str == 'all':
        for row in results:
            country = row.country_code if row.country_code else "Unknown" # Handle null country codes.
            current_val = aggregated_results.get(country, 0)
            aggregated_results[country] = current_val + (float(row.total_metric_value) if row.total_metric_value else 0)
        chart_labels = sorted(list(aggregated_results.keys())) # Sort country codes alphabetically.
        chart_data_values = [aggregated_results[label] for label in chart_labels]
        dataset_label = f"{metric_key.replace('_',' ').title()} by Country (All Platforms)"
    else: # If a specific platform is selected.
        for row in results:
            country = row.country_code if row.country_code else "Unknown"
            chart_labels.append(country)
            chart_data_values.append(float(row.total_metric_value) if row.total_metric_value else 0)
        dataset_label = f"{metric_key.replace('_',' ').title()} by Country ({platform_filter_str})"

    # Predefined background colors for chart bars.
    background_colors = ["#D97706", "#059669", "#7C3AED", "#DB2777", "#2563EB", "#FBBF24", "#4ADE80", "#A78BFA"]

    # Structure final chart data.
    final_chart_data = {
        "labels": chart_labels,
        "datasets": [{
            "label": dataset_label,
            "data": chart_data_values,
            "backgroundColor": background_colors[:len(chart_labels)] # Use a subset of colors.
        }]
    }

    # Handle cases where no data is found.
    if not results and not chart_labels:
        final_chart_data = {"labels": [], "datasets": [{"label": f"No {metric_key.replace('_',' ')} data for selected country breakdown", "data": [], "backgroundColor": []}]}

    return jsonify(final_chart_data)


# Main route for the unified dashboard page.
@dashboard_bp.route('/')
@login_required # User must be logged in to view the dashboard.
# @subscription_required(required_level_names=['Basic', 'Pro', 'Enterprise']) # Future: control access based on subscription.
def main_dashboard():
    """
    Renders the main unified dashboard page.
    This page will typically make AJAX calls to the API endpoints defined above
    to fetch and display various data visualizations and KPIs.
    """
    return render_template('dashboard/unified_dashboard.html')

[end of routes/dashboard.py]
