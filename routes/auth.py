from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy.exc import IntegrityError
from authlib.integrations.base_client.errors import OAuthError

from forms import LoginForm, RegistrationForm
from models.user import User
from models.subscription_plan import SubscriptionPlan
from extensions import db, oauth # oauth is used for OAuth logins
from ..utils.decorators import subscription_required # Custom decorator for subscription checks
from ..utils.helpers import is_safe_url # Utility to ensure safe redirects

# Blueprint for authentication-related routes.
# This blueprint groups all authentication views (login, logout, registration, OAuth, profile)
# under the '/auth' URL prefix, making the application's routing structure more organized.
auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

# Flask-Login user_loader callback.
# This function is used by Flask-Login to reload the user object from the user ID stored in the session.
# It's essential for Flask-Login to manage user sessions across requests.
# Note: This function is typically defined in the main app setup or models (e.g., models/user.py),
# not usually directly within a blueprint's routes.py if it's a global user loader.
# Example (if it were here or for context):
# @login_manager.user_loader
# def load_user(user_id):
#     return User.query.get(int(user_id))

# Route for user registration.
# Supports GET to display the form and POST to process submitted data.
@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    """
    Handles user registration.
    GET: Displays the registration form.
    POST: Processes registration form, creates a new user, logs them in,
          and redirects to their profile or dashboard.
    """
    # If user is already authenticated, redirect them to the main dashboard.
    # This prevents logged-in users from accessing the registration page.
    if current_user.is_authenticated: # Check if the current user is already logged in via Flask-Login.
        return redirect(url_for('dashboard.main_dashboard')) # Redirect to the main dashboard.

    form = RegistrationForm() # Instantiate the registration form.
    # Process form data if it's a POST request and the form is valid.
    # form.validate_on_submit() checks for CSRF token and runs all defined field validators.
    if form.validate_on_submit():
        # Create a new user instance with validated form data (email and full name).
        new_user = User(email=form.email.data, full_name=form.full_name.data)
        new_user.set_password(form.password.data) # Hash the password for secure storage

        try:
            db.session.add(new_user) # Add the new user object to the database session
            db.session.commit() # Commit the transaction to save the user to the database.
            login_user(new_user) # Log in the new user using Flask-Login's login_user function.
            current_app.logger.info(f"New user registered: {new_user.email}")
            flash('Congratulations, you are now a registered user!', 'success')
            # Redirect to the user's profile page after successful registration.
            # This provides immediate feedback and access to their new account area.
            return redirect(url_for('auth.profile'))
        except IntegrityError: # Handle specific database error for duplicate entries.
            # This exception typically occurs if a unique constraint is violated (e.g., email already exists).
            db.session.rollback() # Rollback the database session to undo the failed transaction.
            flash('That email address is already registered. Please use a different email or log in.', 'danger')
            current_app.logger.warning(f"Registration failed for email {form.email.data}: email already exists (IntegrityError).")
        except Exception as e: # Catch any other unexpected errors during the registration process.
            # This provides a generic error message to the user and logs the specific error.
            db.session.rollback() # Rollback the session in case of other unforeseen errors.
            flash('An error occurred during registration. Please try again later.', 'danger')
            current_app.logger.error(f"Error during registration for {form.email.data}: {e}", exc_info=True)

    # If it's a GET request (user is navigating to the page) or if form validation failed (e.g., invalid email format),
    # render the registration template, passing the form object to display errors and repopulate fields.
    return render_template('auth/register.html', title='Register', form=form)

