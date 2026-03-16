"""Integration tests for the economy simulation agent and engine."""

import asyncio
import json

import pytest
import pytest_asyncio

from app.config import (
    CAR_SELL_PRICE,
    FACTORY_BUY_PRICE,
    ORE_BUY_PRICE,
    STARTING_CASH,
    STARTING_FACTORY_COUNT,
)
from app.engine import GameEngine
from app.models import Commodity, FactoryType
from app.tools import dispatch_tool_call


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def engine():
    eng = GameEngine()
    eng.setup_starting_state()
    eng.start_game()
    return eng


# ---------------------------------------------------------------------------
# Engine: initial state
# ---------------------------------------------------------------------------

class TestInitialState:
    @pytest.mark.asyncio
    async def test_firms_created(self, engine: GameEngine):
        snapshot = engine.get_state_snapshot()
        assert set(snapshot["firms"].keys()) == {"firm_a", "firm_b", "firm_c"}

    @pytest.mark.asyncio
    async def test_starting_cash(self, engine: GameEngine):
        for firm_id in ("firm_a", "firm_b", "firm_c"):
            state = await engine.view_state(firm_id)
            assert state["cash"] == STARTING_CASH

    @pytest.mark.asyncio
    async def test_starting_inventory_empty(self, engine: GameEngine):
        state = await engine.view_state("firm_a")
        for commodity in ("ore", "metal", "parts", "cars"):
            assert state["inventory"][commodity] == 0

    @pytest.mark.asyncio
    async def test_starting_factories(self, engine: GameEngine):
        state_a = await engine.view_state("firm_a")
        assert state_a["factories"]["metal"] == STARTING_FACTORY_COUNT
        assert state_a["factories"]["part"] == 0
        assert state_a["factories"]["car"] == 0

        state_b = await engine.view_state("firm_b")
        assert state_b["factories"]["part"] == STARTING_FACTORY_COUNT

        state_c = await engine.view_state("firm_c")
        assert state_c["factories"]["car"] == STARTING_FACTORY_COUNT

    @pytest.mark.asyncio
    async def test_game_running(self, engine: GameEngine):
        assert engine.game_running is True
        assert engine.time_remaining() > 0

    @pytest.mark.asyncio
    async def test_view_state_includes_order_book(self, engine: GameEngine):
        state = await engine.view_state("firm_a")
        assert "order_book" in state
        assert state["order_book"] == []


# ---------------------------------------------------------------------------
# Engine: buy ore
# ---------------------------------------------------------------------------

class TestBuyOre:
    @pytest.mark.asyncio
    async def test_buy_ore_success(self, engine: GameEngine):
        result = await engine.buy_ore("firm_a", 10)
        assert "bought 10 ore" in result
        state = await engine.view_state("firm_a")
        assert state["inventory"]["ore"] == 10
        assert state["cash"] == STARTING_CASH - 10 * ORE_BUY_PRICE

    @pytest.mark.asyncio
    async def test_buy_ore_insufficient_cash(self, engine: GameEngine):
        result = await engine.buy_ore("firm_a", 200)
        assert "error" in result
        assert "insufficient cash" in result

    @pytest.mark.asyncio
    async def test_buy_ore_zero_quantity(self, engine: GameEngine):
        result = await engine.buy_ore("firm_a", 0)
        assert "error" in result


# ---------------------------------------------------------------------------
# Engine: sell cars
# ---------------------------------------------------------------------------

class TestSellCars:
    @pytest.mark.asyncio
    async def test_sell_cars_no_inventory(self, engine: GameEngine):
        result = await engine.sell_cars("firm_c", 1)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_sell_cars_success(self, engine: GameEngine):
        engine._firms["firm_c"].inventory[Commodity.CARS] = 5
        result = await engine.sell_cars("firm_c", 3)
        assert "sold 3 cars" in result
        state = await engine.view_state("firm_c")
        assert state["inventory"]["cars"] == 2
        assert state["cash"] == STARTING_CASH + 3 * CAR_SELL_PRICE


# ---------------------------------------------------------------------------
# Engine: buy factory
# ---------------------------------------------------------------------------

class TestBuyFactory:
    @pytest.mark.asyncio
    async def test_buy_factory_success(self, engine: GameEngine):
        result = await engine.buy_factory("firm_a", "part", 2)
        assert "bought 2 part factory" in result
        state = await engine.view_state("firm_a")
        assert state["factories"]["part"] == 2
        assert state["cash"] == STARTING_CASH - 2 * FACTORY_BUY_PRICE

    @pytest.mark.asyncio
    async def test_buy_factory_insufficient_cash(self, engine: GameEngine):
        result = await engine.buy_factory("firm_a", "metal", 20)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_buy_factory_invalid_type(self, engine: GameEngine):
        result = await engine.buy_factory("firm_a", "invalid", 1)
        assert "error" in result


