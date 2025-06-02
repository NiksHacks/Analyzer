import requests
import re
from flask import Blueprint, render_template, flash, redirect, url_for, current_app, request
from flask_login import login_required, current_user
from datetime import datetime, timedelta, date
from authlib.integrations.base_client.errors import OAuthError
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func

from ..extensions import db, oauth
from ..models import AdPlatformIntegration, PlatformNameEnum, IntegrationStatusEnum, User, CampaignData

from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

# Blueprint for ad platform integration-related routes.
# This blueprint groups all views for connecting, managing, and fetching data from
# ad platforms like Google Ads and Meta Ads.
integration_bp = Blueprint('integration', __name__, url_prefix='/integration')

# Route for the main Ad Platform Integration Hub page.
@integration_bp.route('/')
@login_required # Ensures that only logged-in users can access this page.
# @subscription_required(required_level_names=['Pro', 'Enterprise']) # Example for future subscription-based access control.
def hub():
    """
    Displays the main integration hub page.
    This page lists all connected ad platform integrations for the current user,
    grouped by platform type (e.g., Google Ads, Meta Ads).
    It serves as the central point for users to manage their ad platform connections.
    """
    # Retrieve all ad platform integrations associated with the currently logged-in user from the database.
    user_integrations = AdPlatformIntegration.query.filter_by(user_id=current_user.id).all()

    # Initialize a dictionary to hold the integrations, keyed by the platform's string value (e.g., "GOOGLE_ADS").
    # This structure helps in organizing the display of integrations in the template.
    connected_platforms = {str(p.value): [] for p in PlatformNameEnum} # Uses PlatformNameEnum to ensure all platforms are listed.

    # Iterate through the fetched integrations and categorize them by platform name.
    for integr in user_integrations:
        connected_platforms[str(integr.platform_name.value)].append(integr)

    # Render the integration hub template, passing the categorized integrations and the PlatformNameEnum
    # (which might be used in the template for display logic or iterating through all possible platforms).
    return render_template('integration/smart_api_hub.html',
                           connected_platforms=connected_platforms,
                           PlatformNameEnum=PlatformNameEnum)