# Route for user login.
# Supports GET to display the form and POST to process submitted credentials.
@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """
    Handles user login.
    GET: Displays the login form.
    POST: Processes login form, authenticates the user, and redirects.
    """
    # If user is already authenticated, redirect them to the main dashboard.
    # Prevents logged-in users from accessing the login page again.
    if current_user.is_authenticated: # Check Flask-Login's current_user proxy.
        return redirect(url_for('dashboard.main_dashboard')) # Redirect if already logged in.

    form = LoginForm() # Instantiate the login form.
    # Process form data if it's a POST request and the form is valid.
    # validate_on_submit() handles CSRF and field validation.
    if form.validate_on_submit():
        # Attempt to retrieve the user from the database by the provided email.
        user = User.query.filter_by(email=form.email.data).first()

        # Verify if the user exists and the password matches.
        # user.check_password() compares the provided plain password with the stored hashed password.
        if user is None or not user.check_password(form.password.data):
            current_app.logger.warning(f"Failed login attempt for email: {form.email.data} due to invalid credentials.")
            flash('Invalid email or password. Please try again.', 'danger')
            return redirect(url_for('auth.login')) # Redirect back to login page on authentication failure.

        # If authentication is successful, log in the user using Flask-Login.
        # login_user establishes a user session.
        # The 'remember' flag (form.remember_me.data) determines if the session cookie is persistent ("Remember Me" functionality).
        login_user(user, remember=form.remember_me.data)
        current_app.logger.info(f"User {user.email} logged in successfully.")

        # Handle 'next' query parameter for redirecting after login.
        # This is useful if the user was redirected to login from a page they were trying to access (e.g., a @login_required page).
        next_page = request.args.get('next')
        # Validate the 'next_page' URL to prevent open redirect attacks.
        # is_safe_url checks if the URL is relative or belongs to the same host.
        if next_page and is_safe_url(next_page):
            return redirect(next_page) # Redirect to the originally requested page.
        else:
            # If 'next' is not provided, is empty, or is unsafe, redirect to a default page (e.g., main dashboard).
            return redirect(url_for('dashboard.main_dashboard'))

    # If it's a GET request (initial page load) or form validation failed,
    # render the login template, passing the form object for display (including any validation errors).
    return render_template('auth/login.html', title='Login', form=form)

# Route for user logout.
# Requires the user to be logged in.
@auth_bp.route('/logout')
@login_required # Flask-Login decorator: ensures only authenticated users can access this route.
def logout():
    """
    Logs out the current user by clearing the session and redirects to the login page.
    """
    user_email = current_user.email # Capture email for logging before the session is cleared (current_user becomes unavailable).
    logout_user() # Flask-Login function to remove the user from the session and clear the "Remember Me" cookie if set.
    current_app.logger.info(f"User {user_email} logged out.")
    flash('You have been logged out successfully.', 'info')
    # Redirect to the login page after successful logout, as the current page might require authentication.
    return redirect(url_for('auth.login'))

# Route for user profile page.
# This page displays user-specific information and settings.
# Requires the user to be logged in (enforced by @login_required decorator from Flask-Login).
# Requires an active subscription (enforced by the custom @subscription_required decorator).
@auth_bp.route('/profile')
@login_required
@subscription_required() # Custom decorator: checks if the user has an active subscription.
def profile():
    """
    Displays the user's profile page.
    Requires login and an active subscription.
    """
    # Renders the profile template. User-specific data is available in templates via `current_user` (Flask-Login).
    return render_template('auth/profile.html', title='Profile')

# --- Google OAuth Routes ---

# Route to initiate Google OAuth 2.0 login.
# This is the first step in the OAuth flow when a user clicks "Login with Google".
@auth_bp.route('/google/login')
def google_login():
    """
    Initiates the Google OAuth 2.0 login flow.
    This function redirects the user to Google's authorization page.
    """
    # Generate the callback URI that Google will redirect to after the user authorizes the application.
    # `_external=True` ensures a full absolute URL (including domain and protocol) is generated, which is required by Google.
    redirect_uri = url_for('auth.google_callback', _external=True)
    # Use the Authlib client instance for Google (configured as 'google' in `oauth` extensions)
    # to generate the authorization URL and redirect the user to Google's OAuth server.
    # Scopes requested (like 'openid', 'email', 'profile') are typically configured when initializing the Authlib client.
    return oauth.google.authorize_redirect(redirect_uri)

