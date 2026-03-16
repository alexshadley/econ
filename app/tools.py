import json

from app.engine import GameEngine

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "view_state",
            "description": (
                "View your firm's current state: cash, inventory, factories, running factories, "
                "and the full order book (all open buy/sell orders from all firms)."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "post_buy_order",
            "description": (
                "Post a buy order for a commodity at a specified price per unit. "
                "The total cost (quantity * price) is escrowed from your cash immediately. "
                "If a matching sell order exists at your price or lower, the trade executes instantly. "
                "Otherwise the order stays on the order book until matched or cancelled. "
                "You can cancel to get your escrowed cash back."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "commodity": {
                        "type": "string",
                        "enum": ["metal", "parts"],
                        "description": "The commodity to buy",
                    },
                    "quantity": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Number of units to buy",
                    },
                    "price_per_unit": {
                        "type": "number",
                        "minimum": 0.01,
                        "description": "Maximum price you're willing to pay per unit",
                    },
                },
                "required": ["commodity", "quantity", "price_per_unit"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "post_sell_order",
            "description": (
                "Post a sell order for a commodity at a specified price per unit. "
                "The goods are escrowed from your inventory immediately. "
                "If a matching buy order exists at your price or higher, the trade executes instantly. "
                "Otherwise the order stays on the order book until matched or cancelled. "
                "You can cancel to get your escrowed goods back."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "commodity": {
                        "type": "string",
                        "enum": ["metal", "parts"],
                        "description": "The commodity to sell",
                    },
                    "quantity": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Number of units to sell",
                    },
                    "price_per_unit": {
                        "type": "number",
                        "minimum": 0.01,
                        "description": "Minimum price you're willing to accept per unit",
                    },
                },
                "required": ["commodity", "quantity", "price_per_unit"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_order",
            "description": "Cancel one of your open orders. Escrowed cash or goods are returned to you.",
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "The ID of the order to cancel",
                    },
                },
                "required": ["order_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_factories",
            "description": (
                "Start running factories to convert input commodity into output commodity. "
                "Takes 30 seconds to complete. Input commodity and cash are deducted immediately, "
                "output is delivered when production finishes. "
                "Factory types: metal (ore->metal), part (metal->parts), car (parts->cars)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "factory_type": {
                        "type": "string",
                        "enum": ["metal", "part", "car"],
                        "description": "Type of factory to run",
                    },
                    "count": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Number of factories to run simultaneously",
                    },
                },
                "required": ["factory_type", "count"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buy_ore",
            "description": "Buy ore from the market at $1 per unit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "quantity": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Number of ore units to buy",
                    },
                },
                "required": ["quantity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sell_cars",
            "description": "Sell cars to the market at $10 per unit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "quantity": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Number of cars to sell",
                    },
                },
                "required": ["quantity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buy_factory",
            "description": "Buy new factory units at $10 each. You can buy any type of factory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "factory_type": {
                        "type": "string",
                        "enum": ["metal", "part", "car"],
                        "description": "Type of factory to buy",
                    },
                    "quantity": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Number of factory units to buy",
                    },
                },
                "required": ["factory_type", "quantity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": (
                "Wait for up to the specified number of seconds. "
                "You will be woken up early if something happens that requires your attention "
                "(factory completes, order filled, new order on book). "
                "Use this after starting factories or when waiting for orders to fill."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {
                        "type": "number",
                        "minimum": 1,
                        "maximum": 120,
                        "description": "Maximum seconds to wait",
                    },
                },
                "required": ["seconds"],
            },
        },
    },
]


def _get_tools_for_firm(firm_id: str) -> list[dict]:
    """Return tool definitions (same for all firms — no targeting needed)."""
    return TOOL_DEFINITIONS


async def dispatch_tool_call(
    engine: GameEngine, firm_id: str, tool_name: str, arguments: dict
) -> str:
    match tool_name:
        case "view_state":
            result = await engine.view_state(firm_id)
            return json.dumps(result)
        case "post_buy_order":
            return await engine.post_buy_order(
                firm_id,
                arguments["commodity"],
                arguments["quantity"],
                arguments["price_per_unit"],
            )
        case "post_sell_order":
            return await engine.post_sell_order(
                firm_id,
                arguments["commodity"],
                arguments["quantity"],
                arguments["price_per_unit"],
            )
        case "cancel_order":
            return await engine.cancel_order(firm_id, arguments["order_id"])
        case "start_factories":
            return await engine.start_factories(
                firm_id, arguments["factory_type"], arguments["count"]
            )
        case "buy_ore":
            return await engine.buy_ore(firm_id, arguments["quantity"])
        case "sell_cars":
            return await engine.sell_cars(firm_id, arguments["quantity"])
        case "buy_factory":
            return await engine.buy_factory(
                firm_id, arguments["factory_type"], arguments["quantity"]
            )
        case "wait":
            return await engine.agent_wait(firm_id, arguments["seconds"])
        case _:
            return f"error: unknown tool '{tool_name}'"
