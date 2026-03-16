GAME_DURATION_SECONDS = 300  # 5 minutes
FACTORY_PRODUCTION_SECONDS = 30  # 30 seconds per production cycle
ORE_BUY_PRICE = 1.0
CAR_SELL_PRICE = 10.0
FACTORY_BUY_PRICE = 10.0

STARTING_CASH = 100.0
STARTING_FACTORY_COUNT = 10

OPENAI_MODEL = "gpt-5-mini"

# Pricing per token (gpt-5-mini)
INPUT_PRICE_PER_TOKEN = 0.25 / 1_000_000   # $0.25 per 1M input tokens
OUTPUT_PRICE_PER_TOKEN = 2.00 / 1_000_000  # $2.00 per 1M output tokens

FIRM_CONFIGS = [
    {"id": "firm_a", "name": "Firm A", "factory_type": "metal"},
    {"id": "firm_b", "name": "Firm B", "factory_type": "part"},
    {"id": "firm_c", "name": "Firm C", "factory_type": "car"},
]