# Route to handle the callback from Google after OAuth authorization.
# This is the second step in the OAuth flow. Google redirects the user's browser back to this URL.
@auth_bp.route('/google/callback')
def google_callback():
    """
    Handles the callback from Google after user authorization.
    It exchanges the authorization code for an access token, retrieves user information from Google,
    finds or creates a corresponding user in the local database, logs them in, and redirects.
    """
    try:
        # Step 1: Authorize access token.
        # Authlib's `authorize_access_token()` method handles exchanging the authorization code
        # (sent by Google in the request query parameters) for an access token.
        token = oauth.google.authorize_access_token()
    except OAuthError as e: # Handle specific OAuth errors from Authlib (e.g., user denied access, invalid code).
        flash('Authentication failed with Google. Please try again.', 'danger')
        current_app.logger.error(f"OAuthError during Google callback (token exchange): {e.error} - {e.description}", exc_info=True)
        return redirect(url_for('auth.login')) # Redirect to login page on failure.
    except Exception as e: # Catch any other unexpected errors during the token exchange process.
        flash('An unexpected error occurred during Google authentication. Please try again.', 'danger')
        current_app.logger.error(f"Unexpected error during Google token exchange: {e}", exc_info=True)
        return redirect(url_for('auth.login'))

    # Step 2: Fetch user information from Google.
    # The 'userinfo' method uses the obtained access token to request user details
    # from Google's userinfo endpoint (part of OpenID Connect standard).
    user_info = oauth.google.userinfo(token=token)

    # Extract necessary details from the user_info response.
    google_id = user_info.get('sub') # 'sub' (subject) is the standard OpenID Connect field for the unique user ID.
    email = user_info.get('email') # User's email address.
    full_name = user_info.get('name') # User's full name.
    # Other fields like 'picture', 'given_name', 'family_name' might also be available depending on scopes.

    # Email is essential for account creation and linking in this application.
    if not email:
        flash('Email not provided by Google. Cannot create or link account without an email address.', 'danger')
        current_app.logger.warning("Google OAuth: Email not provided in user_info response.")
        return redirect(url_for('auth.login'))

    # Step 3 & 4: Find existing user by OAuth ID or email, or create a new user.
    user = User.query.filter_by(google_id=google_id).first() # Check if a user already exists with this Google ID.
    created_new_user_flag = False # Flag to indicate if a new user record was created for logging/flash messages.

    if not user:
        # If no user found with this google_id, check if an account exists with the same email address.
        # This allows linking Google OAuth to an account previously created with email/password or another OAuth.
        user = User.query.filter_by(email=email).first()
        if user:
            # Step 5: Logic for linking OAuth ID to an existing email account.
            # User exists with this email. Link their Google ID to this existing account.
            user.google_id = google_id
            current_app.logger.info(f"Linking Google ID {google_id} to existing user {email}.")
        else:
            # No existing user by Google ID or email; create a new user.
            user = User(email=email, full_name=full_name, google_id=google_id)
            # Note: New users created via OAuth typically don't have a password set directly
            # until they choose to set one through a password reset/set mechanism if implemented.
            db.session.add(user) # Add the new User object to the database session.
            created_new_user_flag = True # Mark that a new user was created.

    # Step 6: Database operations (commit) and error handling.
    try:
        db.session.commit() # Commit the transaction to save changes (new user or updated google_id for an existing user).
        if created_new_user_flag:
            current_app.logger.info(f"New user {user.email} (Google ID: {user.google_id}) registered via Google OAuth.")
            # Optionally, flash a different message for new registrations vs logins.

        # Step 7: User login.
        login_user(user) # Log in the user using Flask-Login. This establishes their session.
        current_app.logger.info(f"User {user.email} logged in via Google OAuth.")
        flash('Successfully logged in with Google!', 'success')

        # Step 8: Redirection.
        # Redirect to the main dashboard or a 'next' page if one was stored (e.g., from @login_required).
        next_page = request.args.get('next') # Check for a 'next' URL from request arguments.
        if next_page and is_safe_url(next_page): # Ensure safety of the redirect URL.
            return redirect(next_page)
        return redirect(url_for('dashboard.main_dashboard')) # Default redirect.
    except IntegrityError:
        # This could occur if, in a rare race condition, an email was registered
        # or another OAuth ID was linked to this email between the earlier checks and this commit.
        db.session.rollback() # Rollback the transaction to maintain data integrity.
        flash('This Google account appears to be associated with another user, or the email is already in use by a different account. Please log in with your existing credentials or contact support if you believe this is an error.', 'danger')
        current_app.logger.warning(f"Google OAuth: IntegrityError for email {email} or google_id {google_id}. Possible duplicate account or race condition.")
        return redirect(url_for('auth.login'))
    except Exception as e: # Catch other potential database errors or unexpected issues.
        db.session.rollback()
        flash('An error occurred while linking your Google account. Please try again or contact support.', 'danger')
        current_app.logger.error(f"Google OAuth: Error saving user {email} (Google ID: {google_id}): {e}", exc_info=True)
        return redirect(url_for('auth.login'))