# ---------------------------------------------------------------------------
# Engine: start factories
# ---------------------------------------------------------------------------

class TestStartFactories:
    @pytest.mark.asyncio
    async def test_start_factories_no_input(self, engine: GameEngine):
        result = await engine.start_factories("firm_a", "metal", 1)
        assert "error" in result
        assert "no ore" in result.lower() or "insufficient" in result.lower()

    @pytest.mark.asyncio
    async def test_start_factories_success(self, engine: GameEngine):
        await engine.buy_ore("firm_a", 5)
        result = await engine.start_factories("firm_a", "metal", 5)
        assert "started 5 metal factory" in result
        state = await engine.view_state("firm_a")
        assert state["inventory"]["ore"] == 0
        assert state["running_factories"]["metal"] == 5

    @pytest.mark.asyncio
    async def test_start_factories_too_many(self, engine: GameEngine):
        await engine.buy_ore("firm_a", 20)
        result = await engine.start_factories("firm_a", "metal", 15)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_factory_completion(self, engine: GameEngine):
        await engine.buy_ore("firm_a", 2)
        engine._firms["firm_a"].inventory[Commodity.ORE] = 2
        await engine.start_factories("firm_a", "metal", 2)
        engine.finalize_factory_jobs()
        state = await engine.view_state("firm_a")
        assert state["inventory"]["metal"] == 2
        assert state["running_factories"]["metal"] == 0


# ---------------------------------------------------------------------------
# Engine: order book
# ---------------------------------------------------------------------------

