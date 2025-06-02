import enum
from datetime import datetime
from extensions import db
from utils.security import encrypt_token, decrypt_token # For encrypting/decrypting tokens.

class PlatformNameEnum(enum.Enum):
    """
    Enumeration for supported advertising platform names.
    Provides a controlled vocabulary for identifying ad platforms.
    """
    GOOGLE_ADS = 'GoogleAds' # Represents Google Ads platform.
    META_ADS = 'MetaAds'     # Represents Meta (Facebook/Instagram) Ads platform.

class IntegrationStatusEnum(enum.Enum):
    """
    Enumeration for the possible statuses of an ad platform integration.
    Indicates the current state of the connection to the ad platform.
    """
    ACTIVE = 'active'    # Integration is active and tokens are valid.
    REVOKED = 'revoked'  # User has revoked access, or token was invalidated by the provider.
    EXPIRED = 'expired'  # Access token has expired, and refresh might be needed or failed.
    PENDING = 'pending'  # Integration process started but not yet completed (e.g., pending ad account selection).
    ERROR = 'error'      # An error occurred, making the integration non-operational.

class AdPlatformIntegration(db.Model):
    """
    Represents an integration instance between a user and a specific ad platform
    (e.g., a user's connection to a specific Google Ads or Meta Ads account).

    This model stores OAuth tokens (encrypted), ad account identifiers, status of
    the integration, and associated timestamps. It uses property decorators
    for automatic encryption and decryption of sensitive token data.
    """
    __tablename__ = 'ad_platform_integrations' # Specifies the database table name.

    id = db.Column(db.Integer, primary_key=True) # Unique identifier for the integration record.

    # --- User and Platform Identification ---
    # Foreign key linking to the 'users' table, establishing which user this integration belongs to.
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    # Name of the ad platform, using the PlatformNameEnum for controlled values.
    platform_name = db.Column(db.Enum(PlatformNameEnum), nullable=False, index=True)

    # --- Ad Account Details ---
    # The ad platform's specific account ID (e.g., Google Ads Customer ID, Meta Ad Account ID).
    # Nullable as it might be pending selection by the user after initial OAuth connection.
    ad_account_id = db.Column(db.String(255), nullable=True, index=True)
    # User-friendly name for the ad account (e.g., "My Google Ads Main Account").
    ad_account_name = db.Column(db.String(255), nullable=True)

    # --- Token Management (Encrypted) ---
    # Stores the encrypted OAuth access token. Text type for potentially long tokens.
    access_token_encrypted = db.Column(db.Text, nullable=False)
    # Stores the encrypted OAuth refresh token, if provided by the platform (e.g., Google Ads).
    # Nullable as not all platforms or OAuth flows provide refresh tokens (e.g., some Meta flows).
    refresh_token_encrypted = db.Column(db.Text, nullable=True)
    # Timestamp indicating when the current access token expires. Nullable if expiry is not applicable or unknown.
    token_expiry = db.Column(db.DateTime, nullable=True)

    # --- Additional Integration Details ---
    # Stores the OAuth scopes granted during authorization, typically as a JSON list of strings.
    scopes = db.Column(db.JSON, nullable=True)
    # Current status of the integration, using IntegrationStatusEnum. Defaults to PENDING.
    status = db.Column(db.Enum(IntegrationStatusEnum), nullable=False, default=IntegrationStatusEnum.PENDING, index=True)

    # --- Timestamps ---
    created_at = db.Column(db.DateTime, default=datetime.utcnow) # Timestamp of when the integration record was created.
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow) # Timestamp of the last update.

    # --- Relationships ---
    # Defines a many-to-one relationship with the User model.
    # 'user' attribute allows accessing the User object from an AdPlatformIntegration instance.
    # 'backref='ad_integrations'' adds 'ad_integrations' attribute to User instances, allowing access to all their integrations.
    user = db.relationship('User', backref=db.backref('ad_integrations', lazy='dynamic'))

    def __repr__(self):
        """
        Provides a string representation of the AdPlatformIntegration object, useful for debugging.
        """
        return f'<AdPlatformIntegration UserID:{self.user_id} - Platform:{self.platform_name.value} (AdAccount: {self.ad_account_name or self.ad_account_id}) - Status:{self.status.value}>'

    # --- Access Token Property ---
    @property
    def access_token(self):
        """
        Property getter for the access token.
        Decrypts the stored `access_token_encrypted` value before returning it.

        Returns:
            str or None: The decrypted access token, or None if no encrypted token is stored.
        """
        if self.access_token_encrypted:
            return decrypt_token(self.access_token_encrypted)
        return None

    @access_token.setter
    def access_token(self, value):
        """
        Property setter for the access token.
        Encrypts the provided token value before storing it in `access_token_encrypted`.

        Args:
            value (str): The plain-text access token to store.
        """
        if value:
            self.access_token_encrypted = encrypt_token(value)
        else:
            self.access_token_encrypted = None # Handle cases where token might be cleared.

    # --- Refresh Token Property ---
    @property
    def refresh_token(self):
        """
        Property getter for the refresh token.
        Decrypts the stored `refresh_token_encrypted` value.

        Returns:
            str or None: The decrypted refresh token, or None if not set.
        """
        if self.refresh_token_encrypted:
            return decrypt_token(self.refresh_token_encrypted)
        return None

    @refresh_token.setter
    def refresh_token(self, value):
        """
        Property setter for the refresh token.
        Encrypts the provided token value before storing it in `refresh_token_encrypted`.

        Args:
            value (str): The plain-text refresh token.
        """
        self.refresh_token_encrypted = encrypt_token(value)