# --- Meta (Facebook) OAuth Routes ---

# Route to initiate Meta (Facebook) OAuth 2.0 login.
# This is the first step in the Meta OAuth flow.
@auth_bp.route('/meta/login')
def meta_login():
    """
    Initiates the Meta (Facebook) OAuth 2.0 login flow.
    This function redirects the user to Facebook's authorization page.
    """
    # Generate the callback URI that Meta will redirect to after user authorization.
    redirect_uri = url_for('auth.meta_callback', _external=True) # `_external=True` for an absolute URL.
    # Use Authlib client for Meta (configured as 'meta') to redirect to Meta's OAuth server.
    # Scopes like 'email', 'public_profile' are configured during Authlib client setup.
    return oauth.meta.authorize_redirect(redirect_uri)

# Route to handle the callback from Meta (Facebook) after OAuth authorization.
# This is the second step in the Meta OAuth flow. Meta redirects the user here.
@auth_bp.route('/meta/callback')
def meta_callback():
    """
    Handles the callback from Meta (Facebook) after user authorization.
    Retrieves user info, finds or creates a user, and logs them in.
    """
    try:
        # Step 1: Authorize access token.
        # Exchange the authorization code (from query params) for an access token with Meta.
        token = oauth.meta.authorize_access_token()
    except OAuthError as e: # Handle specific OAuth errors (e.g., user denied, code expired).
        flash('Authentication failed with Meta. Please try again.', 'danger')
        current_app.logger.error(f"OAuthError during Meta callback (token exchange): {e.error} - {e.description}", exc_info=True)
        return redirect(url_for('auth.login'))
    except Exception as e: # Catch other unexpected errors during token exchange.
        flash('An unexpected error occurred during Meta authentication. Please try again.', 'danger')
        current_app.logger.error(f"Unexpected error during Meta token exchange: {e}", exc_info=True)
        return redirect(url_for('auth.login'))

    try:
        # Step 2: Fetch user information from Meta's Graph API.
        # Request 'id', 'name', and 'email' fields for the user. Other fields require specific permissions.
        user_info_response = oauth.meta.get('me?fields=id,name,email', token=token)
        user_info_response.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx).
        user_info = user_info_response.json() # Parse the JSON response from Meta.
    except Exception as e:
        flash('Failed to fetch user information from Meta. Please ensure your Meta account has a verified email and try again.', 'danger')
        current_app.logger.error(f"Meta OAuth: Error fetching user info: {e}", exc_info=True)
        return redirect(url_for('auth.login'))

    # Extract user details from the Meta API response.
    meta_id = user_info.get('id') # Meta's unique user ID for this app.
    email = user_info.get('email') # User's email. Note: User might not have granted email permission or may not have an email linked/verified.
    full_name = user_info.get('name') # User's full name on Meta.

    # Meta ID is essential for identifying the user and linking the account.
    if not meta_id:
        flash('Could not retrieve your Meta ID. Authentication failed. Please try again.', 'danger')
        current_app.logger.error("Meta OAuth: Meta ID not found in user_info response.")
        return redirect(url_for('auth.login'))

    # Email is critical for this application's account system (user uniqueness, communication).
    # If Meta doesn't provide an email, the user cannot be registered or logged in through this flow.
    if not email:
        flash('Email not provided by Meta. Cannot create or link account without an email address. Please ensure your Meta account has a primary email and you grant permission for email access.', 'danger')
        current_app.logger.warning(f"Meta OAuth: Email not provided for meta_id {meta_id}.")
        return redirect(url_for('auth.login'))

    # Step 3 & 4: Find existing user by OAuth ID or email, or create a new user.
    user = User.query.filter_by(meta_id=meta_id).first() # Check if user exists with this Meta ID.
    created_new_user_flag = False # Flag for logging/messaging if a new user is created.

    if not user:
        # No user found with this meta_id. Check if an account exists with the same email address.
        user = User.query.filter_by(email=email).first()
        if user:
            # Step 5: Logic for linking OAuth ID to an existing email account.
            # User exists (e.g., registered via email/password or Google OAuth). Link their Meta ID.
            user.meta_id = meta_id
            current_app.logger.info(f"Linking Meta ID {meta_id} to existing user {email}.")
        else:
            # No existing user by Meta ID or email; create a new user.
            user = User(email=email, full_name=full_name, meta_id=meta_id)
            db.session.add(user) # Add new User object to the database session.
            created_new_user_flag = True # Mark that a new user was created.

    # Step 6: Database operations (commit) and error handling.
    try:
        db.session.commit() # Commit the transaction (new user or updated Meta ID).
        if created_new_user_flag:
            current_app.logger.info(f"New user {user.email} (Meta ID: {user.meta_id}) registered via Meta OAuth.")

        # Step 7: User login.
        login_user(user) # Log in the user using Flask-Login.
        current_app.logger.info(f"User {user.email} logged in via Meta OAuth.")
        flash('Successfully logged in with Meta!', 'success')

        # Step 8: Redirection.
        # Redirect to the main dashboard or a 'next' page.
        next_page = request.args.get('next') # Check for 'next' URL.
        if next_page and is_safe_url(next_page): # Validate redirect URL.
            return redirect(next_page)
        return redirect(url_for('dashboard.main_dashboard')) # Default redirect.
    except IntegrityError:
        # This can happen if the email from Meta is already tied to a local account
        # that is not linked to this specific Meta ID (e.g., user registered with email, then tries Meta login with same email,
        # or a race condition if two OAuth callbacks for the same new user process nearly simultaneously).
        db.session.rollback() # Rollback the transaction.
        flash('This Meta account appears to be associated with another user, or the email is already in use by a different account. Please log in with your existing credentials or contact support.', 'danger')
        current_app.logger.warning(f"Meta OAuth: IntegrityError for email {email} or meta_id {meta_id}. Possible duplicate or race condition.")
        return redirect(url_for('auth.login'))
    except Exception as e: # Catch other potential database or unexpected errors.
        db.session.rollback()
        flash('An error occurred while linking your Meta account. Please try again or contact support.', 'danger')
        current_app.logger.error(f"Meta OAuth: Error saving user {email} (Meta ID: {meta_id}): {e}", exc_info=True)
        return redirect(url_for('auth.login'))

# Route to display subscription plans available to users.
# This page is typically accessed after login, allowing users to see available plans, or perhaps upgrade/change their current plan.
@auth_bp.route('/subscription-plans')
@login_required # Ensures only logged-in users can view subscription plans.
def subscription_plans_overview():
    """
    Displays available subscription plans to the logged-in user.
    Fetches all plans from the database and renders them in a template.
    """
    # Fetches all subscription plans from the database.
    # Ordering by price (ascending) is a common way to display plans.
    plans = SubscriptionPlan.query.order_by(SubscriptionPlan.price).all()
    # Renders the template, passing the list of plans to be displayed.
    # The template is expected to be named 'Subscription Plans Overview.html' (or similar)
    # and located within the appropriate template folder (e.g., 'templates/auth/').
    # Adding a title for the page.
    return render_template('Subscription Plans Overview.html', plans=plans, title='Subscription Plans')
