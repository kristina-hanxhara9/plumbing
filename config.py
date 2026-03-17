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
