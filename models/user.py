from datetime import datetime
from extensions import db
import bcrypt
from flask_login import UserMixin # For Flask-Login integration (e.g., current_user).

class User(db.Model, UserMixin):
    """
    Represents a user in the application.

    This model stores user authentication details (email, password), personal information,
    OAuth provider IDs (Google, Meta), Stripe customer ID for billing, and relationships
    to their subscriptions. It includes methods for password management and checking
    active subscription status. UserMixin provides default implementations for methods
    required by Flask-Login (e.g., is_authenticated, get_id).
    """
    __tablename__ = 'users' # Specifies the database table name.

    # --- Basic User Information ---
    id = db.Column(db.Integer, primary_key=True) # Unique identifier for the user.
    email = db.Column(db.String(120), unique=True, nullable=False, index=True) # User's email address, used for login. Must be unique.
    password_hash = db.Column(db.String(128), nullable=True) # Hashed password for users registered via email/password. Nullable for OAuth-only users.
    full_name = db.Column(db.String(100), nullable=True) # User's full name.

    # --- OAuth Provider Identifiers ---
    # Store unique IDs from OAuth providers if the user signs up or links their account using Google or Meta.
    google_id = db.Column(db.String(120), unique=True, nullable=True, index=True) # Google User ID.
    meta_id = db.Column(db.String(120), unique=True, nullable=True, index=True)   # Meta (Facebook) User ID.

    # --- Timestamps ---
    created_at = db.Column(db.DateTime, default=datetime.utcnow) # Timestamp of when the user record was created.
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow) # Timestamp of the last update to the user record.

    # --- Billing Information ---
    # Stores the Stripe Customer ID, linking this user to a customer object in Stripe for managing subscriptions and payments.
    stripe_customer_id = db.Column(db.String(120), unique=True, nullable=True, index=True)

    # --- Relationships ---
    # Defines a one-to-many relationship with UserSubscription model.
    # 'subscriptions' allows accessing all subscriptions for a user (e.g., user.subscriptions.all()).
    # 'backref='user'' adds a 'user' attribute to UserSubscription instances, linking back to this User.
    # 'lazy='dynamic'' means the subscriptions are loaded as a query, not immediately when the User object is loaded.
    subscriptions = db.relationship('UserSubscription', backref='user', lazy='dynamic')

    def set_password(self, password):
        """
        Hashes the provided password and stores it in `password_hash`.

        Args:
            password (str): The plain-text password to hash.
        """
        # Use bcrypt to generate a secure hash of the password.
        # Password is encoded to UTF-8 before hashing. Salt is generated automatically by bcrypt.
        # The resulting hash is decoded back to UTF-8 for storage in the database.
        self.password_hash = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    def check_password(self, password):
        """
        Verifies if the provided password matches the stored hashed password.

        Args:
            password (str): The plain-text password to check.

        Returns:
            bool: True if the password matches, False otherwise.
                  Returns False if no password_hash is set (e.g., for OAuth-only users).
        """
        if self.password_hash:
            # Use bcrypt to compare the provided password with the stored hash.
            # Both need to be encoded to UTF-8 for bcrypt.checkpw.
            return bcrypt.checkpw(password.encode('utf-8'), self.password_hash.encode('utf-8'))
        return False # No password hash stored, so password check fails.

    def has_active_subscription(self, plan_names=None):
        """
        Checks if the user currently has an active subscription.
        Optionally, it can check if the active subscription's plan name
        is one of the specified plan_names.

        Args:
            plan_names (list of str, optional): A list of subscription plan names to check against.
                                                If None, checks for any active plan.
                                                Defaults to None.
        Returns:
            bool: True if the user has an active subscription (matching plan_names if provided),
                  False otherwise.
        """
        # Local imports to avoid circular dependency issues at module load time,
        # as UserSubscription and SubscriptionPlan might also import User or related enums.
        from .user_subscription import UserSubscription, SubscriptionStatusEnum
        from .subscription_plan import SubscriptionPlan

        # Start a query for UserSubscription records belonging to this user and having an ACTIVE status.
        query = UserSubscription.query.filter_by(user_id=self.id, status=SubscriptionStatusEnum.ACTIVE)

        # If specific plan_names are provided, join with SubscriptionPlan and filter by plan name.
        if plan_names:
            query = query.join(SubscriptionPlan).filter(SubscriptionPlan.name.in_(plan_names))

        # Return True if at least one such subscription exists, False otherwise.
        # .first() is an efficient way to check for existence without loading all records.
        return query.first() is not None

    def __repr__(self):
        """
        Provides a string representation of the User object, useful for debugging.
        """
        return f'<User {self.email}>'