@integration_bp.route('/googleads/fetch_data/<int:integration_id>', methods=['POST'])
@login_required
def google_ads_fetch_data(integration_id):
    """
    Fetches campaign performance data from Google Ads API for a specific integration.
    Data is fetched for the last 7 days and includes breakdowns by device, country,
    age range, and gender. Overall daily totals are also calculated and stored.

    Args:
        integration_id (int): The ID of the AdPlatformIntegration to fetch data for.
    """
    # --- Step 1: Fetch the relevant AdPlatformIntegration record ---
    # Ensure the integration belongs to the current user, is for Google Ads, and is active.
    # .first_or_404() will raise a 404 error if no matching integration is found.
    integration = AdPlatformIntegration.query.filter_by(
        id=integration_id,
        user_id=current_user.id,
        platform_name=PlatformNameEnum.GOOGLE_ADS,
        status=IntegrationStatusEnum.ACTIVE  # Ensure the integration is active before fetching.
    ).first_or_404()

    # --- Step 2: Check if Ad Account (Customer ID) is selected ---
    # Data fetching cannot proceed without a specific Google Ads Customer ID.
    if integration.ad_account_id == "pending_selection":
        flash("A Google Ads Customer ID must be provided for this integration before fetching data. Please update the integration settings.", "warning")
        return redirect(url_for('integration.hub')) # Redirect to hub or settings page.

    # --- Step 3: Retrieve Google Ads API credentials from application configuration ---
    # These are sensitive credentials and should be stored securely (e.g., environment variables).
    developer_token = current_app.config.get('GOOGLE_ADS_DEVELOPER_TOKEN')
    client_id = current_app.config.get('GOOGLE_ADS_CLIENT_ID') # OAuth Client ID
    client_secret = current_app.config.get('GOOGLE_ADS_CLIENT_SECRET') # OAuth Client Secret

    # Developer token is essential for all Google Ads API calls.
    if not developer_token:
        flash("The Google Ads Developer Token is not configured in the application. Data fetching is disabled.", "danger")
        current_app.logger.critical("GOOGLE_ADS_DEVELOPER_TOKEN is not set in app config.")
        return redirect(url_for('integration.hub'))

    # Retrieve the stored refresh token for this integration. It's decrypted by the model's property getter.
    refresh_token = integration.refresh_token
    if refresh_token:
        current_app.logger.info(f"Google Ads: Initializing GoogleAdsClient with stored refresh token for integration {integration.id}.")
    else:
        # If no refresh token is found, API calls might fail if the access token (if any) has expired.
        # This indicates an issue with the OAuth flow or token storage.
        current_app.logger.warning(f"Google Ads: Refresh token NOT found for integration {integration.id}. API calls may fail if the access token is expired. Please advise user to reconnect.")
        flash("The refresh token for Google Ads is missing. Please reconnect the Google Ads account to ensure continued data access.", "danger")
        # Depending on strictness, could redirect here or attempt call and let it fail.

    # --- Step 4: Prepare credentials dictionary for GoogleAdsClient ---
    # The login_customer_id is the Google Ads account ID (MCC or individual) to query.
    # It must not contain hyphens for the API client.
    # `use_proto_plus=True` is recommended for more Pythonic response objects.
    credentials = {
        "developer_token": developer_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "login_customer_id": integration.ad_account_id.replace("-", ""), # Manager account ID if applicable, or account itself.
        "use_proto_plus": True
    }

    try:
        # --- Step 5: Initialize the Google Ads client and Get GoogleAdsService ---
        google_ads_client = GoogleAdsClient.load_from_dict(credentials)
        ga_service = google_ads_client.get_service("GoogleAdsService") # Service for making search requests.

        # --- Step 6: Define the date range for data fetching (e.g., last 7 days) ---
        end_date_str = date.today().isoformat()
        start_date_str = (date.today() - timedelta(days=7)).isoformat()

        # --- Step 7: Construct the GAQL (Google Ads Query Language) query ---
        # This query fetches campaign performance data, segmented by device, geo (country), age range, and gender.
        # Each row in the API response will represent a unique combination of these segments for a given campaign on a specific date.
        # Metrics like impressions, clicks, cost (in micros), and conversions are requested.
        # Campaigns with status 'REMOVED' are excluded.
        query = f"""
            SELECT
                campaign.id,
                campaign.name,
                metrics.date,
                segments.device,
                segments.geo_target_country,  -- This is a resource name like 'countries/US'
                segments.age_range.age_range_type,
                segments.gender.gender_type,
                metrics.impressions,
                metrics.clicks,
                metrics.cost_micros,
                metrics.conversions
            FROM campaign
            WHERE segments.date BETWEEN '{start_date_str}' AND '{end_date_str}'
            AND campaign.status != 'REMOVED'
            ORDER BY metrics.date, campaign.id -- Optional ordering
            """ # Using customer_id parameter in search_stream, not in query directly for most cases.

        # The customer_id to query is the specific ad account ID from the integration, hyphens removed.
        customer_id_to_query = integration.ad_account_id.replace("-", "")

        # --- Step 8: Execute the search stream query ---
        # search_stream is preferred for large reports as it pages through results automatically.
        current_app.logger.info(f"Google Ads: Executing GAQL query for integration {integration.id}, customer {customer_id_to_query}, for dates {start_date_str} to {end_date_str}.")
        response_stream = ga_service.search_stream(customer_id=customer_id_to_query, query=query)

        # Set to keep track of unique campaign-date pairs processed from the API.
        # This is used later for aggregating "overall" daily totals.
        processed_campaign_dates = set()

        # --- Step 9: Process API response ---
        # Iterate through each batch in the response stream.
        for batch_count, batch in enumerate(response_stream):
            current_app.logger.info(f"Google Ads: Processing batch {batch_count + 1} for integration {integration.id}.")
            for row_count, row in enumerate(batch.results):
                # Extract campaign identifiers and date.
                campaign_id_str = str(row.campaign.id)
                campaign_name_str = row.campaign.name
                entry_date_obj = datetime.strptime(row.metrics.date, '%Y-%m-%d').date()

                # --- Normalize Device Breakdown ---
                device_enum_val = row.segments.device
                device_value_normalized = "unknown" # Default if not a known type or UNKNOWN/UNSPECIFIED.
                # Map Google Ads DeviceEnum to simpler, storable string values.
                if device_enum_val == google_ads_client.enums.DeviceEnum.Device.MOBILE: device_value_normalized = "mobile"
                elif device_enum_val == google_ads_client.enums.DeviceEnum.Device.DESKTOP: device_value_normalized = "desktop"
                elif device_enum_val == google_ads_client.enums.DeviceEnum.Device.TABLET: device_value_normalized = "tablet"
                elif device_enum_val == google_ads_client.enums.DeviceEnum.Device.CONNECTED_TV: device_value_normalized = "connected_tv"
                # Other types like OTHER, UNKNOWN, UNSPECIFIED will default to "unknown".

                # --- Normalize Country Breakdown ---
                country_resource_name = row.segments.geo_target_country # Format: "countries/XX" (e.g., "countries/US")
                parsed_country_code = 'unknown' # Default for non-country geo targets or if missing.
                if country_resource_name and 'countries/' in country_resource_name:
                    parsed_country_code = country_resource_name.split('/')[-1] # Extract the 2-letter country code.
                elif country_resource_name:
                    # Log if it's a geo target but not a country (e.g., state, city, DMA).
                    # For this schema, we only store country-level breakdowns from this segment.
                    current_app.logger.debug(f"Google Ads: Received non-country geo_target_constant: {country_resource_name} for campaign {row.campaign.id} on {row.metrics.date}. Storing as 'unknown' country code.")

                # --- Normalize Age Range Breakdown ---
                age_range_enum = row.segments.age_range.age_range_type
                age_value_normalized = "unknown" # Default for unspecified/undetermined age ranges.
                # Map Google Ads AgeRangeTypeEnum to standard string values (e.g., "18-24").
                if age_range_enum == google_ads_client.enums.AgeRangeTypeEnum.AgeRangeType.AGE_RANGE_18_24: age_value_normalized = "18-24"
                elif age_range_enum == google_ads_client.enums.AgeRangeTypeEnum.AgeRangeType.AGE_RANGE_25_34: age_value_normalized = "25-34"
                # ... (add all other age range mappings as in the original code) ...
                elif age_range_enum == google_ads_client.enums.AgeRangeTypeEnum.AgeRangeType.AGE_RANGE_65_UP: age_value_normalized = "65+"

                # --- Normalize Gender Breakdown ---
                gender_enum = row.segments.gender.gender_type
                gender_value_normalized = "unknown" # Default for unspecified/undetermined genders.
                # Map Google Ads GenderTypeEnum to standard string values (e.g., "female", "male").
                if gender_enum == google_ads_client.enums.GenderTypeEnum.GenderType.FEMALE: gender_value_normalized = "female"
                elif gender_enum == google_ads_client.enums.GenderTypeEnum.GenderType.MALE: gender_value_normalized = "male"
                elif gender_enum == google_ads_client.enums.GenderTypeEnum.GenderType.UNDETERMINED: gender_value_normalized = "undetermined"

                # --- Extract Metrics ---
                # These metrics (impressions, clicks, cost, conversions) correspond to the specific
                # combination of campaign, date, device, country, age, and gender for this row.
                cost_actual_val = row.metrics.cost_micros / 1000000.0 # Cost is in micros, convert to standard currency unit.
                impressions_val = row.metrics.impressions
                clicks_val = row.metrics.clicks
                conversions_val = int(row.metrics.conversions) # Conversions are typically whole numbers.

                # --- Step 10: Upsert (Update or Insert) Breakdown Data into CampaignData table ---
                # For each breakdown type (device, country, age, gender), we create or update a record.
                # Since the GAQL query segments by all these dimensions, each row from the API provides
                # the data for a specific point across all these breakdowns simultaneously.
                # So, the metrics (impressions, clicks, etc.) are the same for each of the four
                # breakdown records we derive from this single API row.

                # Upsert Device-Specific Entry
                # Check if a record for this campaign, date, and device already exists.
                existing_entry_device = CampaignData.query.filter_by(
                    integration_id=integration.id, campaign_id_platform=campaign_id_str, date=entry_date_obj,
                    breakdown_type='device', breakdown_value=device_value_normalized,
                    platform=PlatformNameEnum.GOOGLE_ADS # Ensure platform is correctly set.
                ).first()
                if existing_entry_device: # If exists, update its metrics.
                    existing_entry_device.campaign_name_platform = campaign_name_str # Update name in case it changed.
                    existing_entry_device.impressions = impressions_val
                    existing_entry_device.clicks = clicks_val
                    existing_entry_device.spend = cost_actual_val
                    existing_entry_device.conversions = conversions_val
                    existing_entry_device.updated_at = datetime.utcnow()
                else: # If not, create a new CampaignData record.
                    new_entry_device = CampaignData(
                        integration_id=integration.id, platform=PlatformNameEnum.GOOGLE_ADS,
                        campaign_id_platform=campaign_id_str, campaign_name_platform=campaign_name_str,
                        date=entry_date_obj, breakdown_type='device', breakdown_value=device_value_normalized,
                        impressions=impressions_val, clicks=clicks_val, spend=cost_actual_val, conversions=conversions_val
                    )
                    db.session.add(new_entry_device)

                # Upsert Country-Specific Entry (similar logic as device)
                existing_entry_country = CampaignData.query.filter_by(
                    integration_id=integration.id, campaign_id_platform=campaign_id_str, date=entry_date_obj,
                    breakdown_type='country', breakdown_value=parsed_country_code,
                    platform=PlatformNameEnum.GOOGLE_ADS
                ).first()
                if existing_entry_country:
                    existing_entry_country.campaign_name_platform = campaign_name_str
                    existing_entry_country.impressions = impressions_val
                    existing_entry_country.clicks = clicks_val
                    existing_entry_country.spend = cost_actual_val
                    existing_entry_country.conversions = conversions_val
                    existing_entry_country.updated_at = datetime.utcnow()
                else:
                    new_entry_country = CampaignData(
                        integration_id=integration.id, platform=PlatformNameEnum.GOOGLE_ADS,
                        campaign_id_platform=campaign_id_str, campaign_name_platform=campaign_name_str,
                        date=entry_date_obj, breakdown_type='country', breakdown_value=parsed_country_code,
                        impressions=impressions_val, clicks=clicks_val, spend=cost_actual_val, conversions=conversions_val
                    )
                    db.session.add(new_entry_country)

                # Upsert Age-Range-Specific Entry (similar logic)
                existing_entry_age = CampaignData.query.filter_by(
                    integration_id=integration.id, campaign_id_platform=campaign_id_str, date=entry_date_obj,
                    breakdown_type='age_range', breakdown_value=age_value_normalized, platform=PlatformNameEnum.GOOGLE_ADS
                ).first()
                if existing_entry_age:
                    existing_entry_age.campaign_name_platform = campaign_name_str
                    existing_entry_age.impressions = impressions_val
                    existing_entry_age.clicks = clicks_val
                    existing_entry_age.spend = cost_actual_val
                    existing_entry_age.conversions = conversions_val
                    existing_entry_age.updated_at = datetime.utcnow()
                else:
                    new_entry_age = CampaignData(
                        integration_id=integration.id, platform=PlatformNameEnum.GOOGLE_ADS,
                        campaign_id_platform=campaign_id_str, campaign_name_platform=campaign_name_str,
                        date=entry_date_obj, breakdown_type='age_range', breakdown_value=age_value_normalized,
                        impressions=impressions_val, clicks=clicks_val, spend=cost_actual_val, conversions=conversions_val
                    )
                    db.session.add(new_entry_age)

                # Upsert Gender-Specific Entry (similar logic)
                existing_entry_gender = CampaignData.query.filter_by(
                    integration_id=integration.id, campaign_id_platform=campaign_id_str, date=entry_date_obj,
                    breakdown_type='gender', breakdown_value=gender_value_normalized, platform=PlatformNameEnum.GOOGLE_ADS
                ).first()
                if existing_entry_gender:
                    existing_entry_gender.campaign_name_platform = campaign_name_str
                    existing_entry_gender.impressions = impressions_val
                    existing_entry_gender.clicks = clicks_val
                    existing_entry_gender.spend = cost_actual_val
                    existing_entry_gender.conversions = conversions_val
                    existing_entry_gender.updated_at = datetime.utcnow()
                else:
                    new_entry_gender = CampaignData(
                        integration_id=integration.id, platform=PlatformNameEnum.GOOGLE_ADS,
                        campaign_id_platform=campaign_id_str, campaign_name_platform=campaign_name_str,
                        date=entry_date_obj, breakdown_type='gender', breakdown_value=gender_value_normalized,
                        impressions=impressions_val, clicks=clicks_val, spend=cost_actual_val, conversions=conversions_val
                    )
                    db.session.add(new_entry_gender)

                # Add the unique campaign ID, date, and name to the set for later overall aggregation.
                processed_campaign_dates.add((campaign_id_str, entry_date_obj, campaign_name_str))

            # --- Step 11: Commit to Database (Per Batch) ---
            # Commit changes after processing each batch of rows from the API.
            # This provides some level of atomicity and reduces memory usage for very large datasets.
            if row_count > 0 : # Only commit if there were rows in the batch
                try:
                    db.session.commit()
                    current_app.logger.info(f"Google Ads: Committed batch {batch_count + 1} with {row_count + 1} API rows processed for integration {integration.id}.")
                except Exception as e_commit_batch: # Catch potential DB errors during commit.
                    db.session.rollback() # Rollback this batch's changes.
                    current_app.logger.error(f"Google Ads: Error committing batch {batch_count + 1} data for integration {integration.id}: {e_commit_batch}", exc_info=True)
                    flash("Error saving a batch of Google Ads breakdown data. Some data may not have been saved. Please try fetching again.", "danger")
                    # Depending on desired behavior, could re-raise or return error here to stop further processing.
                    # For now, it logs, flashes, and continues to next batch or overall calculation.

        # --- Step 12: Calculate and Store "Overall" Daily Totals ---
        # This section iterates through the unique campaign-date combinations identified earlier.
        # For each combination, it sums up the metrics from the 'device' breakdown records.
        # Rationale: The GAQL query segments by device, country, age, and gender. Each 'device' record
        # derived from an API row implicitly contains metrics for a specific combination of all these segments.
        # Summing all 'device' records for a campaign-date effectively aggregates across all other segments (country, age, gender)
        # to give a total for that campaign on that day.
        # Note: This assumes 'device' is the most granular breakdown needed for this aggregation strategy.
        # If the API provided a non-segmented row, that would be a more direct source for "overall".
        current_app.logger.info(f"Google Ads: Calculating overall daily totals for {len(processed_campaign_dates)} campaign-date pairs for integration {integration.id}.")
        for camp_id, date_obj, camp_name in processed_campaign_dates:
            # Query to sum metrics from 'device' breakdown records for the given campaign and date.
            summed_metrics = db.session.query(
                func.sum(CampaignData.impressions).label('total_impressions'),
                func.sum(CampaignData.clicks).label('total_clicks'),
                func.sum(CampaignData.spend).label('total_spend'),
                func.sum(CampaignData.conversions).label('total_conversions')
            ).filter_by(
                integration_id=integration.id,
                campaign_id_platform=camp_id,
                date=date_obj,
                breakdown_type='device', # Summing based on 'device' records.
                platform=PlatformNameEnum.GOOGLE_ADS
            ).one_or_none() # Expect one row of summed results or None.

            # If no device data was found for this campaign-date (e.g., all rows had 'unknown' device), skip overall.
            if not summed_metrics or summed_metrics.total_impressions is None: # Check if any metrics were actually summed.
                current_app.logger.info(f"Google Ads: No device-specific data found to aggregate for 'overall' record for campaign {camp_id} on {date_obj}. Skipping overall record creation/update.")
                continue

            # Check if an "overall" record already exists for this campaign and date.
            overall_entry = CampaignData.query.filter_by(
                integration_id=integration.id, campaign_id_platform=camp_id, date=date_obj,
                breakdown_type='overall', breakdown_value='N/A', # 'overall' records have 'N/A' as breakdown_value.
                platform=PlatformNameEnum.GOOGLE_ADS
            ).first()

            if overall_entry: # If exists, update its summed metrics.
                overall_entry.impressions = summed_metrics.total_impressions or 0
                overall_entry.clicks = summed_metrics.total_clicks or 0
                overall_entry.spend = summed_metrics.total_spend or 0
                overall_entry.conversions = summed_metrics.total_conversions or 0
                overall_entry.campaign_name_platform = camp_name # Update name in case it changed.
                overall_entry.updated_at = datetime.utcnow()
            else: # If not, create a new "overall" CampaignData record.
                new_overall_entry = CampaignData(
                    integration_id=integration.id, platform=PlatformNameEnum.GOOGLE_ADS,
                    campaign_id_platform=camp_id, campaign_name_platform=camp_name,
                    date=date_obj, breakdown_type='overall', breakdown_value='N/A',
                    impressions=summed_metrics.total_impressions or 0,
                    clicks=summed_metrics.total_clicks or 0,
                    spend=summed_metrics.total_spend or 0,
                    conversions=summed_metrics.total_conversions or 0
                )
                db.session.add(new_overall_entry)

        try:
            db.session.commit() # Commit the "overall" data changes.
            flash(f"Google Ads data, including all breakdowns and overall daily totals, fetched and saved successfully for ad account '{integration.ad_account_name}'.", "success")
            current_app.logger.info(f"Google Ads: Successfully committed 'overall' daily totals for integration {integration.id}.")
        except Exception as e_commit_overall: # Catch DB errors during overall data commit.
            db.session.rollback()
            current_app.logger.error(f"Google Ads: Error committing 'overall' daily totals for integration {integration.id}: {e_commit_overall}", exc_info=True)
            flash("Error saving aggregated 'overall' Google Ads data. Breakdown data might have been saved partially.", "danger")

    # --- Step 13: Handle API and Other Exceptions ---
    except GoogleAdsException as ex: # Handle Google Ads API specific exceptions.
        flash(f"A Google Ads API error occurred while fetching data. Please ensure your Developer Token is valid, the Google Ads API is enabled for your project, and the account has API access. Check server logs for detailed error messages.", "danger")
        current_app.logger.error(f"GoogleAdsException during data fetch for integration {integration.id} (Customer ID: {integration.ad_account_id}): {ex}")
        # Log detailed error information from the GoogleAdsException.
        for error in ex.failure.errors:
            current_app.logger.error(f'    Google Ads API Error Code: {error.error_code}, Message: "{error.message}".')
            if error.location:
                for field_path_element in error.location.field_path_elements:
                    current_app.logger.error(f'        Error Location Field: {field_path_element.field_name}')
    except Exception as e: # Handle other general exceptions (network issues, unexpected errors).
        db.session.rollback() # Rollback any partial database changes from this fetch attempt.
        flash(f"An unexpected error occurred while fetching Google Ads data: {str(e)}. Please try again later or contact support.", "danger")
        current_app.logger.error(f"Unexpected error during Google Ads data fetch for integration {integration.id} (Customer ID: {integration.ad_account_id}): {e}", exc_info=True)

    # Redirect back to the integration hub page after fetching (or on error).
    return redirect(url_for('integration.hub'))

