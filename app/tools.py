import json

from app.engine import GameEngine

FIRM_IDS = ["firm_a", "firm_b", "firm_c"]

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "view_state",
            "description": "View your firm's current state: cash, inventory, factories, and running factories.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_messages",
            "description": "View all unread messages sent to you. Messages are marked as read after viewing.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "view_contracts",
            "description": "View all pending contracts involving you (both sent and received).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": "Send a message to another firm. Use thread_id to group related messages into a conversation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "enum": FIRM_IDS,
                        "description": "The firm to send the message to",
                    },
                    "thread_id": {
                        "type": "string",
                        "description": "Thread ID to group related messages. Use a descriptive name like 'metal-trade-negotiation'.",
                    },
                    "content": {
                        "type": "string",
                        "description": "The message content",
                    },
                },
                "required": ["to", "thread_id", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_contract",
            "description": "Send a contract offer to another firm to buy or sell a commodity at a specified price.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "enum": FIRM_IDS,
                        "description": "The firm to send the contract to",
                    },
                    "commodity": {
                        "type": "string",
                        "enum": ["ore", "metal", "parts", "cars"],
                        "description": "The commodity to trade",
                    },
                    "quantity": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Number of units to trade",
                    },
                    "price_per_unit": {
                        "type": "number",
                        "minimum": 0,
                        "description": "Price per unit in dollars",
                    },
                    "side": {
                        "type": "string",
                        "enum": ["buy", "sell"],
                        "description": "Whether YOU want to buy or sell this commodity",
                    },
                },
                "required": ["to", "commodity", "quantity", "price_per_unit", "side"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "accept_contract",
            "description": "Accept a pending contract that was sent to you. The trade executes immediately.",
            "parameters": {
                "type": "object",
                "properties": {
                    "contract_id": {
                        "type": "string",
                        "description": "The ID of the contract to accept",
                    },
                },
                "required": ["contract_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reject_contract",
            "description": "Reject a pending contract that was sent to you.",
            "parameters": {
                "type": "object",
                "properties": {
                    "contract_id": {
                        "type": "string",
                        "description": "The ID of the contract to reject",
                    },
                },
                "required": ["contract_id"],
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
                "(factory completes, message received, contract offer received/accepted/rejected). "
                "Use this after starting factories or when waiting for responses."
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
    """Return tool definitions with the firm's own ID excluded from 'to' enums."""
    other_firms = [fid for fid in FIRM_IDS if fid != firm_id]
    tools = json.loads(json.dumps(TOOL_DEFINITIONS))
    for tool in tools:
        params = tool["function"].get("parameters", {}).get("properties", {})
        if "to" in params and "enum" in params["to"]:
            params["to"]["enum"] = other_firms
    return tools


async def dispatch_tool_call(
    engine: GameEngine, firm_id: str, tool_name: str, arguments: dict
) -> str:
    match tool_name:
        case "view_state":
            result = await engine.view_state(firm_id)
            return json.dumps(result)
        case "view_messages":
            result = await engine.view_messages(firm_id)
            if not result:
                return "no unread messages"
            return json.dumps(result, default=str)
        case "view_contracts":
            result = await engine.view_contracts(firm_id)
            if not result:
                return "no pending contracts"
            return json.dumps(result, default=str)
        case "send_message":
            return await engine.send_message(
                firm_id, arguments["to"], arguments["thread_id"], arguments["content"]
            )
        case "send_contract":
            return await engine.send_contract(
                firm_id,
                arguments["to"],
                arguments["commodity"],
                arguments["quantity"],
                arguments["price_per_unit"],
                arguments["side"],
            )
        case "accept_contract":
            return await engine.accept_contract(firm_id, arguments["contract_id"])
        case "reject_contract":
            return await engine.reject_contract(firm_id, arguments["contract_id"])
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