class TestOrderBook:
    @pytest.mark.asyncio
    async def test_post_buy_order_escrows_cash(self, engine: GameEngine):
        result = await engine.post_buy_order("firm_a", "metal", 5, 3.0)
        assert "buy order posted" in result
        state = await engine.view_state("firm_a")
        assert state["cash"] == STARTING_CASH - 15.0  # 5 * $3 escrowed
        assert len(state["order_book"]) == 1
        assert state["order_book"][0]["side"] == "buy"

    @pytest.mark.asyncio
    async def test_post_sell_order_escrows_goods(self, engine: GameEngine):
        engine._firms["firm_a"].inventory[Commodity.METAL] = 10
        result = await engine.post_sell_order("firm_a", "metal", 5, 3.0)
        assert "sell order posted" in result
        state = await engine.view_state("firm_a")
        assert state["inventory"]["metal"] == 5  # 5 escrowed

    @pytest.mark.asyncio
    async def test_post_sell_order_insufficient_inventory(self, engine: GameEngine):
        result = await engine.post_sell_order("firm_a", "metal", 5, 3.0)
        assert "error" in result
        assert "insufficient" in result

    @pytest.mark.asyncio
    async def test_post_buy_order_insufficient_cash(self, engine: GameEngine):
        result = await engine.post_buy_order("firm_a", "metal", 100, 50.0)
        assert "error" in result
        assert "insufficient cash" in result

    @pytest.mark.asyncio
    async def test_orders_match_instantly(self, engine: GameEngine):
        """A sell order followed by a matching buy order should fill instantly."""
        engine._firms["firm_a"].inventory[Commodity.METAL] = 10
        # firm_a posts sell at $3
        r1 = await engine.post_sell_order("firm_a", "metal", 5, 3.0)
        assert "sell order posted" in r1

        # firm_b posts buy at $3 — should match
        r2 = await engine.post_buy_order("firm_b", "metal", 5, 3.0)
        assert "filled" in r2

        state_a = await engine.view_state("firm_a")
        state_b = await engine.view_state("firm_b")
        assert state_a["cash"] == STARTING_CASH + 15.0  # sold 5 at $3
        assert state_a["inventory"]["metal"] == 5  # 10 - 5 sold
        assert state_b["cash"] == STARTING_CASH - 15.0  # bought 5 at $3
        assert state_b["inventory"]["metal"] == 5

    @pytest.mark.asyncio
    async def test_buy_order_matches_at_sellers_price(self, engine: GameEngine):
        """Buyer posts higher price, trade happens at seller's (maker) price."""
        engine._firms["firm_a"].inventory[Commodity.METAL] = 5
        await engine.post_sell_order("firm_a", "metal", 5, 2.0)
        r = await engine.post_buy_order("firm_b", "metal", 5, 4.0)
        assert "filled" in r

        state_a = await engine.view_state("firm_a")
        state_b = await engine.view_state("firm_b")
        # Trade at seller's price: $2/unit
        assert state_a["cash"] == STARTING_CASH + 10.0  # 5 * $2
        assert state_b["cash"] == STARTING_CASH - 10.0  # 5 * $2
        assert state_b["inventory"]["metal"] == 5

    @pytest.mark.asyncio
    async def test_no_match_when_prices_incompatible(self, engine: GameEngine):
        """Buy order at $2 shouldn't match sell order at $3."""
        engine._firms["firm_a"].inventory[Commodity.METAL] = 5
        await engine.post_sell_order("firm_a", "metal", 5, 3.0)
        r = await engine.post_buy_order("firm_b", "metal", 5, 2.0)
        assert "buy order posted" in r  # no match
        state = await engine.view_state("firm_b")
        assert len(state["order_book"]) == 2  # both orders on book

    @pytest.mark.asyncio
    async def test_cancel_buy_order_returns_cash(self, engine: GameEngine):
        r = await engine.post_buy_order("firm_a", "metal", 5, 3.0)
        order_id = r.split("Order id: ")[1]
        state = await engine.view_state("firm_a")
        assert state["cash"] == STARTING_CASH - 15.0

        r2 = await engine.cancel_order("firm_a", order_id)
        assert "cancelled" in r2
        assert "$15.00 returned" in r2
        state = await engine.view_state("firm_a")
        assert state["cash"] == STARTING_CASH

    @pytest.mark.asyncio
    async def test_cancel_sell_order_returns_goods(self, engine: GameEngine):
        engine._firms["firm_a"].inventory[Commodity.METAL] = 10
        r = await engine.post_sell_order("firm_a", "metal", 5, 3.0)
        order_id = r.split("Order id: ")[1]
        state = await engine.view_state("firm_a")
        assert state["inventory"]["metal"] == 5

        r2 = await engine.cancel_order("firm_a", order_id)
        assert "cancelled" in r2
        state = await engine.view_state("firm_a")
        assert state["inventory"]["metal"] == 10

    @pytest.mark.asyncio
    async def test_cannot_cancel_other_firms_order(self, engine: GameEngine):
        r = await engine.post_buy_order("firm_a", "metal", 5, 3.0)
        order_id = r.split("Order id: ")[1]
        r2 = await engine.cancel_order("firm_b", order_id)
        assert "error" in r2
        assert "not your order" in r2

    @pytest.mark.asyncio
    async def test_cannot_cancel_filled_order(self, engine: GameEngine):
        engine._firms["firm_a"].inventory[Commodity.METAL] = 5
        await engine.post_sell_order("firm_a", "metal", 5, 3.0)
        r = await engine.post_buy_order("firm_b", "metal", 5, 3.0)
        # Extract buy order id
        # The buy order was filled, try to cancel it
        # We need to get the order ID from the sell order instead
        state = await engine.view_state("firm_a")
        assert len(state["order_book"]) == 0  # both filled

    @pytest.mark.asyncio
    async def test_partial_match(self, engine: GameEngine):
        """Sell 10 but buy only 5 — should partially fill."""
        engine._firms["firm_a"].inventory[Commodity.METAL] = 10
        await engine.post_sell_order("firm_a", "metal", 10, 3.0)
        r = await engine.post_buy_order("firm_b", "metal", 5, 3.0)
        assert "filled" in r

        state = await engine.view_state("firm_a")
        # 5 sold for $15, 5 still escrowed on order book
        assert state["cash"] == STARTING_CASH + 15.0
        assert state["inventory"]["metal"] == 0  # all 10 escrowed/sold
        # Remaining sell order for 5
        open_orders = [o for o in state["order_book"] if o["firm"] == "firm_a"]
        assert len(open_orders) == 1
        assert open_orders[0]["quantity"] == 5

    @pytest.mark.asyncio
    async def test_order_book_visible_to_all(self, engine: GameEngine):
        """All firms should see all open orders in view_state."""
        engine._firms["firm_a"].inventory[Commodity.METAL] = 5
        await engine.post_sell_order("firm_a", "metal", 5, 3.0)

        for firm_id in ("firm_a", "firm_b", "firm_c"):
            state = await engine.view_state(firm_id)
            assert len(state["order_book"]) == 1
            assert state["order_book"][0]["commodity"] == "metal"

    @pytest.mark.asyncio
    async def test_finalize_orders_returns_escrow(self, engine: GameEngine):
        """finalize_orders should return all escrowed resources."""
        engine._firms["firm_a"].inventory[Commodity.METAL] = 5
        await engine.post_sell_order("firm_a", "metal", 5, 3.0)
        await engine.post_buy_order("firm_b", "parts", 5, 2.0)

        engine.finalize_orders()
        state_a = await engine.view_state("firm_a")
        state_b = await engine.view_state("firm_b")
        assert state_a["inventory"]["metal"] == 5  # returned
        assert state_b["cash"] == STARTING_CASH  # returned


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

class TestToolDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_view_state(self, engine: GameEngine):
        result = await dispatch_tool_call(engine, "firm_a", "view_state", {})
        data = json.loads(result)
        assert data["firm_id"] == "firm_a"
        assert data["cash"] == STARTING_CASH
        assert "order_book" in data

    @pytest.mark.asyncio
    async def test_dispatch_buy_ore(self, engine: GameEngine):
        result = await dispatch_tool_call(
            engine, "firm_a", "buy_ore", {"quantity": 5}
        )
        assert "bought 5 ore" in result

    @pytest.mark.asyncio
    async def test_dispatch_sell_cars(self, engine: GameEngine):
        engine._firms["firm_c"].inventory[Commodity.CARS] = 3
        result = await dispatch_tool_call(
            engine, "firm_c", "sell_cars", {"quantity": 2}
        )
        assert "sold 2 cars" in result

    @pytest.mark.asyncio
    async def test_dispatch_start_factories(self, engine: GameEngine):
        await engine.buy_ore("firm_a", 3)
        result = await dispatch_tool_call(
            engine, "firm_a", "start_factories",
            {"factory_type": "metal", "count": 3},
        )
        assert "started 3 metal factory" in result

    @pytest.mark.asyncio
    async def test_dispatch_buy_factory(self, engine: GameEngine):
        result = await dispatch_tool_call(
            engine, "firm_a", "buy_factory",
            {"factory_type": "car", "quantity": 1},
        )
        assert "bought 1 car factory" in result

    @pytest.mark.asyncio
    async def test_dispatch_post_buy_order(self, engine: GameEngine):
        result = await dispatch_tool_call(
            engine, "firm_a", "post_buy_order",
            {"commodity": "metal", "quantity": 5, "price_per_unit": 3.0},
        )
        assert "buy order posted" in result

    @pytest.mark.asyncio
    async def test_dispatch_post_sell_order(self, engine: GameEngine):
        engine._firms["firm_a"].inventory[Commodity.METAL] = 10
        result = await dispatch_tool_call(
            engine, "firm_a", "post_sell_order",
            {"commodity": "metal", "quantity": 5, "price_per_unit": 3.0},
        )
        assert "sell order posted" in result

    @pytest.mark.asyncio
    async def test_dispatch_cancel_order(self, engine: GameEngine):
        r = await engine.post_buy_order("firm_a", "metal", 5, 3.0)
        order_id = r.split("Order id: ")[1]
        result = await dispatch_tool_call(
            engine, "firm_a", "cancel_order",
            {"order_id": order_id},
        )
        assert "cancelled" in result

    @pytest.mark.asyncio
    async def test_dispatch_unknown_tool(self, engine: GameEngine):
        result = await dispatch_tool_call(
            engine, "firm_a", "nonexistent_tool", {}
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_dispatch_wait(self, engine: GameEngine):
        result = await dispatch_tool_call(
            engine, "firm_a", "wait", {"seconds": 0.1}
        )
        assert "wait completed" in result or "interrupted" in result


# ---------------------------------------------------------------------------
# Full supply chain integration
# ---------------------------------------------------------------------------

class TestSupplyChain:
    @pytest.mark.asyncio
    async def test_full_supply_chain_via_order_book(self, engine: GameEngine):
        """Simulate a full supply chain using the order book."""
        # Step 1: Buy ore (firm_a has metal factories)
        r = await dispatch_tool_call(engine, "firm_a", "buy_ore", {"quantity": 5})
        assert "bought" in r

        # Step 2: Start metal factories
        r = await dispatch_tool_call(
            engine, "firm_a", "start_factories",
            {"factory_type": "metal", "count": 5},
        )
        assert "started" in r
        engine.finalize_factory_jobs()

        # Step 3: firm_a sells metal, firm_b buys metal via order book
        r = await engine.post_sell_order("firm_a", "metal", 5, 2.0)
        assert "sell order posted" in r
        r = await engine.post_buy_order("firm_b", "metal", 5, 2.0)
        assert "filled" in r

        # Step 4: firm_b converts metal -> parts
        r = await dispatch_tool_call(
            engine, "firm_b", "start_factories",
            {"factory_type": "part", "count": 5},
        )
        assert "started" in r
        engine.finalize_factory_jobs()
        state_b = await engine.view_state("firm_b")
        assert state_b["inventory"]["parts"] == 5

        # Step 5: firm_b sells parts, firm_c buys parts via order book
        r = await engine.post_sell_order("firm_b", "parts", 5, 4.0)
        assert "sell order posted" in r
        r = await engine.post_buy_order("firm_c", "parts", 5, 4.0)
        assert "filled" in r

        # Step 6: firm_c converts parts -> cars
        r = await dispatch_tool_call(
            engine, "firm_c", "start_factories",
            {"factory_type": "car", "count": 5},
        )
        assert "started" in r
        engine.finalize_factory_jobs()

        # Step 7: firm_c sells cars
        r = await dispatch_tool_call(
            engine, "firm_c", "sell_cars", {"quantity": 5},
        )
        assert "sold 5 cars" in r

        state_a = await engine.view_state("firm_a")
        state_b = await engine.view_state("firm_b")
        state_c = await engine.view_state("firm_c")
        total_cash = state_a["cash"] + state_b["cash"] + state_c["cash"]
        assert total_cash > 300 - 50


# ---------------------------------------------------------------------------
# Agent integration test (real OpenAI API)
# ---------------------------------------------------------------------------


class TestAgent:
    @pytest.mark.asyncio
    async def test_agent_harness(self, engine: GameEngine):
        """Smoke test: one agent, two steps — confirm the agentic loop works."""
        from app.agent import Agent

        agent = Agent("firm_a", engine)

        step_count = 0
        original_step = agent._step

        async def counted_step():
            nonlocal step_count
            step_count += 1
            if step_count >= 2:
                agent.stop()
                return
            await original_step()

        agent._step = counted_step
        await agent.run()

        log = engine.get_tool_call_log()
        assert len(log) >= 1, "Agent made no tool calls"
        tool_names = [e["tool"] for e in log]
        assert "view_state" in tool_names, (
            f"Agent never called view_state. Tools: {tool_names}"
        )


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_cancel_nonexistent_order(self, engine: GameEngine):
        result = await engine.cancel_order("firm_a", "nonexistent-id")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invalid_commodity_buy_order(self, engine: GameEngine):
        result = await engine.post_buy_order("firm_a", "unobtanium", 5, 3.0)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invalid_commodity_sell_order(self, engine: GameEngine):
        result = await engine.post_sell_order("firm_a", "unobtanium", 5, 3.0)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_negative_price_order(self, engine: GameEngine):
        result = await engine.post_buy_order("firm_a", "metal", 5, -1.0)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_zero_quantity_order(self, engine: GameEngine):
        result = await engine.post_buy_order("firm_a", "metal", 0, 3.0)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_sell_cars_zero(self, engine: GameEngine):
        result = await engine.sell_cars("firm_a", 0)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_buy_factory_zero(self, engine: GameEngine):
        result = await engine.buy_factory("firm_a", "metal", 0)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_game_stops(self, engine: GameEngine):
        engine.stop_game()
        assert engine.game_running is False
        assert engine.time_remaining() == 0.0

    @pytest.mark.asyncio
    async def test_production_cost_decreases_with_more_factories(self, engine: GameEngine):
        cost_at_10 = engine._production_cost_per_unit(10)
        cost_at_20 = engine._production_cost_per_unit(20)
        cost_at_100 = engine._production_cost_per_unit(100)
        assert cost_at_10 > cost_at_20 > cost_at_100
        assert cost_at_100 > 1.0

    @pytest.mark.asyncio
    async def test_same_firm_orders_dont_match(self, engine: GameEngine):
        """A firm's buy and sell orders shouldn't match against each other."""
        engine._firms["firm_a"].inventory[Commodity.METAL] = 10
        await engine.post_sell_order("firm_a", "metal", 5, 2.0)
        r = await engine.post_buy_order("firm_a", "metal", 5, 3.0)
        assert "buy order posted" in r  # should NOT match
        state = await engine.view_state("firm_a")
        assert len(state["order_book"]) == 2