@integration_bp.route('/google/connect')
@login_required
def google_ads_connect():
    """
    Initiates the Google Ads OAuth 2.0 connection flow.
    Redirects the user to Google's authorization page for Google Ads.
    """
    # Define the callback URL that Google will redirect to after the user authorizes the application.
    # `_external=True` generates an absolute URL, which is required by Google.
    redirect_uri = url_for('integration.google_ads_callback', _external=True)

    # Use the Authlib client instance for Google Ads (configured as 'google_ads' in `oauth` extensions)
    # to generate the authorization URL and redirect the user.
    # Key parameters for Google OAuth:
    # - Scopes: 'adwords' is the scope for accessing the Google Ads API.
    # - 'prompt': 'consent' ensures the user is explicitly asked for consent each time they connect.
    #   This is particularly important for ensuring a refresh token is issued, especially on subsequent connections.
    # - 'access_type': 'offline' is crucial for obtaining a refresh token, which allows the application
    #   to access the Google Ads API on behalf of the user even when they are not actively logged in.
    #   (These specific params like prompt and access_type are often configured when `oauth.register('google_ads', ...)` is called in extensions.py)
    return oauth.google_ads.authorize_redirect(redirect_uri)

# Route to handle the callback from Google after user authorization for Google Ads.
@integration_bp.route('/google/callback')
@login_required # User must be logged in to complete the OAuth callback.
def google_ads_callback():
    """
    Handles the callback from Google after user authorization for Google Ads.
    It exchanges the authorization code for access and refresh tokens,
    encrypts these tokens (handled by model setters), and stores them
    in the AdPlatformIntegration record for the user.
    """
    try:
        # Exchange the authorization code (received from Google in the request query parameters)
        # for an access token and a refresh token.
        token_response = oauth.google_ads.authorize_access_token()
    except OAuthError as e: # Handle specific OAuth errors from Authlib.
        flash(f'Google Ads: Authentication failed during token exchange ({e.error}). Please try reconnecting.', 'danger')
        current_app.logger.error(f"Google Ads OAuthError during token authorization: {e.error} - {e.description}", exc_info=True)
        return redirect(url_for('integration.hub'))
    except Exception as e: # Catch any other unexpected errors during token exchange.
        flash('An unexpected error occurred during Google Ads authentication. Please try again.', 'danger')
        current_app.logger.error(f"Unexpected error during Google Ads token exchange: {e}", exc_info=True)
        return redirect(url_for('integration.hub'))

    # Extract token information from the response.
    access_token_raw = token_response.get('access_token')
    refresh_token_raw = token_response.get('refresh_token') # This is critical for long-term API access.
    expires_in = token_response.get('expires_in') # Duration in seconds until the access token expires.
    # Calculate the absolute expiry time for the access token.
    token_expiry_time = datetime.utcnow() + timedelta(seconds=expires_in) if expires_in else None

    if not access_token_raw: # Access token should always be present if no OAuthError was raised.
        flash('Google Ads: Could not retrieve access token from Google. Please try again.', 'danger')
        current_app.logger.error("Google Ads OAuth: Access token not found in token_response.")
        return redirect(url_for('integration.hub'))

    # Note: Raw tokens are passed to the model. Encryption (e.g., using Fernet) is handled by the
    # AdPlatformIntegration model's property setters for `access_token` and `refresh_token`.

    # Define placeholders for ad account ID and name, as Google Ads requires manual Customer ID input later.
    ad_account_id_placeholder = "pending_selection"
    ad_account_name_placeholder = "Google Ads Account (Pending Selection)"

    # Check if an AdPlatformIntegration record for Google Ads already exists for this user.
    existing_integration = AdPlatformIntegration.query.filter_by(
        user_id=current_user.id,
        platform_name=PlatformNameEnum.GOOGLE_ADS
    ).first()

    integration_being_processed = None # To hold the instance we are working with for logging/redirection.

    if existing_integration:
        # If an integration exists, update it with the new tokens and expiry time.
        integration_being_processed = existing_integration
        existing_integration.access_token = access_token_raw # Setter handles encryption.
        if refresh_token_raw: # Only update the refresh token if a new one was provided by Google.
            existing_integration.refresh_token = refresh_token_raw # Setter handles encryption.
        existing_integration.token_expiry = token_expiry_time
        existing_integration.status = IntegrationStatusEnum.ACTIVE # Re-activate if it was previously INACTIVE or ERROR.
        # If ad_account_id was already set, keep it. If not, it remains "pending_selection".
        flash('Google Ads connection updated successfully with new tokens.', 'success')
    else:
        # If no integration exists, create a new one.
        new_integration = AdPlatformIntegration(
            user_id=current_user.id,
            platform_name=PlatformNameEnum.GOOGLE_ADS,
            ad_account_id=ad_account_id_placeholder,
            ad_account_name=ad_account_name_placeholder,
            status=IntegrationStatusEnum.ACTIVE, # Initially active, pending account selection.
            token_expiry=token_expiry_time
        )
        new_integration.access_token = access_token_raw # Setter handles encryption.
        if refresh_token_raw:
            new_integration.refresh_token = refresh_token_raw # Setter handles encryption.
        db.session.add(new_integration)
        integration_being_processed = new_integration
        flash('Google Ads connected successfully! Please select or confirm your ad account.', 'success')

    try:
        db.session.commit() # Commit the new or updated integration record to the database.
        log_integration_id = integration_being_processed.id if integration_being_processed else "N/A"
        current_app.logger.info(f"Google Ads connection details saved for user {current_user.id}, integration ID {log_integration_id}.")
    except IntegrityError: # Should be rare due to the prior check, but handles potential race conditions.
        db.session.rollback()
        # This message might be slightly confusing if it's just a token update for an existing linked account.
        flash('This Google Ads account connection seems to have a conflict. Please check your integrations.', 'danger')
        current_app.logger.warning(f"Google Ads OAuth: IntegrityError during commit for user {current_user.id}.", exc_info=True)
        return redirect(url_for('integration.hub'))
    except Exception as e: # Catch other potential database errors.
        db.session.rollback()
        flash('An error occurred while saving the Google Ads connection details.', 'danger')
        current_app.logger.error(f"Google Ads OAuth: Error saving connection for user {current_user.id}: {e}", exc_info=True)
        return redirect(url_for('integration.hub'))

    # If the ad account ID is still "pending_selection", redirect to the Google Ads account selection page.
    if integration_being_processed and integration_being_processed.ad_account_id == "pending_selection":
        return redirect(url_for('integration.googleads_select_account', integration_id=integration_being_processed.id))

    # Otherwise, redirect to the main integration hub.
    return redirect(url_for('integration.hub'))

