from flask import Blueprint, request, redirect, url_for, flash, current_app, render_template
from flask_login import login_required, current_user
from datetime import datetime, date, timedelta
import stripe # Import the Stripe Python library
from stripe import error as stripe_error # Import Stripe error types for specific handling
from sqlalchemy.exc import IntegrityError # For handling database integrity errors (e.g., unique constraint violations)

from extensions import db # For database operations
from models.user import User # User model for associating subscriptions
from models.subscription_plan import SubscriptionPlan # SubscriptionPlan model to get plan details
from models.user_subscription import UserSubscription, SubscriptionStatusEnum # UserSubscription model and status enum

# Blueprint for billing-related routes.
# This blueprint groups all billing and subscription management views
# under the '/billing' URL prefix, organizing these functionalities.
billing_bp = Blueprint('billing', __name__, url_prefix='/billing')

# Note: Stripe API key (stripe.api_key) is typically set in the main application setup
# (e.g., in app.py or config.py from environment variables like app.config['STRIPE_SECRET_KEY'])
# and then assigned to `stripe.api_key` during application initialization.
# The STRIPE_WEBHOOK_SECRET is also configured similarly for webhook signature verification.

# Route to create a Stripe Checkout session for a subscription plan.
# This route is triggered when a user clicks to subscribe to a plan.
@billing_bp.route('/create-checkout-session/<int:plan_id>', methods=['POST'])
@login_required # Ensures only logged-in users can create a checkout session.
def create_checkout_session(plan_id):
    """
    Creates a Stripe Checkout session for a selected subscription plan.
    Redirects the user to Stripe's checkout page.
    Handles various Stripe API errors.
    Args:
        plan_id (int): The ID of the SubscriptionPlan to subscribe to.
    """
    # Fetch the selected plan from the database; returns 404 if not found.
    plan = SubscriptionPlan.query.get_or_404(plan_id) # Use get_or_404 for concise error handling if plan doesn't exist.

    # Check if the plan has a configured Stripe Price ID. This ID links our internal plan to a Stripe Price object.
    # Without it, we cannot initiate a Stripe Checkout session for this plan.
    if not plan.stripe_price_id:
        flash('This plan is not available for online purchase at the moment. Please contact support.', 'error')
        current_app.logger.error(f"User {current_user.id} (email: {current_user.email}) attempted to subscribe to plan ID {plan_id} ('{plan.name}') which has no stripe_price_id.")
        return redirect(url_for('auth.subscription_plans_overview')) # Redirect back to the subscription plans page.

    try:
        # Create a Stripe Checkout Session.
        # This session represents the user's payment interaction with Stripe and will redirect them to a Stripe-hosted page.
        checkout_session_params = {
            # client_reference_id is a crucial link back to our application's User model.
            # This ID is passed in webhook events, allowing us to identify the user who completed the checkout.
            'client_reference_id': str(current_user.id), # Ensure it's a string if user ID is not already.
            # line_items specifies the products or services being purchased.
            # For a subscription, this includes the Stripe Price ID of the plan and the quantity (usually 1).
            'line_items': [{'price': plan.stripe_price_id, 'quantity': 1}],
            # mode='subscription' indicates that this Checkout session is for creating a recurring subscription.
            # Other modes include 'payment' (for one-time payments) and 'setup' (for saving payment details).
            'mode': 'subscription',
            # success_url is the URL Stripe redirects the user to after a successful payment and subscription creation.
            # {CHECKOUT_SESSION_ID} is a Stripe template variable that gets replaced with the actual session ID.
            # This can be useful for displaying a custom success message or for reconciliation, though webhooks are primary for fulfillment.
            'success_url': url_for('billing.checkout_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            # cancel_url is the URL Stripe redirects the user to if they cancel the checkout process (e.g., by clicking "back" or closing the page).
            'cancel_url': url_for('billing.checkout_cancel', _external=True),
            # customer_email can be prefilled on the Stripe Checkout page. current_user is available due to @login_required.
            'customer_email': current_user.email,
            # allow_promotion_codes enables the promotion code field on the Checkout page if you use Stripe coupons.
            'allow_promotion_codes': True,
            # If the Stripe Price object (plan.stripe_price_id) has a trial period configured in the Stripe Dashboard,
            # it will automatically apply.
            # Alternatively, a trial period can be specified dynamically here using:
            # 'subscription_data': {
            #     'trial_period_days': plan.trial_period_days, # Assuming 'trial_period_days' is a field on your Plan model
            # }
            # if plan.trial_period_days and plan.trial_period_days > 0 else {} # Add only if trial days are set
        }

        # If a user already has a Stripe Customer ID, pass it to Stripe.
        # This ensures that the new subscription is associated with their existing Stripe customer object.
        # It helps keep all their subscriptions and payment methods under one customer in Stripe.
        if current_user.stripe_customer_id:
            checkout_session_params['customer'] = current_user.stripe_customer_id
        else:
            # If no Stripe Customer ID exists for the user, Stripe will create a new one.
            # We can also explicitly ask Stripe to create a new customer and prefill details:
            # checkout_session_params['customer_creation'] = 'always' # Or 'if_required'
            pass # Default behavior is to create a customer if one is not provided and mode is 'subscription'.

        checkout_session = stripe.checkout.Session.create(**checkout_session_params)

        # Redirect the user to the Stripe-hosted checkout page's URL.
        # HTTP 303 See Other is the standard response code for redirecting after a POST request.
        return redirect(checkout_session.url, code=303)

    # Specific Stripe error handling:
    except stripe_error.CardError as e:
        # Handles card-specific errors like card declined, insufficient funds, expired card.
        # e.user_message provides a user-friendly message.
        flash(f"Your card was declined: {e.user_message or 'Please try a different card or contact your bank.'}", "danger")
        current_app.logger.warning(f"Stripe CardError for user {current_user.id} (Plan ID: {plan_id}): {e.code} - {e.user_message}")
    except stripe_error.RateLimitError as e:
        # Handles errors due to too many requests made to the Stripe API in a short period.
        flash("We're currently experiencing high traffic with our payment provider. Please try again in a few moments.", "warning")
        current_app.logger.error(f"Stripe RateLimitError for user {current_user.id} (Plan ID: {plan_id}): {e}")
    except stripe_error.InvalidRequestError as e:
        # Handles errors due to invalid parameters sent to Stripe (e.g., malformed request, incorrect data type).
        # This might indicate a bug in our integration.
        flash("There was an issue with the payment request. Please check your details or contact support if the problem persists.", "danger")
        current_app.logger.error(f"Stripe InvalidRequestError for user {current_user.id} (Plan ID: {plan_id}): {e}")
    except stripe_error.AuthenticationError as e:
        # Handles errors due to authentication issues with the Stripe API (e.g., incorrect or missing API key).
        # This is a server-side configuration issue.
        flash("There's an issue with our payment provider configuration. Please contact support.", "danger")
        current_app.logger.critical(f"Stripe AuthenticationError: {e}. Check Stripe API key configuration.")
    except stripe_error.APIConnectionError as e:
        # Handles network communication errors with Stripe (e.g., DNS issues, network outage).
        flash("We couldn't connect to our payment provider. Please check your internet connection and try again.", "warning")
        current_app.logger.error(f"Stripe APIConnectionError: {e}")
    except stripe_error.StripeError as e:
        # Handles other generic Stripe API errors not covered by the specific types above.
        # e.user_message can provide a generic message if available.
        flash(f"A payment processing error occurred: {e.user_message or 'Please try again or contact support.'}", "danger")
        current_app.logger.error(f"Generic StripeError for user {current_user.id} (Checkout for Plan ID: {plan_id}): {e}")
    except Exception as e:
        # Catch any other unexpected errors during the process.
        flash("An unexpected error occurred while trying to set up your payment. Please contact support.", "danger")
        current_app.logger.error(f"Unexpected error during create_checkout_session for user {current_user.id} (Plan ID: {plan_id}): {e}", exc_info=True)

    # If any error occurs during the try block, the user is redirected back to the subscription plans page.
    return redirect(url_for('auth.subscription_plans_overview'))


# Route for handling successful redirection from Stripe Checkout.
@billing_bp.route('/checkout-success')
@login_required # User should be logged in to see this page.
def checkout_success():
    """
    Handles successful redirection from Stripe Checkout.
    Flashes a success message and redirects to the user's profile.
    The actual subscription provisioning is handled by the webhook.
    """
    # The session_id is passed as a query parameter by Stripe.
    session_id = request.args.get('session_id')
    # While we can retrieve the session here using stripe.checkout.Session.retrieve(session_id),
    # it's generally best practice to rely on webhooks for actual subscription provisioning and fulfillment
    # because the webhook is a more reliable mechanism (e.g., handles cases where user closes browser before redirect).
    # This page is primarily for user experience, confirming the UI part of the flow.
    current_app.logger.info(f"User {current_user.id} successfully redirected from Stripe Checkout. Session ID: {session_id}")
    flash('Your subscription checkout was successful! Your plan should be active shortly.', 'success')
    # Redirect to a relevant page, e.g., user's profile, dashboard, or subscription settings.
    return redirect(url_for('auth.profile'))

# Route for handling cancellation redirection from Stripe Checkout.
@billing_bp.route('/checkout-cancel')
@login_required # User should be logged in, as they initiated the checkout.
def checkout_cancel():
    """
    Handles cancellation redirection from Stripe Checkout.
    This page is shown when the user explicitly cancels the Stripe Checkout process.
    Flashes an informational message and redirects to the subscription plans page.
    """
    current_app.logger.info(f"User {current_user.id} cancelled the Stripe Checkout process.")
    flash('Your subscription checkout was cancelled. You can choose a plan anytime.', 'info')
    # Redirect user back to the subscription plans page or another relevant location.
    return redirect(url_for('auth.subscription_plans_overview'))

# Route to create a Stripe Billing Portal session.
# This allows users to manage their existing subscriptions (e.g., update card, cancel).
@billing_bp.route('/create-customer-portal-session', methods=['POST'])
@login_required
def create_customer_portal_session():
    """
    Creates a Stripe Billing Portal session for the current user.
    Redirects the user to the Stripe-hosted portal to manage their subscription.
    """
    # Ensure the user has a Stripe Customer ID. This ID is created by Stripe when a user first subscribes
    # or sets up a payment method. It's required to access the Billing Portal.
    if not current_user.stripe_customer_id:
        flash("No billing information found for your account. This usually means you don't have an active subscription.", "warning")
        current_app.logger.warning(f"User {current_user.id} (email: {current_user.email}) attempted to access customer portal without a stripe_customer_id.")
        return redirect(url_for('billing.subscription_settings_page')) # Redirect to subscription settings or profile.

    try:
        # Define the URL to which Stripe will redirect the user after they finish managing their subscription in the portal.
        # This should typically be a page where they can see their updated subscription status.
        return_url = url_for('billing.subscription_settings_page', _external=True)

        # Create a Stripe Billing Portal session.
        # This session provides a short-lived URL that grants the user access to the portal.
        portal_session = stripe.billing_portal.Session.create(
            customer=current_user.stripe_customer_id, # The Stripe Customer ID of the logged-in user.
            return_url=return_url, # The URL the user will be redirected to after exiting the portal.
            # configuration=current_app.config.get('STRIPE_PORTAL_CONFIGURATION_ID') # Optional: If you have a custom portal configuration ID from Stripe.
        )
        # Redirect the user to the Stripe-hosted Billing Portal URL.
        return redirect(portal_session.url, code=303)
    except stripe_error.InvalidRequestError as e:
        # Handles errors where the request to create a portal session is invalid (e.g., customer ID is wrong).
        flash("Could not create a billing portal session due to an invalid request. This might happen if your billing information is incomplete. Please contact support.", "danger")
        current_app.logger.error(f"Stripe Portal InvalidRequestError for user {current_user.id} (Stripe Customer ID: {current_user.stripe_customer_id}): {e}")
    except stripe_error.StripeError as e: # Catch other Stripe-specific errors.
        flash(f"Could not create a billing portal session: {e.user_message or 'An issue occurred with our payment provider. Please try again or contact support.'}", "danger")
        current_app.logger.error(f"Stripe Portal Error for user {current_user.id} (Stripe Customer ID: {current_user.stripe_customer_id}): {e}")
    except Exception as e: # Catch any other unexpected errors.
        flash("An unexpected error occurred while trying to access the billing portal. Please contact support.", "danger")
        current_app.logger.error(f"Unexpected Portal Error for user {current_user.id} (Stripe Customer ID: {current_user.stripe_customer_id}): {e}", exc_info=True)

    # If any error occurs during the try block, redirect the user back to their subscription settings page.
    return redirect(url_for('billing.subscription_settings_page'))


# Route to display the user's subscription settings page.
@billing_bp.route('/subscription')
@login_required
def subscription_settings_page():
    """
    Displays the user's current active or trialing subscription details.
    Provides options to manage their subscription (via Stripe Billing Portal).
    """
    # Fetch the most recent subscription for the user that is considered "active" in a broad sense.
    # This includes TRIALING, ACTIVE, or PAST_DUE (which needs payment update but is still technically the active plan).
    # We join with SubscriptionPlan to potentially display plan details like name or features.
    # Order by created_at descending to get the latest one if, for some reason, multiple such subscriptions exist (which should ideally be avoided by proper webhook handling).
    active_subscription = UserSubscription.query.filter_by(user_id=current_user.id)\
        .filter(UserSubscription.status.in_([
            SubscriptionStatusEnum.ACTIVE,
            SubscriptionStatusEnum.TRIALING,
            SubscriptionStatusEnum.PAST_DUE
        ]))\
        .join(SubscriptionPlan)\
        .order_by(UserSubscription.created_at.desc()).first()

    # Render the subscription settings template, passing the fetched subscription details.
    # The template can then display information about the current plan, status, end date, etc.,
    # and provide a button to access the Stripe Billing Portal (handled by `create_customer_portal_session`).
    return render_template('billing/subscription_settings.html', active_sub=active_subscription)

# Route to display information about the user's trial status.
@billing_bp.route('/trial-status')
@login_required
def trial_info_page():
    """
    Displays detailed information about the user's current trial subscription, if any.
    """
    # Fetch the most recent trialing subscription for the user.
    trial_sub = UserSubscription.query.filter_by(
        user_id=current_user.id,
        status=SubscriptionStatusEnum.TRIALING
    ).join(SubscriptionPlan).order_by(UserSubscription.end_date.desc()).first()

    trial_info_data = None # Initialize to None
    if trial_sub:
        today = date.today() # Get current date for comparisons
        # Ensure end_date and start_date are date objects for correct calculation.
        trial_end_date_obj = trial_sub.end_date.date() if isinstance(trial_sub.end_date, datetime) else trial_sub.end_date
        trial_start_date_obj = trial_sub.start_date.date() if isinstance(trial_sub.start_date, datetime) else trial_sub.start_date

        # Calculate remaining days and trial duration.
        remaining_days = (trial_end_date_obj - today).days if trial_end_date_obj else 0
        duration = (trial_end_date_obj - trial_start_date_obj).days if trial_end_date_obj and trial_start_date_obj else "N/A"

        # Prepare data to pass to the template.
        trial_info_data = {
            "plan_name": trial_sub.plan.name,
            "start_date": trial_sub.start_date,
            "end_date": trial_sub.end_date,
            "is_active": trial_end_date_obj >= today if trial_end_date_obj else False, # Check if trial is currently active
            "remaining_days": max(0, remaining_days), # Avoid displaying negative days
            "duration_days": duration,
            "features_summary": ", ".join(trial_sub.plan.features) if trial_sub.plan.features and isinstance(trial_sub.plan.features, list) else "All selected plan features"
        }
    # Render the trial information template.
    return render_template('billing/trial_info.html', trial_info=trial_info_data)

# Stripe Webhook endpoint to receive and process events from Stripe.
# This endpoint must be publicly accessible (no @login_required).
@billing_bp.route('/stripe-webhook', methods=['POST'])
def stripe_webhook():
    """
    Handles incoming webhooks from Stripe for various subscription events.
    This endpoint is crucial for keeping local subscription data in sync with Stripe.
    # It must be publicly accessible (no @login_required) as Stripe makes POST requests to it.
    # Security is handled by verifying the Stripe signature.
    """
    # Retrieve the raw request body and the Stripe-Signature header.
    payload = request.data
    sig_header = request.headers.get('Stripe-Signature')
    event = None # Initialize event object.
    event_id_for_logging = 'unknown_event_id' # Default for logging if event ID cannot be extracted.

    # --- Webhook Signature Verification ---
    # This is a critical security step to ensure the webhook request genuinely came from Stripe.
    # current_app.config['STRIPE_WEBHOOK_SECRET'] must be set to your Stripe webhook's signing secret.
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, current_app.config['STRIPE_WEBHOOK_SECRET']
        )
        event_id_for_logging = event.get('id', event_id_for_logging) # Use actual event ID for logging.
        current_app.logger.info(f"Stripe Webhook Event ID {event_id_for_logging}: Received event type '{event.type}'.")
    except ValueError as e:
        # Invalid payload (e.g., not valid JSON).
        current_app.logger.error(f"Webhook ValueError (Event ID {event_id_for_logging}): Invalid payload - {e}")
        return 'Invalid payload', 400 # Return 400 Bad Request.
    except stripe_error.SignatureVerificationError as e:
        # Invalid signature - the request may be spoofed or the webhook secret might be misconfigured.
        current_app.logger.error(f"Webhook SignatureVerificationError (Event ID {event_id_for_logging}): {e}")
        return 'Invalid signature', 400 # Return 400 Bad Request.
    except Exception as e:
        # Other errors during event construction (e.g., unexpected issues with Stripe library).
        current_app.logger.error(f"Webhook event construction error (Event ID {event_id_for_logging}): {e}", exc_info=True)
        return 'Error constructing event', 500 # Return 500 Internal Server Error.

    # --- Event Handling Logic ---
    # Process specific event types. The order can matter if events are related.

    # Event: 'checkout.session.completed'
    # Signifies: A user has successfully completed a Stripe Checkout session.
    # This is the primary event for provisioning a new subscription.
    if event.type == 'checkout.session.completed':
        session = event.data.object # The Checkout Session object from the event payload.

        # Extract key identifiers from the session.
        user_id = session.get('client_reference_id') # Our internal user ID, passed during Checkout session creation.
        stripe_subscription_id = session.get('subscription') # The ID of the Stripe Subscription object created.
        stripe_customer_id = session.get('customer') # The ID of the Stripe Customer object created or used.

        # Validate that essential IDs are present in the event payload.
        if not user_id or not stripe_subscription_id or not stripe_customer_id:
            current_app.logger.error(f'Event ID {event_id_for_logging} (checkout.session.completed): Webhook Error: Missing user_id, stripe_subscription_id, or stripe_customer_id in session. UserID: {user_id}, SubID: {stripe_subscription_id}, CustID: {stripe_customer_id}')
            return 'Missing essential IDs in session', 400 # Bad request, missing critical data.

        user = User.query.get(user_id) # Retrieve the user from our database.
        if not user:
            current_app.logger.error(f'Event ID {event_id_for_logging} (checkout.session.completed): Webhook Error: User with ID {user_id} not found in local database.')
            return f'User {user_id} not found', 404 # Not Found, if user doesn't exist.

        # Update user's Stripe Customer ID if it's not already set or if it's different.
        # This links our local user record to the Stripe customer record.
        if not user.stripe_customer_id or user.stripe_customer_id != stripe_customer_id:
            user.stripe_customer_id = stripe_customer_id
            current_app.logger.info(f"Event ID {event_id_for_logging} (checkout.session.completed): Updated/set stripe_customer_id for user {user_id} to {stripe_customer_id}.")

        # Idempotency Check: Prevent processing the same subscription event multiple times.
        # Stripe might resend webhooks if it doesn't receive a timely 200 OK response.
        existing_sub_check = UserSubscription.query.filter_by(stripe_subscription_id=stripe_subscription_id).first()
        if existing_sub_check:
            current_app.logger.info(f"Event ID {event_id_for_logging} (checkout.session.completed): Subscription {stripe_subscription_id} for user {user_id} has already been processed. Skipping creation to ensure idempotency.")
            if db.session.is_modified(user): # If only stripe_customer_id was updated on the user object.
                try:
                    db.session.commit()
                    current_app.logger.info(f"Event ID {event_id_for_logging}: Committed stripe_customer_id update for user {user_id} on already processed sub {stripe_subscription_id}.")
                except Exception as e_commit:
                    db.session.rollback()
                    current_app.logger.error(f"Event ID {event_id_for_logging}: Error committing stripe_customer_id update for user {user_id} on existing sub {stripe_subscription_id}: {e_commit}")
            return 'Acknowledged: Subscription already processed', 200 # Acknowledge event, but do nothing more.

        # Retrieve session with line items to get the price ID, which links to our SubscriptionPlan.
        try:
            session_with_line_items = stripe.checkout.Session.retrieve(session.id, expand=['line_items'])
            if not session_with_line_items.line_items or not session_with_line_items.line_items.data:
                current_app.logger.error(f"Event ID {event_id_for_logging} (checkout.session.completed): No line items found in session {session.id}.")
                return 'No line items in session', 400
            stripe_price_id = session_with_line_items.line_items.data[0].price.id
        except Exception as e:
            current_app.logger.error(f"Event ID {event_id_for_logging} (checkout.session.completed): Webhook Error: Could not retrieve line items or price ID from session {session.id}: {e}")
            return 'Could not determine stripe_price_id from session', 400

        subscription_plan = SubscriptionPlan.query.filter_by(stripe_price_id=stripe_price_id).first()
        if not subscription_plan:
            current_app.logger.error(f'Event ID {event_id_for_logging} (checkout.session.completed): Webhook Error: SubscriptionPlan with stripe_price_id {stripe_price_id} not found in local database.')
            return f'SubscriptionPlan with stripe_price_id {stripe_price_id} not found', 404

        # Handle potential existing subscriptions: If a user buys a new plan, Stripe might create a new subscription.
        # We should mark any previous active/trialing subscriptions for this user as cancelled.
        # This logic depends on how Stripe handles upgrades/downgrades in your setup (e.g., proration, new sub ID).
        # It's safer to query Stripe for the most up-to-date subscription details.
        existing_user_subscriptions = UserSubscription.query.filter(
                UserSubscription.user_id == user.id,
                UserSubscription.stripe_subscription_id != stripe_subscription_id, # Exclude the one being created
                UserSubscription.status.in_([SubscriptionStatusEnum.ACTIVE, SubscriptionStatusEnum.TRIALING, SubscriptionStatusEnum.PAST_DUE])
            ).all()
        for old_sub in existing_user_subscriptions:
            current_app.logger.info(f"Event ID {event_id_for_logging} (checkout.session.completed): User {user.id} has existing subscription {old_sub.stripe_subscription_id} (status: {old_sub.status}). Marking as CANCELLED due to new subscription {stripe_subscription_id}.")
            old_sub.status = SubscriptionStatusEnum.CANCELLED
            # old_sub.end_date = datetime.utcnow() # Or fetch actual end date from Stripe for the old sub if necessary.

        # Retrieve full subscription details from Stripe for accurate dates and trial status.
        # This is the source of truth for subscription details.
        try:
            stripe_sub_data = stripe.Subscription.retrieve(stripe_subscription_id)
        except stripe_error.StripeError as e:
            current_app.logger.error(f"Event ID {event_id_for_logging} (checkout.session.completed): Failed to retrieve Stripe Subscription {stripe_subscription_id}: {e}")
            # Depending on retry strategy, might return 500 to have Stripe retry or handle gracefully.
            return "Failed to retrieve subscription details from Stripe", 500

        current_period_end_dt = datetime.utcfromtimestamp(stripe_sub_data.current_period_end)
        start_date_dt = datetime.utcfromtimestamp(stripe_sub_data.start_date)
        trial_end_dt = datetime.utcfromtimestamp(stripe_sub_data.trial_end) if stripe_sub_data.trial_end else None

        # Determine initial subscription status and end date based on trial period.
        new_subscription_status = SubscriptionStatusEnum.ACTIVE
        subscription_effective_end_date = current_period_end_dt # Default end date is current period end.

        if trial_end_dt and trial_end_dt > datetime.utcnow():
            new_subscription_status = SubscriptionStatusEnum.TRIALING
            subscription_effective_end_date = trial_end_dt # If in trial, the "active" period ends when trial ends.

        # Create the new local UserSubscription record.
        new_subscription = UserSubscription(
            user_id=user.id,
            plan_id=subscription_plan.id,
            stripe_subscription_id=stripe_subscription_id,
            status=new_subscription_status,
            start_date=start_date_dt,
            end_date=subscription_effective_end_date, # This is the current period end or trial end.
            stripe_customer_id=stripe_customer_id # Store customer ID with the subscription too.
        )
        db.session.add(new_subscription)

        # Commit all changes to the database (user's stripe_customer_id, old subs cancelled, new sub created).
        try:
            db.session.commit()
            current_app.logger.info(f"Event ID {event_id_for_logging} (checkout.session.completed): Successfully created new UserSubscription for user {user.id}, plan '{subscription_plan.name}', Stripe Sub ID {stripe_subscription_id}, status {new_subscription_status.value}.")
        except IntegrityError as e:
            db.session.rollback() # Rollback in case of integrity issues (e.g., unique constraint violation if idempotency check failed).
            current_app.logger.error(f"Event ID {event_id_for_logging} (checkout.session.completed): IntegrityError creating UserSubscription for {stripe_subscription_id}: {e}")
            return "Database integrity error during subscription creation", 500 # Internal server error.
        except Exception as e: # Catch other potential database errors.
            db.session.rollback()
            current_app.logger.error(f"Event ID {event_id_for_logging} (checkout.session.completed): Error committing new UserSubscription for {stripe_subscription_id}: {e}", exc_info=True)
            return "Error creating subscription in database", 500 # Internal server error.

    # Event: 'invoice.payment_succeeded'
    # Signifies: A payment for an invoice has been successfully made.
    # This often occurs for subscription renewals or payments after a trial.
    elif event.type == 'invoice.payment_succeeded':
        invoice = event.data.object # The Invoice object from the event.
        stripe_subscription_id = invoice.get('subscription') # Get associated Stripe Subscription ID.

        current_app.logger.info(f"Event ID {event_id_for_logging} (invoice.payment_succeeded): Processing for Stripe Subscription ID: {stripe_subscription_id}, Invoice ID: {invoice.id}.")

        if stripe_subscription_id:
            # Find the corresponding UserSubscription in our database.
            user_subscription = UserSubscription.query.filter_by(stripe_subscription_id=stripe_subscription_id).first()
            if user_subscription:
                # Retrieve the latest subscription data from Stripe to get the new current_period_end.
                try:
                    stripe_sub_data = stripe.Subscription.retrieve(stripe_subscription_id)
                except stripe_error.StripeError as e:
                    current_app.logger.error(f"Event ID {event_id_for_logging} (invoice.payment_succeeded): Failed to retrieve Stripe Subscription {stripe_subscription_id}: {e}")
                    return "Failed to retrieve subscription details from Stripe", 500

                new_period_end_dt = datetime.utcfromtimestamp(stripe_sub_data.current_period_end)

                # Logic to update subscription status and end date.
                # This handles transitions from trial to active, or renewals of active/past_due subscriptions.
                updated_fields = [] # For logging changes.
                if user_subscription.status == SubscriptionStatusEnum.TRIALING and invoice.billing_reason == 'subscription_cycle':
                    user_subscription.status = SubscriptionStatusEnum.ACTIVE
                    updated_fields.append(f"status from TRIALING to ACTIVE (trial ended, first payment successful)")

                if user_subscription.status == SubscriptionStatusEnum.PAST_DUE: # If it was past_due and payment succeeded
                    user_subscription.status = SubscriptionStatusEnum.ACTIVE
                    updated_fields.append(f"status from PAST_DUE to ACTIVE")

                if user_subscription.end_date != new_period_end_dt: # Update end date if it changed
                    user_subscription.end_date = new_period_end_dt
                    updated_fields.append(f"end_date to {new_period_end_dt.isoformat()}")

                if updated_fields:
                    try:
                        db.session.commit()
                        current_app.logger.info(f"Event ID {event_id_for_logging} (invoice.payment_succeeded): Subscription {stripe_subscription_id} for user {user_subscription.user_id} updated: {'; '.join(updated_fields)}.")
                    except Exception as e:
                        db.session.rollback()
                        current_app.logger.error(f"Event ID {event_id_for_logging} (invoice.payment_succeeded): Error committing updates for sub {stripe_subscription_id}: {e}", exc_info=True)
                        return "Error updating subscription after successful payment", 500 # Respond with error to Stripe.
                else:
                    current_app.logger.info(f"Event ID {event_id_for_logging} (invoice.payment_succeeded): No changes needed for subscription {stripe_subscription_id} (user {user_subscription.user_id}). Status: {user_subscription.status}, End Date: {user_subscription.end_date}.")
            else:
                # This case should be rare if checkout.session.completed was handled correctly.
                current_app.logger.warning(f"Event ID {event_id_for_logging} (invoice.payment_succeeded): UserSubscription not found for stripe_subscription_id {stripe_subscription_id}. Invoice ID: {invoice.id}.")
        else:
            # This could be an invoice for a one-time charge, not related to a subscription.
            current_app.logger.info(f"Event ID {event_id_for_logging} (invoice.payment_succeeded): Received event without a subscription ID (e.g., for a one-time charge or non-subscription product). Invoice ID: {invoice.id}, Customer: {invoice.get('customer')}.")

    # Event: 'customer.subscription.deleted'
    # Signifies: A subscription has been canceled, either immediately or at the end of the current billing period
    # if `cancel_at_period_end` was previously set to true and that date has been reached.
    # Event: 'customer.subscription.updated' with 'cancel_at_period_end' = true
    # Signifies: A subscription has been updated, and specifically, it's now set to cancel at the end of the current period.
    # We treat this as a future cancellation.
    elif event.type == 'customer.subscription.deleted' or \
         (event.type == 'customer.subscription.updated' and event.data.object.get('cancel_at_period_end') is True):

        subscription_data = event.data.object # The Stripe Subscription object.
        stripe_subscription_id = subscription_data.id

        log_prefix = f"Event ID {event_id_for_logging} ({event.type} for Stripe Sub ID {stripe_subscription_id})"
        current_app.logger.info(f"{log_prefix}: Processing subscription cancellation or end.")

        user_subscription = UserSubscription.query.filter_by(stripe_subscription_id=stripe_subscription_id).first()

        if user_subscription:
            if user_subscription.status == SubscriptionStatusEnum.CANCELLED:
                current_app.logger.info(f"{log_prefix}: Subscription for user {user_subscription.user_id} already marked as CANCELLED. No state change needed.")
            else:
                user_subscription.status = SubscriptionStatusEnum.CANCELLED

                # Determine the actual end date based on Stripe's data.
                # For 'customer.subscription.deleted', 'ended_at' is when it was definitively ended.
                # For 'customer.subscription.updated' with 'cancel_at_period_end: true', 'cancel_at' is the timestamp it will be cancelled.
                # If 'cancel_at' is not yet reached, `current_period_end` is also relevant.
                effective_end_date_ts = None
                if event.type == 'customer.subscription.deleted':
                    effective_end_date_ts = subscription_data.get('ended_at')
                elif event.type == 'customer.subscription.updated' and subscription_data.get('cancel_at_period_end'):
                    effective_end_date_ts = subscription_data.get('cancel_at') # This is the timestamp it will cancel.

                if effective_end_date_ts:
                    user_subscription.end_date = datetime.utcfromtimestamp(effective_end_date_ts)
                    current_app.logger.info(f"{log_prefix}: Setting end_date to {user_subscription.end_date.isoformat()} based on 'ended_at' or 'cancel_at'.")
                # Fallback or alternative if specific fields aren't present as expected:
                # elif subscription_data.get('cancel_at_period_end'):
                # user_subscription.end_date = datetime.utcfromtimestamp(subscription_data.current_period_end)
                # current_app.logger.info(f"{log_prefix}: Setting end_date to current_period_end {user_subscription.end_date.isoformat()} for cancel_at_period_end.")

                try:
                    db.session.commit()
                    current_app.logger.info(f"{log_prefix}: UserSubscription for user {user_subscription.user_id} successfully marked as CANCELLED with end_date {user_subscription.end_date}.")
                except Exception as e:
                    db.session.rollback()
                    current_app.logger.error(f"{log_prefix}: Error committing UserSubscription cancellation for user {user_subscription.user_id}: {e}", exc_info=True)
                    return "Error processing subscription cancellation in database", 500
        else:
            current_app.logger.warning(f"{log_prefix}: UserSubscription not found in local database. Cannot process cancellation/end.")

    # Event: 'invoice.payment_failed'
    # Signifies: A payment attempt for an invoice (e.g., subscription renewal) has failed.
    elif event.type == 'invoice.payment_failed':
        invoice = event.data.object # The Invoice object.
        stripe_subscription_id = invoice.get('subscription') # Associated Stripe Subscription ID.

        log_prefix = f"Event ID {event_id_for_logging} (invoice.payment_failed for Stripe Sub ID {stripe_subscription_id})"
        current_app.logger.info(f"{log_prefix}: Processing.")

        if stripe_subscription_id:
            user_subscription = UserSubscription.query.filter_by(stripe_subscription_id=stripe_subscription_id).first()
            if user_subscription:
                # Set local subscription status to PAST_DUE.
                # The user might need to update their payment method via the Stripe Billing Portal.
                # Stripe will typically attempt retries based on your Stripe account's dunning settings.
                # If retries fail, Stripe will eventually trigger 'customer.subscription.deleted' or similar.
                if user_subscription.status != SubscriptionStatusEnum.PAST_DUE:
                    user_subscription.status = SubscriptionStatusEnum.PAST_DUE
                    try:
                        db.session.commit()
                        current_app.logger.info(f"{log_prefix}: UserSubscription for user {user_subscription.user_id} status successfully set to PAST_DUE.")
                    except Exception as e:
                        db.session.rollback()
                        current_app.logger.error(f"{log_prefix}: Error committing PAST_DUE status for user {user_subscription.user_id}: {e}", exc_info=True)
                        return "Error updating subscription to past_due in database", 500
                else:
                    current_app.logger.info(f"{log_prefix}: UserSubscription for user {user_subscription.user_id} already in PAST_DUE status.")
            else:
                current_app.logger.warning(f"{log_prefix}: UserSubscription not found in local database.")
        else:
            current_app.logger.info(f"Event ID {event_id_for_logging} (invoice.payment_failed): No subscription ID associated with this failed invoice (Invoice ID: {invoice.id}). Might be for a one-time payment.")

    # Event: 'customer.subscription.updated' (General updates)
    # Signifies: A subscription has been updated (e.g., plan change, quantity change, trial extended).
    # This is a broad event; specific changes should be checked in the payload.
    # Note: 'cancel_at_period_end' updates are handled in the 'customer.subscription.deleted' block for clarity.
    elif event.type == 'customer.subscription.updated' and not event.data.object.get('cancel_at_period_end'):
        subscription_data = event.data.object # The Stripe Subscription object.
        stripe_subscription_id = subscription_data.id
        log_prefix = f"Event ID {event_id_for_logging} (customer.subscription.updated for Stripe Sub ID {stripe_subscription_id})"
        current_app.logger.info(f"{log_prefix}: Processing general update.")

        user_subscription = UserSubscription.query.filter_by(stripe_subscription_id=stripe_subscription_id).first()
        if not user_subscription:
            current_app.logger.warning(f"{log_prefix}: UserSubscription not found. Cannot process update. This might occur if 'checkout.session.completed' was missed or this is a new subscription not yet created locally.")
            # Consider if a new subscription should be created here if it doesn't exist, though it's less common.
            # This usually implies a synchronization issue or a subscription created outside the app's checkout flow.
            return 'UserSubscription not found for update', 200 # Acknowledge, but log as warning.

        changed_fields = [] # To log what changed.

        # Update Current Period End Date
        new_period_end_dt = datetime.utcfromtimestamp(subscription_data.current_period_end)
        if user_subscription.end_date != new_period_end_dt:
            user_subscription.end_date = new_period_end_dt
            changed_fields.append(f"end_date to {new_period_end_dt.isoformat()}")

        # Update Plan if it changed (check stripe_price_id)
        # Stripe Subscription object contains 'items' -> 'data' -> [0] -> 'price' -> 'id'
        if subscription_data.get('items') and subscription_data.items.data:
            current_stripe_price_id = subscription_data.items.data[0].price.id
            if user_subscription.plan.stripe_price_id != current_stripe_price_id:
                new_plan = SubscriptionPlan.query.filter_by(stripe_price_id=current_stripe_price_id).first()
                if new_plan:
                    user_subscription.plan_id = new_plan.id
                    changed_fields.append(f"plan_id to {new_plan.id} (Stripe Price ID: {current_stripe_price_id})")
                else:
                    current_app.logger.error(f"{log_prefix}: New Stripe Price ID {current_stripe_price_id} does not match any local SubscriptionPlan.")

        # Update Status (e.g., from trial to active, or if it was past_due and now active)
        # Stripe 'status' field can be 'trialing', 'active', 'past_due', 'canceled', 'unpaid', 'incomplete', 'incomplete_expired'.
        stripe_status_str = subscription_data.get('status')
        new_local_status = SubscriptionStatusEnum.from_stripe_status(stripe_status_str) # Assumes a helper method

        if new_local_status and user_subscription.status != new_local_status:
             # Avoid reverting from CANCELLED if Stripe still sends updates for a cancelled sub (rare)
            if user_subscription.status == SubscriptionStatusEnum.CANCELLED and new_local_status != SubscriptionStatusEnum.CANCELLED:
                 current_app.logger.warning(f"{log_prefix}: Attempted to change status from CANCELLED to {new_local_status.value}. Ignoring status update to preserve cancellation.")
            else:
                user_subscription.status = new_local_status
                changed_fields.append(f"status to {new_local_status.value} (from Stripe status '{stripe_status_str}')")


        # Handle trial end date changes
        trial_end_ts = subscription_data.get('trial_end')
        new_trial_end_dt = datetime.utcfromtimestamp(trial_end_ts) if trial_end_ts else None
        # Assuming trial_end_date is stored on UserSubscription model, or handled by status and end_date.
        # If new_trial_end_dt exists and is different from what might be implicitly stored, update.
        # This logic is closely tied to how TRIALING status and end_date are managed.
        # For example, if status is TRIALING, end_date should be trial_end_dt.
        if user_subscription.status == SubscriptionStatusEnum.TRIALING:
            if new_trial_end_dt and user_subscription.end_date != new_trial_end_dt :
                user_subscription.end_date = new_trial_end_dt
                changed_fields.append(f"trial end_date to {new_trial_end_dt.isoformat()}")
            elif not new_trial_end_dt and user_subscription.status == SubscriptionStatusEnum.TRIALING: # Trial ended
                 user_subscription.status = SubscriptionStatusEnum.ACTIVE # Assuming it becomes active
                 user_subscription.end_date = new_period_end_dt # Set to current period end
                 changed_fields.append(f"status to ACTIVE (trial ended), end_date to {new_period_end_dt.isoformat()}")


        if changed_fields:
            try:
                db.session.commit()
                current_app.logger.info(f"{log_prefix}: UserSubscription for user {user_subscription.user_id} updated: {'; '.join(changed_fields)}.")
            except Exception as e:
                db.session.rollback()
                current_app.logger.error(f"{log_prefix}: Error committing UserSubscription updates for user {user_subscription.user_id}: {e}", exc_info=True)
                return "Error updating subscription from general update event", 500
        else:
            current_app.logger.info(f"{log_prefix}: No actionable changes found in customer.subscription.updated event for user {user_subscription.user_id}.")

    else:
        # Log unhandled event types for awareness and future implementation if needed.
        current_app.logger.warning(f"Event ID {event_id_for_logging}: Received unhandled event type '{event.type}'.")
        # It's crucial to return a 200 OK for unhandled events that Stripe might send (e.g., new event types).
        # This tells Stripe that the webhook endpoint is active and receiving events, preventing unnecessary retries for these events.
        return 'Success: Event received but not explicitly handled by this endpoint.', 200

    # Acknowledge receipt of the event to Stripe with a 200 OK status to prevent retries for successfully handled events.
    return 'Success', 200
