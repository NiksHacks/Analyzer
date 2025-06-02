from flask import request # For accessing request context (e.g., host_url).
from datetime import date, timedelta, datetime # For date calculations.
# Local import of urlparse and urljoin is done inside is_safe_url to avoid potential import cycles
# or making it a hard dependency if not always used, though in this small app it's less critical.

def is_safe_url(target):
    """
    Checks if a target URL is safe for redirection.
    A URL is considered safe if it has a scheme of 'http' or 'https'
    and its network location (netloc, i.e., domain) matches the application's host.
    This helps prevent open redirect vulnerabilities.

    Args:
        target (str or None): The URL to check. Can be relative or absolute.

    Returns:
        bool: True if the target URL is safe, False otherwise.
    """
    # Ensure target is not None and is a string; otherwise, it's not a valid URL to check.
    if target is None or not isinstance(target, str):
        return False

    from urllib.parse import urlparse, urljoin # Local import for URL parsing utilities.

    # Get the reference URL from the current request's host URL (e.g., "http://localhost:5000/").
    ref_url = urlparse(request.host_url)

    # Join the target URL with the host URL to handle relative paths correctly, then parse it.
    # For example, if host_url is "http://example.com/app/" and target is "nextpage",
    # urljoin makes it "http://example.com/app/nextpage".
    # If target is an absolute URL like "http://othersite.com", urljoin uses that directly.
    test_url = urlparse(urljoin(request.host_url, target))

    # Check if the scheme is HTTP or HTTPS and if the network location (domain) matches.
    return test_url.scheme in ('http', 'https') and \
           ref_url.netloc == test_url.netloc

def parse_date_range(request_args, default_range_str='last_7_days', get_previous_period=False):
    """
    Parses date range parameters from Flask request arguments (request.args).
    It supports predefined ranges like 'last_7_days', 'last_30_days', and 'custom'
    date ranges specified by 'start_date' and 'end_date' parameters.
    Optionally, it can return the dates for the period immediately preceding the main period.

    Args:
        request_args (werkzeug.datastructures.MultiDict): The request arguments object
                                                          (typically `request.args`).
        default_range_str (str, optional): The default date range string to use if
                                           'date_range' is not provided in request_args.
                                           Defaults to 'last_7_days'.
        get_previous_period (bool, optional): If True, the function calculates and returns
                                              the start and end dates for the period
                                              immediately preceding the main calculated period.
                                              The duration of this previous period will match
                                              the duration of the main period.
                                              Defaults to False.

    Returns:
        tuple: A tuple containing (start_date_obj, end_date_obj, error_response_tuple).
               - start_date_obj (date or None): The calculated start date.
               - end_date_obj (date or None): The calculated end date.
               - error_response_tuple (tuple or None): If an error occurs (e.g., invalid parameters),
                 this will be a tuple like ({"error": "message"}, http_status_code).
                 Otherwise, it's None.
    """
    # Get 'date_range' from request arguments, or use the default if not provided.
    date_range_str = request_args.get('date_range', default_range_str)
    today = date.today() # Current date, used as a reference for relative ranges.
    start_date_obj, end_date_obj = None, None # Initialize date objects.

    # --- Determine date objects based on date_range_str ---
    if date_range_str == 'last_7_days':
        end_date_obj = today # End date is today.
        start_date_obj = today - timedelta(days=6) # Start date is 6 days ago (total 7 days including today).
    elif date_range_str == 'last_30_days':
        end_date_obj = today # End date is today.
        start_date_obj = today - timedelta(days=29) # Start date is 29 days ago.
    elif date_range_str == 'custom':
        # For custom range, 'start_date' and 'end_date' must be provided in request_args.
        start_date_param = request_args.get('start_date')
        end_date_param = request_args.get('end_date')
        if not (start_date_param and end_date_param):
            # Return error if custom range is selected but dates are missing.
            return None, None, ({"error": "Custom date range requires 'start_date' and 'end_date' parameters (YYYY-MM-DD)."}, 400)
        try:
            # Parse date strings into date objects.
            start_date_obj = datetime.strptime(start_date_param, '%Y-%m-%d').date()
            end_date_obj = datetime.strptime(end_date_param, '%Y-%m-%d').date()
            # Validate that start_date is not after end_date.
            if start_date_obj > end_date_obj:
                return None, None, ({"error": "Start date cannot be after end date for custom range."}, 400)
        except ValueError: # Handle invalid date format.
            return None, None, ({"error": "Invalid date format for custom range. Please use YYYY-MM-DD."}, 400)
    else: # Handle invalid or unsupported date_range_str by falling back to default_range_str logic.
        # This ensures that if an unknown string is passed, it gracefully defaults.
        # This part effectively re-evaluates based on 'default_range_str'.
        if default_range_str == 'last_7_days':
            end_date_obj = today
            start_date_obj = today - timedelta(days=6)
        elif default_range_str == 'last_30_days':
            end_date_obj = today
            start_date_obj = today - timedelta(days=29)
        else: # This case should ideally not be reached if default_range_str is always valid.
             return None, None, ({"error": f"Invalid or unsupported default_range_str configured: {default_range_str}"}, 500)

    # --- Calculate previous period if requested ---
    if get_previous_period:
        # Ensure the main period dates were successfully parsed first.
        if start_date_obj and end_date_obj:
            duration_days = (end_date_obj - start_date_obj).days # Duration of the main period.
            # Previous period ends the day before the main period starts.
            prev_end_date = start_date_obj - timedelta(days=1)
            # Previous period starts 'duration_days' before its end date.
            prev_start_date = prev_end_date - timedelta(days=duration_days)
            return prev_start_date, prev_end_date, None # Return previous period dates.
        else:
            # Cannot calculate previous period if main period dates are invalid.
            return None, None, ({"error": "Cannot calculate previous period due to invalid dates for the main period."}, 400)

    # Return the calculated start and end dates for the main period, and no error.
    return start_date_obj, end_date_obj, None