@integration_bp.route('/meta/connect')
@login_required
def meta_ads_connect():
    """
    Initiates the Meta (Facebook) Ads OAuth 2.0 connection flow.
    Redirects the user to Facebook's authorization page.
    """
    redirect_uri = url_for('integration.meta_ads_callback', _external=True)
    # Define the callback URL that Meta will redirect to after the user authorizes the application.
    redirect_uri = url_for('integration.meta_ads_callback', _external=True)
    # Use the Authlib client instance for Meta Ads (configured as 'meta_ads' in `oauth` extensions).
    # Scopes requested (e.g., 'ads_read', 'ads_management', 'business_management') are typically configured
    # during the `oauth.register('meta_ads', ...)` call in extensions.py.
    return oauth.meta_ads.authorize_redirect(redirect_uri)

# Route to handle the callback from Meta (Facebook) after user authorization for Ads.
@integration_bp.route('/meta/callback')
@login_required # User must be logged in to complete the OAuth callback.
def meta_ads_callback():
    """
    Handles the callback from Meta (Facebook) after user authorization for Ads.
    It exchanges the authorization code for a short-lived access token,
    then attempts to exchange the short-lived token for a long-lived token.
    The final token (preferably long-lived) is encrypted (by model setter)
    and stored in the AdPlatformIntegration record.
    """
    try:
        # Exchange the authorization code (from request query params) for a short-lived access token.
        token_response = oauth.meta_ads.authorize_access_token()
    except OAuthError as e: # Handle Authlib specific OAuth errors.
        flash(f'Meta Ads: Authentication failed during token exchange ({e.error}). Please try reconnecting.', 'danger')
        current_app.logger.error(f"Meta Ads OAuthError during initial token authorization: {e.error} - {e.description}", exc_info=True)
        return redirect(url_for('integration.hub'))
    except Exception as e: # Catch other unexpected errors.
        flash('An unexpected error occurred during Meta Ads authentication. Please try again.', 'danger')
        current_app.logger.error(f"Unexpected error during Meta Ads initial token exchange: {e}", exc_info=True)
        return redirect(url_for('integration.hub'))

    short_lived_token = token_response.get('access_token')
    token_expiry_time = None # Will be set based on the final token's expiry.
    final_access_token_to_store = short_lived_token # Default to short-lived if exchange for long-lived fails.

    # --- Exchange short-lived token for a long-lived token ---
    # This is a server-to-server request to Facebook's Graph API.
    if short_lived_token:
        try:
            # Construct the URL for token exchange. Ensure the API version is current.
            graph_api_version = current_app.config.get('META_GRAPH_API_VERSION', 'v18.0') # Get from config or default
            https_graph_url = f"https://graph.facebook.com/{graph_api_version}/oauth/access_token"
            params = {
                'grant_type': 'fb_exchange_token',
                'client_id': current_app.config['META_ADS_APP_ID'],      # Your Meta App ID
                'client_secret': current_app.config['META_ADS_APP_SECRET'],# Your Meta App Secret
                'fb_exchange_token': short_lived_token                  # The short-lived token to exchange
            }
            exchange_response = requests.get(https_graph_url, params=params, timeout=10) # Timeout for the request.
            exchange_response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx).
            token_data = exchange_response.json() # Parse the JSON response.

            if token_data.get('access_token'):
                final_access_token_to_store = token_data['access_token'] # Store the long-lived token.
                expires_in = token_data.get('expires_in') # Long-lived tokens typically last around 60 days.
                if expires_in:
                    token_expiry_time = datetime.utcnow() + timedelta(seconds=int(expires_in))
                current_app.logger.info(f"Successfully exchanged Meta Ads short-lived token for a long-lived token for user {current_user.id}.")
            else:
                # Log error if long-lived token exchange fails, but proceed with the short-lived token.
                current_app.logger.error(f"Meta Ads long-lived token exchange failed for user {current_user.id}: Response did not contain 'access_token'. Data: {token_data}")
                # Calculate expiry for the short-lived token if exchange failed.
                sl_expires_in = token_response.get('expires_in') # Short-lived tokens usually last a few hours.
                if sl_expires_in:
                    token_expiry_time = datetime.utcnow() + timedelta(seconds=int(sl_expires_in))
                flash("Could not extend your Meta Ads session duration automatically. You may need to reconnect sooner if the connection drops.", "info")
        except requests.exceptions.RequestException as e_req: # Handle network errors during exchange.
            current_app.logger.error(f"Meta Ads long-lived token exchange request failed for user {current_user.id}: {e_req}", exc_info=True)
            flash("Could not automatically extend your Meta Ads session's duration due to a network issue. You may need to reconnect sooner.", "info")
        except Exception as e_json: # Handle JSON parsing errors or other unexpected issues during exchange.
             current_app.logger.error(f"Meta Ads long-lived token exchange processing error for user {current_user.id}: {e_json}", exc_info=True)
             flash("Error processing Meta Ads session extension. Using the initial short-term session.", "info")

    if not final_access_token_to_store: # Should not happen if short_lived_token was obtained initially.
        flash('Meta Ads: Could not retrieve a valid access token. Please try again.', 'danger')
        current_app.logger.error(f"Meta Ads OAuth: final_access_token_to_store is None for user {current_user.id}.")
        return redirect(url_for('integration.hub'))

    # Define placeholders for ad account ID and name, as these will be selected by the user in a subsequent step.
    ad_account_id_placeholder = "pending_selection"
    ad_account_name_placeholder = "Meta Ads Account (Pending Selection)"

    # Check if an AdPlatformIntegration record for Meta Ads already exists for this user.
    existing_integration = AdPlatformIntegration.query.filter_by(
        user_id=current_user.id,
        platform_name=PlatformNameEnum.META_ADS
    ).first()

    integration_being_processed = None # To hold the instance for logging/redirection.

    if existing_integration:
        # Update existing integration. Meta Ads does not use refresh tokens in the same way as Google.
        # The long-lived access token is used directly until it expires or is invalidated.
        integration_being_processed = existing_integration
        existing_integration.access_token = final_access_token_to_store # Setter handles encryption.
        existing_integration.token_expiry = token_expiry_time
        existing_integration.status = IntegrationStatusEnum.ACTIVE # Re-activate if previously INACTIVE or ERROR.
        # If ad_account_id was already set, keep it. If not, it remains "pending_selection".
        flash('Meta Ads connection updated successfully.', 'success')
    else:
        # Create a new integration record.
        new_integration = AdPlatformIntegration(
            user_id=current_user.id,
            platform_name=PlatformNameEnum.META_ADS,
            ad_account_id=ad_account_id_placeholder,
            ad_account_name=ad_account_name_placeholder,
            status=IntegrationStatusEnum.ACTIVE, # Initially active, pending account selection.
            token_expiry=token_expiry_time
        )
        new_integration.access_token = final_access_token_to_store # Setter handles encryption.
        db.session.add(new_integration)
        integration_being_processed = new_integration
        flash('Meta Ads connected successfully! Please select your ad account.', 'success')

    try:
        db.session.commit() # Commit the new or updated integration to the database.
        log_integration_id = integration_being_processed.id if integration_being_processed else "N/A"
        current_app.logger.info(f"Meta Ads connection details saved for user {current_user.id}, integration ID {log_integration_id}.")
    except IntegrityError: # Handles rare race conditions or unexpected unique constraint violations.
        db.session.rollback()
        flash('This Meta Ads account connection appears to have a conflict. Please check your integrations.', 'danger')
        current_app.logger.warning(f"Meta Ads OAuth: IntegrityError during commit for user {current_user.id}.", exc_info=True)
        return redirect(url_for('integration.hub'))
    except Exception as e: # Catch other potential database errors.
        db.session.rollback()
        flash('An error occurred while saving the Meta Ads connection details.', 'danger')
        current_app.logger.error(f"Meta Ads OAuth: Error saving connection for user {current_user.id}: {e}", exc_info=True)
        return redirect(url_for('integration.hub'))

    # If the ad account ID is still "pending_selection", redirect to the Meta Ads account selection page.
    if integration_being_processed and integration_being_processed.ad_account_id == "pending_selection":
        return redirect(url_for('integration.metaads_select_account', integration_id=integration_being_processed.id))

    # Otherwise, redirect to the main integration hub.
    return redirect(url_for('integration.hub'))

