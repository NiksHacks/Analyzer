from extensions import db # Import the SQLAlchemy instance from extensions.

class SubscriptionPlan(db.Model):
    """
    Represents a subscription plan offered by the application.

    This model stores details about different subscription tiers, such as their
    name, price, a list of features included, and the corresponding Stripe Price ID
    for payment processing.
    """
    __tablename__ = 'subscription_plans' # Specifies the database table name.

    # --- Plan Identification and Details ---
    id = db.Column(db.Integer, primary_key=True) # Unique identifier for the subscription plan.
    name = db.Column(db.String(100), unique=True, nullable=False) # Name of the subscription plan (e.g., "Basic", "Pro", "Enterprise"). Must be unique.
    price = db.Column(db.Numeric(10, 2), nullable=False) # Monthly or annual price of the plan. Numeric type for precise decimal values (e.g., 10.00).

    # --- Features ---
    # Stores a list of features associated with this plan.
    # Using db.JSON allows for flexible storage of features, typically as a list of strings.
    # Example: ["Feature A", "Feature B", "Up to 5 users"]
    features = db.Column(db.JSON, nullable=True)

    # --- Stripe Integration ---
    # Stores the Stripe Price ID associated with this plan.
    # This ID is used when creating Stripe Checkout sessions or subscriptions.
    # It must be unique as it maps to a specific price object in Stripe.
    # Nullable if a plan is defined in the system but not yet linked to Stripe.
    stripe_price_id = db.Column(db.String(100), unique=True, nullable=True, index=True)

    def __repr__(self):
        """
        Provides a string representation of the SubscriptionPlan object, useful for debugging.
        """
        return f'<SubscriptionPlan {self.name} - {self.price}>'
