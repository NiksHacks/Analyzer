from datetime import datetime
from extensions import db
from .ad_platform_integration import PlatformNameEnum # Import for typing and relationship consistency.

class CampaignData(db.Model):
    """
    Stores campaign performance data fetched from various ad platforms.

    Each record represents metrics for a specific campaign on a given date,
    potentially broken down by various dimensions (e.g., device, country, age, gender).
    A unique constraint ensures that for a given integration, campaign, date, and
    specific breakdown, there's only one entry, preventing data duplication.
    """
    __tablename__ = 'campaign_data' # Specifies the database table name.

    id = db.Column(db.Integer, primary_key=True) # Unique identifier for each data record.

    # --- Foreign Keys and Platform Identification ---
    # Links this data entry to a specific AdPlatformIntegration record.
    integration_id = db.Column(db.Integer, db.ForeignKey('ad_platform_integrations.id'), nullable=False, index=True)
    # Specifies the ad platform this data belongs to (e.g., GoogleAds, MetaAds). Uses PlatformNameEnum.
    platform = db.Column(db.Enum(PlatformNameEnum), nullable=False, index=True)

    # --- Campaign Information ---
    # The campaign ID as provided by the ad platform.
    campaign_id_platform = db.Column(db.String(255), nullable=False, index=True)
    # The name of the campaign as reported by the ad platform. Stored for easier reporting and display.
    # Max length 512 to accommodate potentially long campaign names.
    campaign_name_platform = db.Column(db.String(512), nullable=True)

    # --- Date of Metrics ---
    # The specific date for which these metrics are reported. Stored as a Date object.
    date = db.Column(db.Date, nullable=False, index=True)

    # --- Breakdown Dimensions ---
    # Type of breakdown (e.g., 'overall', 'device', 'country', 'age_range', 'gender').
    # 'overall' indicates that the metrics are totals for the campaign on that day without specific segmentation.
    # Indexed for efficient querying when filtering or grouping by breakdown type.
    breakdown_type = db.Column(db.String(50), nullable=False, default='overall', server_default='overall', index=True)
    # Value of the breakdown (e.g., 'mobile', 'US', '18-24', 'female').
    # 'N/A' (Not Applicable) is used as the default for 'overall' breakdown_type or if a breakdown value is unknown.
    # Indexed for efficient querying.
    breakdown_value = db.Column(db.String(255), nullable=False, default='N/A', server_default='N/A', index=True)

    # --- Core Performance Metrics ---
    # Number of times ads were displayed. Defaults to 0.
    impressions = db.Column(db.Integer, default=0)
    # Number of clicks on ads. Defaults to 0.
    clicks = db.Column(db.Integer, default=0)
    # Amount spent on the campaign/breakdown, stored as a Numeric for precision (e.g., 123.45). Defaults to 0.0.
    spend = db.Column(db.Numeric(10, 2), default=0.0)
    # Number of conversions attributed to the ads. Defaults to 0.
    conversions = db.Column(db.Integer, default=0)

    # --- Timestamps ---
    created_at = db.Column(db.DateTime, default=datetime.utcnow) # Timestamp of when this data record was created in our system.
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow) # Timestamp of the last update to this record.

    # --- Relationships ---
    # Defines a many-to-one relationship with AdPlatformIntegration.
    # 'integration' allows accessing the AdPlatformIntegration object to which this data belongs.
    # 'backref='campaign_data_entries'' adds 'campaign_data_entries' attribute to AdPlatformIntegration instances.
    integration = db.relationship('AdPlatformIntegration', backref=db.backref('campaign_data_entries', lazy='dynamic'))

    # --- Table Arguments: Unique Constraint ---
    # Ensures data integrity by preventing duplicate entries for the same integration,
    # campaign, date, and specific breakdown (type and value).
    # This means, for example, there can only be one record for:
    #   Integration X, Campaign Y, Date Z, BreakdownType 'device', BreakdownValue 'mobile'.
    #   And another for:
    #   Integration X, Campaign Y, Date Z, BreakdownType 'overall', BreakdownValue 'N/A'.
    __table_args__ = (
        db.UniqueConstraint('integration_id', 'campaign_id_platform', 'date',
                            'breakdown_type', 'breakdown_value',
                            name='uq_campaign_daily_breakdown_metric'), # Name for the unique constraint in the database.
    )

    def __repr__(self):
        """
        Provides a string representation of the CampaignData object, useful for debugging.
        """
        return f'<CampaignData {self.platform.value} - Camp: {self.campaign_id_platform} - Date: {self.date} - Breakdown: {self.breakdown_type}:{self.breakdown_value}>'