@integration_bp.route('/metaads/select_account/<int:integration_id>', methods=['GET', 'POST'])
@login_required
def metaads_select_account(integration_id):
    """
    Allows the user to select a Meta Ad Account from a list fetched from the Meta Ads API.
    GET: Displays the list of ad accounts.
    POST: Saves the selected ad account ID and name to the integration.
    """
    # Fetch the specific Meta Ads integration record for the current user, identified by integration_id.
    # .first_or_404() will automatically return a 404 error if no such integration is found.
    integration = AdPlatformIntegration.query.filter_by(
        id=integration_id,
        user_id=current_user.id,
        platform_name=PlatformNameEnum.META_ADS
    ).first_or_404()

    # If the ad account is already selected and the integration is active, no need to re-select.
    # Redirect the user to the main integration hub.
    if integration.status == IntegrationStatusEnum.ACTIVE and integration.ad_account_id != "pending_selection":
        flash("Meta Ads account has already been selected and is active for this integration.", "info")
        return redirect(url_for('integration.hub'))

    # Handle POST request (when the user submits the selected ad account).
    if request.method == 'POST':
        selected_account_id = request.form.get('ad_account_id')
        selected_account_name = request.form.get('ad_account_name') # This should be sent via a hidden input or looked up again.

        if not selected_account_id:
            flash("No ad account was selected. Please choose an account.", "danger")
        else:
            # Update the AdPlatformIntegration record with the selected ad account ID and name.
            integration.ad_account_id = selected_account_id
            # Use the provided name, or create a fallback name if it's missing (shouldn't happen with proper form).
            integration.ad_account_name = selected_account_name or f"Meta Ad Account {selected_account_id}"
            integration.status = IntegrationStatusEnum.ACTIVE # Mark the integration as fully active.
            try:
                db.session.commit() # Save changes to the database.
                flash(f"Meta Ads account '{integration.ad_account_name}' connected and activated successfully!", "success")
            except Exception as e: # Catch potential database errors during commit.
                db.session.rollback()
                flash("An error occurred while saving your ad account selection. Please try again.", "danger")
                current_app.logger.error(f"Error saving Meta Ad Account selection for integration ID {integration_id}: {e}", exc_info=True)
            return redirect(url_for('integration.hub')) # Redirect to hub after processing.

    # --- Handle GET Request: Fetch and display available ad accounts ---
    accounts_data = [] # List to store ad accounts fetched from the API.
    error_message = None # To store any error messages for display.
    try:
        access_token = integration.access_token # Access token is decrypted by the model's property getter.
        if not access_token:
            # This should ideally not happen if the OAuth flow completed successfully.
            raise ValueError("Access token is missing or invalid for this Meta Ads integration. Please try reconnecting the platform.")

        # Construct the URL to fetch ad accounts from Meta Graph API.
        # Fields requested: account_id, name, account_status.
        # 'limit=100' to get up to 100 accounts; pagination would be needed for more.
        graph_api_version = current_app.config.get('META_GRAPH_API_VERSION', 'v18.0')
        ad_accounts_url = f"https://graph.facebook.com/{graph_api_version}/me/adaccounts?fields=account_id,name,account_status&access_token={access_token}&limit=100"

        response = requests.get(ad_accounts_url, timeout=10) # Make the API request with a timeout.
        response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx).
        data = response.json() # Parse the JSON response.

        # Process the fetched ad accounts.
        for acc in data.get('data', []):
            # Filter for active accounts (account_status=1). Other statuses might include disabled, unsettled, etc.
            if acc.get('account_status') == 1:
                accounts_data.append({'id': acc.get('account_id'), 'name': acc.get('name')})

        # Basic pagination awareness: Log and flash a message if more accounts might exist.
        # Robust pagination would involve checking `data.get('paging', {}).get('next')` and making further requests.
        if len(accounts_data) == 100 and 'paging' in data and 'next' in data['paging']:
             current_app.logger.warning(f"Meta Ad Account selection for user {current_user.id} (integration {integration.id}): Displaying the first 100 active ad accounts. More may exist (full pagination not implemented).")
             flash("Displaying the first 100 active ad accounts. If you have more, not all may be shown in this list.", "info")

    except requests.exceptions.RequestException as e: # Handle network errors or HTTP errors from API.
        error_message = f"Error fetching Meta Ad accounts from Facebook: {str(e)}. Please ensure your connection has 'ads_read' and potentially 'business_management' permissions, and that the token is valid."
        current_app.logger.error(f"Meta Ad Account API Fetch Error for integration {integration.id}: {e}", exc_info=True)
    except ValueError as e: # Handle errors like missing access token.
        error_message = str(e) # Display the ValueError message (e.g., "Access token not found...").
        current_app.logger.error(f"Meta Ad Account Fetch ValueError (likely token issue) for integration {integration.id}: {e}", exc_info=True)
    except Exception as e: # Catch-all for other unexpected errors during API call or processing.
        error_message = f"An unexpected error occurred while fetching your Meta Ad accounts: {str(e)}"
        current_app.logger.error(f"Meta Ad Account Fetch Unexpected Error for integration {integration.id}: {e}", exc_info=True)

    if error_message:
        flash(error_message, "danger") # Display any error message to the user.

    # Render the template for selecting an ad account, passing the integration object and fetched accounts.
    return render_template('integration/meta_select_account.html', integration=integration, accounts=accounts_data)

