import os # For accessing environment variables.

class Config:
    """
    Configuration class for the Flask application.

    Loads settings from environment variables, with sensible defaults for development where applicable.
    Environment variables are the preferred way to set configuration for security and flexibility,
    especially in production environments.
    """

    # --- General Flask Configuration ---
    # Secret key for session management, CSRF protection, etc.
    # CRITICAL: Should be a long, random string and kept secret in production.
    # Loaded from SECRET_KEY environment variable, with a fallback for development (not suitable for production).
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'a-very-complex-and-unguessable-secret-key-for-dev' # Changed fallback for clarity

    # --- Database Configuration ---
    # URI for connecting to the PostgreSQL database.
    # Loaded from DATABASE_URL environment variable.
    # Example: 'postgresql://username:password@host:port/database_name'
    # Fallback is a generic local PostgreSQL URI (ensure your local setup matches or set DATABASE_URL).
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'postgresql://your_db_user:your_db_password@localhost:5432/adinsights_db'
    # Disables Flask-SQLAlchemy's event system, which is not needed by default and adds overhead.
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- OAuth General Login Configurations (e.g., for "Login with Google/Meta") ---
    # Google OAuth credentials for user login (not Ads API).
    GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_LOGIN_CLIENT_ID') # Renamed for clarity vs Ads
    GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_LOGIN_CLIENT_SECRET') # Renamed for clarity
    # Meta (Facebook) OAuth credentials for user login.
    META_CLIENT_ID = os.environ.get('META_LOGIN_CLIENT_ID') # Renamed for clarity
    META_CLIENT_SECRET = os.environ.get('META_LOGIN_CLIENT_SECRET') # Renamed for clarity

    # --- Stripe Configuration (for billing) ---
    # Stripe secret API key. Required for all server-side Stripe API calls.
    STRIPE_SECRET_KEY = os.environ.get('STRIPE_SECRET_KEY')
    # Stripe publishable API key. Used on the client-side (e.g., with Stripe.js).
    STRIPE_PUBLISHABLE_KEY = os.environ.get('STRIPE_PUBLISHABLE_KEY')
    # Stripe webhook signing secret. Used to verify webhook events from Stripe.
    STRIPE_WEBHOOK_SECRET = os.environ.get('STRIPE_WEBHOOK_SECRET')

    # --- Google Ads API OAuth Configurations (for ad platform integration) ---
    # Client ID for Google Ads API OAuth flow. Distinct from general Google Login.
    GOOGLE_ADS_CLIENT_ID = os.environ.get('GOOGLE_ADS_API_CLIENT_ID') # Renamed for clarity
    # Client Secret for Google Ads API OAuth flow.
    GOOGLE_ADS_CLIENT_SECRET = os.environ.get('GOOGLE_ADS_API_CLIENT_SECRET') # Renamed for clarity

    # --- Meta (Facebook) Ads API App Configurations (for ad platform integration) ---
    # App ID for your Meta (Facebook) App configured for Ads API access.
    META_ADS_APP_ID = os.environ.get('META_ADS_APP_ID')
    # App Secret for your Meta (Facebook) App.
    META_ADS_APP_SECRET = os.environ.get('META_ADS_APP_SECRET')
    # Optional: Specify a fixed Graph API version if needed, otherwise Authlib might use a default.
    # META_GRAPH_API_VERSION = os.environ.get('META_GRAPH_API_VERSION', 'v18.0')


    # --- Google Ads API Specific (Service Access) ---
    # Developer Token for accessing the Google Ads API. This is obtained from your Google Ads MCC account.
    GOOGLE_ADS_DEVELOPER_TOKEN = os.environ.get('GOOGLE_ADS_DEVELOPER_TOKEN')
    # Optional: Login Customer ID (MCC ID if managing multiple accounts, or individual account ID).
    # If using an MCC, this ID is used by the Google Ads client library to specify which manager account
    # is making the API requests. Can be overridden or set per-request if needed.
    # Set to None if not globally applicable or if managed dynamically.
    GOOGLE_ADS_LOGIN_CUSTOMER_ID = os.environ.get('GOOGLE_ADS_LOGIN_CUSTOMER_ID', None)

    # --- Fernet Key for Encryption (for storing sensitive data like OAuth tokens) ---
    # A URL-safe base64-encoded 32-byte key. Must be kept secret.
    # Generate using `from cryptography.fernet import Fernet; Fernet.generate_key().decode()`.
    # Loaded from FERNET_KEY environment variable.
    # .encode() ensures it's bytes, as required by Fernet, assuming the env var stores it as a string.
    # If FERNET_KEY is not set, encryption/decryption will fail. A default is NOT provided for security.
    FERNET_KEY = os.environ.get('FERNET_KEY', '').encode('utf-8') # Ensure it's bytes
    # Example of how to set it if missing, for dev only (DO NOT USE THIS KEY IN PROD):
    # if not FERNET_KEY:
    #     print("WARNING: FERNET_KEY not set, using a default DEV key. Generate a new key for production.")
    #     FERNET_KEY = b'gQhY_-s_xAl5xdfkZkP7T5YfImqfJ8zY1jrnZgYXDEo=' # Example DEV key

    # --- Logging Configuration (Optional) ---
    # LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
    # Example: Add more specific logging configurations if needed.

    # Ensure critical keys are set, especially for production environments.
    # (This is a conceptual check; actual enforcement might be done at app startup)
    # def __init__(self):
    #     if not self.SECRET_KEY or self.SECRET_KEY == 'you-will-never-guess':
    #         # In a real app, you might raise an error or log a critical warning if not in DEBUG mode
    #         print("WARNING: SECRET_KEY is not set to a secure value.")
    #     if not self.FERNET_KEY:
    #         print("CRITICAL: FERNET_KEY is not set. Encryption will fail.")
    #         # raise ValueError("FERNET_KEY is not set in environment variables.")
    #     if not self.SQLALCHEMY_DATABASE_URI or 'user:password@localhost/dbname' in self.SQLALCHEMY_DATABASE_URI:
    #         print("WARNING: DATABASE_URL is not set or using a default placeholder.")
