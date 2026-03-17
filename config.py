API_BASE_URL = "https://api.company-information.service.gov.uk"
SEARCH_ENDPOINT = "/search/companies"
COMPANY_ENDPOINT = "/company/{company_number}"
ADVANCED_SEARCH_ENDPOINT = "/advanced-search/companies"

RATE_LIMIT_REQUESTS = 600
RATE_LIMIT_WINDOW = 300  # seconds (5 minutes)
REQUEST_DELAY = 0.5  # seconds between API calls

# Excel column indices (0-based)
NAME_COL = 0      # Column A
POSTCODE_COL = 4   # Column E

# SIC code prediction confidence threshold
DEFAULT_CONFIDENCE_THRESHOLD = 0.3

# Plumbing-relevant SIC codes (from frequency analysis of 1066 merchants).
# Only these codes are used for training — all other SIC codes are treated as
# noise and excluded, which dramatically improves model accuracy.
PLUMBING_SIC_CODES = {
    "46740",  # Wholesale of hardware, plumbing and heating equipment (19.3%)
    "43220",  # Plumbing, heat and air-conditioning installation (7.0%)
    "47520",  # Retail sale of hardware, paints and glass (5.9%)
    "46130",  # Agents in sale of timber and building materials (2.3%)
    "46730",  # Wholesale of wood, construction materials and sanitary equipment
    "47540",  # Retail sale of electrical household appliances
    "25210",  # Manufacture of central heating radiators and boilers
    "33200",  # Installation of industrial machinery and equipment
    "43290",  # Other construction installation
    "35300",  # Steam and air conditioning supply
    "36000",  # Water collection, treatment and supply
    "37000",  # Sewerage
}