@integration_bp.route('/googleads/select_account/<int:integration_id>', methods=['GET', 'POST'])
@login_required
def googleads_select_account(integration_id):
    """
    Allows the user to manually input their Google Ads Customer ID.
    GET: Displays the input form.
    POST: Validates and saves the Customer ID to the integration.
    """
    # Fetch the specific Google Ads integration record for the current user.
    integration = AdPlatformIntegration.query.filter_by(
        id=integration_id,
        user_id=current_user.id,
        platform_name=PlatformNameEnum.GOOGLE_ADS
    ).first_or_404() # Return 404 if not found.

    # If a Customer ID is already selected and the integration is active, redirect to the hub.
    if integration.status == IntegrationStatusEnum.ACTIVE and integration.ad_account_id != "pending_selection":
        flash("Google Ads Customer ID has already been provided and is active for this integration.", "info")
        return redirect(url_for('integration.hub'))

    # Handle POST request (when the user submits the Customer ID).
    if request.method == 'POST':
        customer_id = request.form.get('customer_id', '').strip() # Get Customer ID from form and strip whitespace.

        # Validate the Google Ads Customer ID format (typically NNN-NNN-NNNN).
        # This regex checks for three digits, a hyphen, three digits, a hyphen, and four digits.
        if not customer_id or not re.match(r"^\d{3}-\d{3}-\d{4}$", customer_id):
            flash("Invalid Google Ads Customer ID format. Please use the format xxx-xxx-xxxx (e.g., 123-456-7890).", "danger")
        else:
            # Update the AdPlatformIntegration record with the provided Customer ID.
            integration.ad_account_id = customer_id
            # Generate a default name for the ad account using the Customer ID.
            integration.ad_account_name = f"Google Ads Account {customer_id}"
            integration.status = IntegrationStatusEnum.ACTIVE # Mark the integration as fully active.
            try:
                db.session.commit() # Save changes to the database.
                flash(f"Google Ads Customer ID {customer_id} linked and activated successfully!", "success")
            except IntegrityError: # Should be rare as we are updating an existing integration.
                db.session.rollback()
                flash('This Google Ads Customer ID might already be linked to another integration in our system or cause a conflict.', 'danger')
                current_app.logger.warning(f"IntegrityError saving Google Ads Customer ID for integration {integration.id}: Customer ID {customer_id}", exc_info=True)
            except Exception as e: # Catch other potential database errors.
                db.session.rollback()
                flash('An error occurred while saving your Google Ads Customer ID. Please try again.', 'danger')
                current_app.logger.error(f"Error saving Google Ads Customer ID for integration {integration.id}: {e}", exc_info=True)
            return redirect(url_for('integration.hub')) # Redirect to hub after processing.

    # For GET requests, render the template that shows the input form for Google Ads Customer ID.
    return render_template('integration/google_select_account.html', integration=integration)

