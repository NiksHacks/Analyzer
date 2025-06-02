import os # Standard library for operating system interactions (e.g., creating directories).
# import os # Duplicate import removed.
import stripe # Stripe Python library for payment processing.
from flask import Flask # The main Flask class.
from flask_migrate import Migrate # For handling database migrations with Flask-Migrate.
from config import Config # Import the application's configuration class.
from extensions import db, login_manager, oauth # Import initialized extensions.
from models.user import User # Import User model, primarily for the user_loader.

# Application Factory Function
def create_app():
    """
    Application factory for creating and configuring the Flask app.
    This pattern is useful for creating multiple app instances (e.g., for testing)
    and avoids global app objects.
    """
    # Create a Flask application instance.
    # `instance_relative_config=True` allows loading config from an 'instance' folder (not used here but good practice).
    app = Flask(__name__, instance_relative_config=True)

    # Load configuration from the Config object (defined in config.py).
    app.config.from_object(Config)

    # --- Initialize Stripe ---
    # Set the Stripe API secret key from the application configuration.
    # This is necessary for making server-side calls to the Stripe API.
    stripe.api_key = app.config['STRIPE_SECRET_KEY']

    # --- Initialize Flask Extensions ---
    # Initialize SQLAlchemy with the app (for database ORM).
    db.init_app(app)
    # Initialize Flask-Migrate for database schema migrations.
    # This links the Flask app and SQLAlchemy DB instance to the migration engine.
    migrate = Migrate(app, db)

    # Initialize Flask-Login for user session management.
    login_manager.init_app(app)
    # Specify the login view. If a user tries to access a @login_required route
    # without being authenticated, they will be redirected to this view.
    login_manager.login_view = 'auth.login' # 'auth' is the blueprint name, 'login' is the route function.

    # Initialize Authlib's OAuth client registry with the app.
    oauth.init_app(app)

    # --- OAuth Client Registrations with Authlib ---
    # These blocks register OAuth providers (Google, Meta for login; Google Ads, Meta Ads for API integration)
    # with Authlib, configuring them with credentials and endpoints.

    # General Google OAuth for user login ("Login with Google").
    oauth.register(
        name='google', # Name used to refer to this provider (e.g., oauth.google).
        client_id=app.config['GOOGLE_CLIENT_ID'], # Google OAuth Client ID.
        client_secret=app.config['GOOGLE_CLIENT_SECRET'], # Google OAuth Client Secret.
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration', # Auto-discovery for endpoints.
        client_kwargs={'scope': 'openid email profile'} # Scopes requested: OpenID Connect, email, profile.
    )

    # General Meta (Facebook) OAuth for user login ("Login with Meta/Facebook").
    oauth.register(
        name='meta', # Name for this provider.
        client_id=app.config['META_CLIENT_ID'], # Meta App ID.
        client_secret=app.config['META_CLIENT_SECRET'], # Meta App Secret.
        authorize_url='https://www.facebook.com/dialog/oauth', # Facebook's authorization endpoint.
        authorize_params=None, # Additional parameters for authorization URL, if any.
        access_token_url='https://graph.facebook.com/oauth/access_token', # Endpoint to get access token.
        access_token_params=None, # Additional parameters for access token request.
        refresh_token_url=None, # Facebook long-lived tokens are obtained via exchange, not standard refresh.
        userinfo_endpoint='https://graph.facebook.com/me?fields=id,name,email', # Endpoint to get user info.
        client_kwargs={'scope': 'email public_profile'} # Scopes requested: email, public profile.
    )

    # Google Ads API OAuth Integration.
    oauth.register(
        name='google_ads',
        client_id=app.config['GOOGLE_ADS_CLIENT_ID'],       # Google Ads API specific Client ID.
        client_secret=app.config['GOOGLE_ADS_CLIENT_SECRET'], # Google Ads API specific Client Secret.
        authorize_url='https://accounts.google.com/o/oauth2/v2/auth', # Google's OAuth 2.0 authorization endpoint.
        authorize_params=None,
        access_token_url='https://oauth2.googleapis.com/token',      # Google's OAuth 2.0 token endpoint.
        access_token_params=None,
        refresh_token_url='https://oauth2.googleapis.com/token',      # Google's token endpoint is also used for refreshing.
        userinfo_endpoint=None, # Not typically used for Ads API; user info is through Google login if separate.
        client_kwargs={
            'scope': 'https://www.googleapis.com/auth/adwords', # Scope required for Google Ads API access.
            'prompt': 'consent',      # Ensures user is prompted for consent, important for getting refresh token.
            'access_type': 'offline'  # Crucial for obtaining a refresh token for long-term API access.
        }
    )

    # Meta (Facebook) Ads API OAuth Integration.
    # Uses a specific API version in URLs for stability.
    meta_api_version = app.config.get('META_GRAPH_API_VERSION', 'v18.0') # Get from config or default
    oauth.register(
        name='meta_ads',
        client_id=app.config['META_ADS_APP_ID'],           # Meta App ID for Ads API.
        client_secret=app.config['META_ADS_APP_SECRET'],     # Meta App Secret for Ads API.
        authorize_url=f'https://www.facebook.com/{meta_api_version}/dialog/oauth', # Authorization URL with API version.
        authorize_params=None,
        access_token_url=f'https://graph.facebook.com/{meta_api_version}/oauth/access_token', # Access token URL.
        access_token_params=None,
        userinfo_endpoint=f'https://graph.facebook.com/{meta_api_version}/me?fields=id,name', # User info (typically app-scoped ID).
        refresh_token_url=None, # Meta uses long-lived tokens, not standard refresh tokens here.
        client_kwargs={
            'scope': 'ads_management ads_read read_insights business_management', # Scopes for Ads API access.
        }
    )

    # Ensure the instance folder exists (though not strictly used in this config).
    # Flask uses the instance folder for instance-specific configuration or files.
    try:
        os.makedirs(app.instance_path)
    except OSError:
        pass # Folder already exists or cannot be created (less critical here).

    # A simple test route for the root URL.
    @app.route('/')
    def hello_world():
        return 'Hello World!' # Placeholder, likely replaced by a main blueprint's index.

    # --- Import and Register Blueprints ---
    # Blueprints help organize routes and views into modular components.
    # Import blueprint objects from their respective route files.
    from routes.auth import auth_bp
    from routes.billing import billing_bp
    from routes.integration import integration_bp
    from routes.dashboard import dashboard_bp
    from routes.ai_insights import ai_insights_bp
    from routes.optimization import optimization_bp
    from routes.main import main_bp # For general site pages like home, about, etc.

    # Register blueprints with the application, optionally setting URL prefixes.
    app.register_blueprint(auth_bp, url_prefix='/auth') # All auth routes will be under /auth/...
    app.register_blueprint(billing_bp)      # No prefix, routes defined directly in billing_bp.
    app.register_blueprint(integration_bp)  # No prefix for integration routes.
    app.register_blueprint(dashboard_bp)    # No prefix for dashboard routes.
    app.register_blueprint(ai_insights_bp)  # No prefix for AI insights routes.
    app.register_blueprint(optimization_bp) # No prefix for optimization routes.
    app.register_blueprint(main_bp)         # No prefix for main site routes.

    # --- Flask-Login User Loader ---
    # This callback is used by Flask-Login to reload the user object from the
    # user ID stored in the session. It's called on each request for an authenticated user.
    @login_manager.user_loader
    def load_user(user_id):
        """Loads a user from the database given their ID."""
        return User.query.get(int(user_id)) # Fetches user by primary key.

    return app # Return the configured Flask app instance.

# This block allows running the Flask development server directly using `python app.py`.
if __name__ == '__main__':
    app = create_app() # Create an app instance using the factory.
    app.run(debug=True)