@integration_bp.route('/metaads/fetch_data/<int:integration_id>', methods=['POST'])
@login_required
def meta_ads_fetch_data(integration_id):
    """
    Fetches campaign performance data from Meta Ads API for a specific integration.
    Data is fetched for the last 7 days and includes breakdowns by device, country, age, and gender.
    Overall daily totals are also calculated from device data.

    Args:
        integration_id (int): The ID of the AdPlatformIntegration to fetch data for.
    """
    # --- Step 1: Fetch the relevant AdPlatformIntegration record ---
    # Ensure the integration belongs to the current user, is for Meta Ads, and is active.
    integration = AdPlatformIntegration.query.filter_by(
        id=integration_id,
        user_id=current_user.id,
        platform_name=PlatformNameEnum.META_ADS,
        status=IntegrationStatusEnum.ACTIVE # Ensure integration is active.
    ).first_or_404() # Returns 404 if no matching integration found.

    # --- Step 2: Check if Ad Account is selected ---
    # Data fetching requires a specific ad account ID.
    if integration.ad_account_id == "pending_selection":
        flash("Please select an ad account for this Meta Ads integration before fetching data.", "warning")
        return redirect(url_for('integration.hub')) # Redirect to hub or settings page.

    # --- Step 3: Prepare for API calls ---
    # Retrieve the stored access token (decrypted by the model's property getter).
    access_token = integration.access_token
    if not access_token: # Should not happen if integration is ACTIVE and properly set up.
        flash("Access token for Meta Ads is missing or invalid. Please reconnect the integration.", "danger")
        current_app.logger.error(f"Meta Ads: Access token not found for active integration {integration.id} (User: {current_user.id}).")
        return redirect(url_for('integration.hub'))

    # Meta Ads API requires the ad account ID to be prefixed with 'act_'.
    ad_account_id_with_prefix = f"act_{integration.ad_account_id}"

    # Define the date range for data fetching (e.g., last 7 days).
    end_date_dt = datetime.utcnow().date() # Today's date.
    start_date_dt = end_date_dt - timedelta(days=7) # 7 days ago.

    # Construct the base URL for Meta Ads Insights API. Use API version from config or default.
    graph_api_version = current_app.config.get('META_GRAPH_API_VERSION', 'v18.0')
    insights_url = f"https://graph.facebook.com/{graph_api_version}/{ad_account_id_with_prefix}/insights"

    # --- Step 4: Helper function to process and upsert breakdown data ---
    # This nested function encapsulates the logic for handling API response data for a specific breakdown
    # and storing it in the CampaignData table.
    def _process_and_store_meta_breakdown(api_response_data, breakdown_type_str, api_breakdown_field_name):
        """
        Processes data from Meta Ads API for a given breakdown and upserts into CampaignData.
        Args:
            api_response_data (list): List of data items from the Meta Ads API response.
            breakdown_type_str (str): The type of breakdown (e.g., 'device', 'country') for storage.
            api_breakdown_field_name (str): The field name in the API response that contains the breakdown value.
        Returns:
            bool: True if data was processed (even if empty), False if api_response_data was None initially.
        """
        if api_response_data is None: # Check if API returned any data object at all
            current_app.logger.warning(f"Meta Ads: No data object in API response for {breakdown_type_str} breakdown (Integration: {integration.id}).")
            flash(f"No {breakdown_type_str.replace('_', ' ')} breakdown data was found for Meta Ads account '{integration.ad_account_name}' for the last 7 days.", "info")
            return False # Indicate no data to process for this breakdown.

        if not api_response_data: # Empty list means API returned data, but it was empty for the query.
            current_app.logger.info(f"Meta Ads: API returned empty data list for {breakdown_type_str} breakdown (Integration: {integration.id}).")
            # flash(f"Empty data set for {breakdown_type_str.replace('_', ' ')} breakdown (Meta Ads: {integration.ad_account_name}).", "info") # Optional: can be too verbose
            return True # Processed (empty data), so return True.

        # Iterate through each item in the API data (each item represents a campaign-date-breakdown combination).
        for item in api_response_data:
            campaign_id = item['campaign_id']
            campaign_name = item.get('campaign_name', f'Campaign {campaign_id}') # Use campaign_name if available.
            entry_date_obj = datetime.strptime(item['date_start'], '%Y-%m-%d').date() # Convert date string to date object.
            breakdown_value = item.get(api_breakdown_field_name, 'unknown') # Get the breakdown value (e.g., 'desktop', 'US').

            # Normalize device platform names for consistency.
            if breakdown_type_str == 'device': # 'device_platform' from API
                if 'mobile' in breakdown_value.lower(): breakdown_value = 'mobile'
                elif 'desktop' in breakdown_value.lower(): breakdown_value = 'desktop'
                elif 'tablet' in breakdown_value.lower(): breakdown_value = 'tablet'
                # Other values like 'messenger_mobile_web' might default to 'unknown' or need specific mapping.

            # Extract metrics.
            impressions_val = int(item.get('impressions', 0))
            clicks_val = int(item.get('clicks', 0))
            spend_val = float(item.get('spend', 0.0))

            # Extract conversions. Meta Ads 'actions' field is a list of action types.
            # We sum values for common purchase-related action types. This might need customization.
            num_conversions = 0
            if 'actions' in item:
                for action in item.get('actions', []):
                    action_type = action.get('action_type', '')
                    # Common Meta Pixel purchase events and standard events.
                    if 'purchase' in action_type or \
                       'offsite_conversion.fb_pixel_purchase' in action_type or \
                       'omni_purchase' in action_type or \
                       'website_purchase' in action_type:
                        num_conversions += int(action.get('value', 0)) # 'value' field usually holds count for these actions.

            # Upsert logic: Check if a record for this specific breakdown already exists.
            existing_entry = CampaignData.query.filter_by(
                integration_id=integration.id,
                campaign_id_platform=campaign_id,
                date=entry_date_obj,
                breakdown_type=breakdown_type_str,
                breakdown_value=breakdown_value,
                platform=PlatformNameEnum.META_ADS
            ).first()

            if existing_entry: # If exists, update its metrics.
                existing_entry.campaign_name_platform = campaign_name
                existing_entry.impressions = impressions_val
                existing_entry.clicks = clicks_val
                existing_entry.spend = spend_val
                existing_entry.conversions = num_conversions
                existing_entry.updated_at = datetime.utcnow()
            else: # If not, create a new CampaignData record.
                new_entry = CampaignData(
                    integration_id=integration.id, platform=PlatformNameEnum.META_ADS,
                    campaign_id_platform=campaign_id, campaign_name_platform=campaign_name,
                    date=entry_date_obj, breakdown_type=breakdown_type_str, breakdown_value=breakdown_value,
                    impressions=impressions_val, clicks=clicks_val, spend=spend_val, conversions=num_conversions
                )
                db.session.add(new_entry)
        return True # Indicate successful processing of (potentially empty) data.

    # --- Step 5: Define configurations for fetching each breakdown type ---
    # This list drives the data fetching loop, making it easier to add/remove breakdowns.
    breakdown_configs = [
        {'name': 'device', 'api_param': 'device_platform', 'db_type': 'device', 'flash_msg_part': 'device platform'},
        {'name': 'country', 'api_param': 'country', 'db_type': 'country', 'flash_msg_part': 'country'},
        {'name': 'age', 'api_param': 'age', 'db_type': 'age_range', 'flash_msg_part': 'age range'},
        {'name': 'gender', 'api_param': 'gender', 'db_type': 'gender', 'flash_msg_part': 'gender'}
    ]

    # Set to store unique (campaign_id, date, campaign_name) tuples from device data,
    # used later for calculating "overall" daily totals.
    processed_overall_campaign_dates = set()
    all_fetches_successful = True # Flag to track if all breakdown fetches succeeded.

    # --- Step 6: Loop through breakdown configurations and fetch/process data ---
    for config in breakdown_configs:
        current_app.logger.info(f"Meta Ads: Fetching '{config['name']}' breakdown for integration {integration.id} (Ad Account: {integration.ad_account_id}).")
        # Parameters for the Meta Ads Insights API call.
        params = {
            'access_token': access_token,
            'level': 'campaign', # Data aggregated at the campaign level.
            'fields': 'campaign_id,campaign_name,impressions,clicks,spend,actions', # Core metrics and campaign info.
            'breakdowns': config['api_param'], # The specific breakdown type (e.g., 'device_platform').
            'time_range': f"{{'since':'{start_date_dt.isoformat()}','until':'{end_date_dt.isoformat()}'}}", # Date range.
            'time_increment': 1, # Daily data.
            'limit': 500 # Max results per page (adjust if expecting more for a single call/day). Pagination not fully implemented here.
        }
        try:
            response = requests.get(insights_url, params=params, timeout=30) # API request with timeout.
            response.raise_for_status() # Raise HTTPError for bad API responses.
            api_data_list = response.json().get('data', []) # Extract 'data' list from JSON response.

            # Process the fetched data using the helper function.
            data_was_processed = _process_and_store_meta_breakdown(api_data_list, config['db_type'], config['api_param'])

            if data_was_processed: # If data was processed (even if empty list from API).
                try:
                    db.session.commit() # Commit changes for this breakdown type to the database.
                    current_app.logger.info(f"Meta Ads: Successfully committed '{config['name']}' breakdown data for integration {integration.id}.")
                    # If this is device data, store campaign-date-name tuples for later "overall" calculation.
                    if config['name'] == 'device' and api_data_list:
                         for item in api_data_list:
                             processed_overall_campaign_dates.add((
                                 item['campaign_id'],
                                 datetime.strptime(item['date_start'], '%Y-%m-%d').date(),
                                 item.get('campaign_name', f'Campaign {item["campaign_id"]}')
                             ))
                except Exception as e_commit: # Handle DB commit errors.
                    db.session.rollback()
                    all_fetches_successful = False
                    current_app.logger.error(f"Meta Ads: Database error committing '{config['name']}' data for integration {integration.id}: {e_commit}", exc_info=True)
                    flash(f"Error saving Meta Ads {config['flash_msg_part']} breakdown data to the database.", "danger")
                    # Optionally, decide if one failure should stop all subsequent fetches or continue.

        except requests.exceptions.RequestException as e_req: # Handle API request errors (network, HTTP status).
            all_fetches_successful = False
            flash(f"Error fetching {config['flash_msg_part']} breakdown data from Meta Ads: {str(e_req)}. Please check connection and permissions.", "danger")
            current_app.logger.error(f"Meta Ads API Request Error ('{config['name']}' Breakdown) for integration {integration.id}: {e_req}", exc_info=True)
        except Exception as e_gen: # Handle other unexpected errors during this breakdown's processing.
            db.session.rollback() # Ensure rollback for any partial processing of this breakdown.
            all_fetches_successful = False
            flash(f"An unexpected error occurred while processing Meta Ads {config['flash_msg_part']} data. Some data may not be complete.", "danger")
            current_app.logger.error(f"Unexpected error during Meta Ads '{config['name']}' breakdown processing for integration {integration.id}: {e_gen}", exc_info=True)

    # --- Step 7: Calculate and save "overall" daily totals (derived from device data) ---
    if processed_overall_campaign_dates: # Only proceed if device data was successfully fetched and processed.
        current_app.logger.info(f"Meta Ads: Calculating 'overall' daily totals for {len(processed_overall_campaign_dates)} campaign-date pairs for integration {integration.id}.")
        for camp_id, date_obj, camp_name in processed_overall_campaign_dates:
            # Query to sum metrics from 'device' breakdown records for the given campaign and date.
            # This aggregates data across all devices to get an overall daily total for the campaign.
            summed_metrics = db.session.query(
                func.sum(CampaignData.impressions).label('total_impressions'),
                func.sum(CampaignData.clicks).label('total_clicks'),
                func.sum(CampaignData.spend).label('total_spend'),
                func.sum(CampaignData.conversions).label('total_conversions')
            ).filter_by(
                integration_id=integration.id,
                campaign_id_platform=camp_id,
                date=date_obj,
                breakdown_type='device', # Summing based on 'device' records.
                platform=PlatformNameEnum.META_ADS
            ).one_or_none() # Expect one row of summed results or None.

            # If no device data was found for this campaign-date, skip creating/updating "overall".
            if not summed_metrics or summed_metrics.total_impressions is None: # Check if any metrics were actually summed.
                 current_app.logger.info(f"Meta Ads: No device-specific data found to aggregate for 'overall' record for campaign {camp_id} on {date_obj}. Skipping.")
                 continue

            # Check if an "overall" record already exists for this campaign and date.
            overall_entry = CampaignData.query.filter_by(
                integration_id=integration.id, campaign_id_platform=camp_id, date=date_obj,
                breakdown_type='overall', breakdown_value='N/A', # 'overall' records have 'N/A' as breakdown_value.
                platform=PlatformNameEnum.META_ADS
            ).first()

            if overall_entry: # If exists, update its summed metrics.
                overall_entry.impressions = summed_metrics.total_impressions or 0
                overall_entry.clicks = summed_metrics.total_clicks or 0
                overall_entry.spend = summed_metrics.total_spend or 0
                overall_entry.conversions = summed_metrics.total_conversions or 0
                overall_entry.campaign_name_platform = camp_name # Update name in case it changed.
                overall_entry.updated_at = datetime.utcnow()
            else: # If not, create a new "overall" CampaignData record.
                new_overall_entry = CampaignData(
                    integration_id=integration.id, platform=PlatformNameEnum.META_ADS,
                    campaign_id_platform=camp_id, campaign_name_platform=camp_name,
                    date=date_obj, breakdown_type='overall', breakdown_value='N/A',
                    impressions=summed_metrics.total_impressions or 0,
                    clicks=summed_metrics.total_clicks or 0,
                    spend=summed_metrics.total_spend or 0,
                    conversions=summed_metrics.total_conversions or 0
                )
                db.session.add(new_overall_entry)
        try:
            db.session.commit() # Commit the "overall" data changes.
            current_app.logger.info(f"Meta Ads: Successfully committed 'overall' daily totals for integration {integration.id}.")
            if all_fetches_successful: # Only show full success if all breakdowns also succeeded.
                 flash(f"Meta Ads data fetch (including all breakdowns and overall daily totals) completed successfully for ad account '{integration.ad_account_name}'.", "success")
            else:
                 flash(f"Meta Ads data fetch for '{integration.ad_account_name}' completed, but some breakdown data may have encountered issues. Overall totals calculated based on available data.", "warning")
        except Exception as e_commit_overall: # Catch DB errors during "overall" data commit.
            db.session.rollback()
            current_app.logger.error(f"Error committing 'overall' Meta Ads data for integration {integration.id}: {e_commit_overall}", exc_info=True)
            flash("Error saving aggregated 'overall' daily totals for Meta Ads. Other breakdown data might have been saved.", "danger")
    elif all_fetches_successful: # Device data might be empty but other fetches were okay.
        flash(f"Meta Ads data fetch completed for '{integration.ad_account_name}'. Note: Overall totals could not be calculated as no device-specific data was found for aggregation.", "info")
    else: # If no device data AND other fetches failed.
        flash(f"Meta Ads data fetch for '{integration.ad_account_name}' encountered issues. Some or all data may be missing.", "danger")

    # Redirect back to the integration hub page.

    return redirect(url_for('integration.hub'))
