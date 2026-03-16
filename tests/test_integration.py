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
        # Give firm_c some cars directly for testing
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
        # firm_a has metal factories but no ore
        result = await engine.start_factories("firm_a", "metal", 1)
        assert "error" in result
        assert "no ore" in result.lower() or "insufficient" in result.lower()

    @pytest.mark.asyncio
    async def test_start_factories_success(self, engine: GameEngine):
        # Buy ore first, then start metal factory
        await engine.buy_ore("firm_a", 5)
        result = await engine.start_factories("firm_a", "metal", 5)
        assert "started 5 metal factory" in result
        state = await engine.view_state("firm_a")
        assert state["inventory"]["ore"] == 0  # consumed
        assert state["running_factories"]["metal"] == 5

    @pytest.mark.asyncio
    async def test_start_factories_too_many(self, engine: GameEngine):
        await engine.buy_ore("firm_a", 20)
        result = await engine.start_factories("firm_a", "metal", 15)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_factory_completion(self, engine: GameEngine):
        """Factories should produce output after completion."""
        await engine.buy_ore("firm_a", 2)
        # Patch time so completion is instant
        engine._firms["firm_a"].inventory[Commodity.ORE] = 2
        await engine.start_factories("firm_a", "metal", 2)
        # Wait for the factory job to complete (they're scheduled as async tasks)
        # The jobs complete after FACTORY_PRODUCTION_SECONDS, but we can finalize
        engine.finalize_factory_jobs()
        state = await engine.view_state("firm_a")
        assert state["inventory"]["metal"] == 2
        assert state["running_factories"]["metal"] == 0


# ---------------------------------------------------------------------------
# Engine: contracts
# ---------------------------------------------------------------------------

class TestContracts:
    @pytest.mark.asyncio
    async def test_send_contract(self, engine: GameEngine):
        result = await engine.send_contract(
            "firm_a", "firm_b", "metal", 5, 3.0, "sell"
        )
        assert "contract sent" in result

    @pytest.mark.asyncio
    async def test_send_contract_to_self(self, engine: GameEngine):
        result = await engine.send_contract(
            "firm_a", "firm_a", "metal", 5, 3.0, "sell"
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_accept_contract(self, engine: GameEngine):
        # firm_a sells 5 metal to firm_b at $3/unit
        engine._firms["firm_a"].inventory[Commodity.METAL] = 10
        result = await engine.send_contract(
            "firm_a", "firm_b", "metal", 5, 3.0, "sell"
        )
        # Extract contract ID from result
        contract_id = result.split("id: ")[1].rstrip(")")
        result = await engine.accept_contract("firm_b", contract_id)
        assert "accepted" in result

        state_a = await engine.view_state("firm_a")
        state_b = await engine.view_state("firm_b")
        # firm_a sold 5 metal, gained $15
        assert state_a["inventory"]["metal"] == 5
        assert state_a["cash"] == STARTING_CASH + 15.0
        # firm_b bought 5 metal, lost $15
        assert state_b["inventory"]["metal"] == 5
        assert state_b["cash"] == STARTING_CASH - 15.0

    @pytest.mark.asyncio
    async def test_reject_contract(self, engine: GameEngine):
        engine._firms["firm_a"].inventory[Commodity.METAL] = 10
        result = await engine.send_contract(
            "firm_a", "firm_b", "metal", 5, 3.0, "sell"
        )
        contract_id = result.split("id: ")[1].rstrip(")")
        result = await engine.reject_contract("firm_b", contract_id)
        assert "rejected" in result

    @pytest.mark.asyncio
    async def test_cannot_accept_own_contract(self, engine: GameEngine):
        engine._firms["firm_a"].inventory[Commodity.METAL] = 10
        result = await engine.send_contract(
            "firm_a", "firm_b", "metal", 5, 3.0, "sell"
        )
        contract_id = result.split("id: ")[1].rstrip(")")
        result = await engine.accept_contract("firm_a", contract_id)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_view_contracts(self, engine: GameEngine):
        await engine.send_contract("firm_a", "firm_b", "metal", 5, 3.0, "sell")
        contracts = await engine.view_contracts("firm_b")
        assert len(contracts) == 1
        assert contracts[0]["commodity"] == "metal"

    @pytest.mark.asyncio
    async def test_auto_resolve_contracts(self, engine: GameEngine):
        """Two opposite contracts should auto-resolve."""
        engine._firms["firm_a"].inventory[Commodity.METAL] = 10
        # firm_a sends sell contract to firm_b
        r1 = await engine.send_contract(
            "firm_a", "firm_b", "metal", 5, 2.0, "sell"
        )
        assert "contract sent" in r1
        # firm_b sends buy contract to firm_a for same commodity/quantity
        r2 = await engine.send_contract(
            "firm_b", "firm_a", "metal", 5, 4.0, "buy"
        )
        assert "auto-matched" in r2

        state_a = await engine.view_state("firm_a")
        state_b = await engine.view_state("firm_b")
        # Trade at average price: (2+4)/2 = $3/unit, 5 units = $15
        assert state_a["inventory"]["metal"] == 5
        assert state_b["inventory"]["metal"] == 5
        assert state_a["cash"] == pytest.approx(STARTING_CASH + 15.0)
        assert state_b["cash"] == pytest.approx(STARTING_CASH - 15.0)


# ---------------------------------------------------------------------------
# Engine: messages
# ---------------------------------------------------------------------------

class TestMessages:
    @pytest.mark.asyncio
    async def test_send_and_view_messages(self, engine: GameEngine):
        result = await engine.send_message(
            "firm_a", "firm_b", "trade-talk", "Want to trade metal?"
        )
        assert "message sent" in result

        msgs = await engine.view_messages("firm_b")
        assert len(msgs) == 1
        assert msgs[0]["content"] == "Want to trade metal?"
        assert msgs[0]["from"] == "firm_a"

        # Reading again should return empty (marked as read)
        msgs2 = await engine.view_messages("firm_b")
        assert len(msgs2) == 0

    @pytest.mark.asyncio
    async def test_send_message_to_self(self, engine: GameEngine):
        result = await engine.send_message("firm_a", "firm_a", "t", "hi")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_send_message_unknown_firm(self, engine: GameEngine):
        result = await engine.send_message("firm_a", "firm_z", "t", "hi")
        assert "error" in result


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
    async def test_dispatch_send_message(self, engine: GameEngine):
        result = await dispatch_tool_call(
            engine, "firm_a", "send_message",
            {"to": "firm_b", "thread_id": "t1", "content": "hello"},
        )
        assert "message sent" in result

    @pytest.mark.asyncio
    async def test_dispatch_view_messages(self, engine: GameEngine):
        result = await dispatch_tool_call(
            engine, "firm_a", "view_messages", {}
        )
        assert result == "no unread messages"

    @pytest.mark.asyncio
    async def test_dispatch_send_contract(self, engine: GameEngine):
        result = await dispatch_tool_call(
            engine, "firm_a", "send_contract",
            {"to": "firm_b", "commodity": "metal", "quantity": 5,
             "price_per_unit": 3.0, "side": "sell"},
        )
        assert "contract sent" in result

    @pytest.mark.asyncio
    async def test_dispatch_view_contracts(self, engine: GameEngine):
        result = await dispatch_tool_call(
            engine, "firm_a", "view_contracts", {}
        )
        assert result == "no pending contracts"

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
    async def test_full_supply_chain_via_tools(self, engine: GameEngine):
        """Simulate a full supply chain: buy ore -> metal -> parts -> cars -> sell."""
        # Step 1: Buy ore (firm_a has metal factories)
        r = await dispatch_tool_call(engine, "firm_a", "buy_ore", {"quantity": 5})
        assert "bought" in r

        # Step 2: Start metal factories
        r = await dispatch_tool_call(
            engine, "firm_a", "start_factories",
            {"factory_type": "metal", "count": 5},
        )
        assert "started" in r

        # Finalize to complete production instantly
        engine.finalize_factory_jobs()
        state = await engine.view_state("firm_a")
        assert state["inventory"]["metal"] == 5

        # Step 3: Transfer metal to firm_b (has part factories) via contract
        r = await dispatch_tool_call(
            engine, "firm_a", "send_contract",
            {"to": "firm_b", "commodity": "metal", "quantity": 5,
             "price_per_unit": 2.0, "side": "sell"},
        )
        contract_id = r.split("id: ")[1].rstrip(")")

        r = await dispatch_tool_call(
            engine, "firm_b", "accept_contract",
            {"contract_id": contract_id},
        )
        assert "accepted" in r

        # Step 4: firm_b converts metal -> parts
        r = await dispatch_tool_call(
            engine, "firm_b", "start_factories",
            {"factory_type": "part", "count": 5},
        )
        assert "started" in r
        engine.finalize_factory_jobs()
        state_b = await engine.view_state("firm_b")
        assert state_b["inventory"]["parts"] == 5

        # Step 5: Transfer parts to firm_c (has car factories) via contract
        r = await dispatch_tool_call(
            engine, "firm_b", "send_contract",
            {"to": "firm_c", "commodity": "parts", "quantity": 5,
             "price_per_unit": 4.0, "side": "sell"},
        )
        contract_id = r.split("id: ")[1].rstrip(")")

        r = await dispatch_tool_call(
            engine, "firm_c", "accept_contract",
            {"contract_id": contract_id},
        )
        assert "accepted" in r

        # Step 6: firm_c converts parts -> cars
        r = await dispatch_tool_call(
            engine, "firm_c", "start_factories",
            {"factory_type": "car", "count": 5},
        )
        assert "started" in r
        engine.finalize_factory_jobs()
        state_c = await engine.view_state("firm_c")
        assert state_c["inventory"]["cars"] == 5

        # Step 7: firm_c sells cars
        r = await dispatch_tool_call(
            engine, "firm_c", "sell_cars", {"quantity": 5},
        )
        assert "sold 5 cars" in r

        # Verify final cash positions make sense
        # firm_a: 100 - 5 (ore) - production_cost + 10 (sold metal)
        # firm_b: 100 - 10 (bought metal) - production_cost + 20 (sold parts)
        # firm_c: 100 - 20 (bought parts) - production_cost + 50 (sold cars)
        state_a = await engine.view_state("firm_a")
        state_b = await engine.view_state("firm_b")
        state_c = await engine.view_state("firm_c")
        # All firms should have made some money relative to costs
        total_cash = state_a["cash"] + state_b["cash"] + state_c["cash"]
        # Total money injected: 5 cars * $10 = $50 revenue, 5 ore * $1 = $5 cost
        # Net injection from market = $45 minus production costs
        # Starting total = $300
        # System should have net gained from car sales minus ore/production costs
        assert total_cash > 300 - 50  # conservative lower bound


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
    async def test_accept_nonexistent_contract(self, engine: GameEngine):
        result = await engine.accept_contract("firm_a", "nonexistent-id")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_reject_nonexistent_contract(self, engine: GameEngine):
        result = await engine.reject_contract("firm_a", "nonexistent-id")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_accept_already_accepted_contract(self, engine: GameEngine):
        engine._firms["firm_a"].inventory[Commodity.METAL] = 10
        r = await engine.send_contract("firm_a", "firm_b", "metal", 5, 2.0, "sell")
        contract_id = r.split("id: ")[1].rstrip(")")
        await engine.accept_contract("firm_b", contract_id)
        result = await engine.accept_contract("firm_b", contract_id)
        assert "error" in result
        assert "already" in result

    @pytest.mark.asyncio
    async def test_invalid_commodity(self, engine: GameEngine):
        result = await engine.send_contract(
            "firm_a", "firm_b", "unobtanium", 5, 3.0, "sell"
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_invalid_side(self, engine: GameEngine):
        result = await engine.send_contract(
            "firm_a", "firm_b", "metal", 5, 3.0, "barter"
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_negative_price_contract(self, engine: GameEngine):
        result = await engine.send_contract(
            "firm_a", "firm_b", "metal", 5, -1.0, "sell"
        )
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
        # All costs should be > 1 (base cost)
        assert cost_at_100 > 1.0

    @pytest.mark.asyncio
    async def test_contract_seller_insufficient_inventory(self, engine: GameEngine):
        """Accept should fail if seller doesn't have enough inventory."""
        # firm_a tries to sell metal but has none
        r = await engine.send_contract("firm_a", "firm_b", "metal", 5, 2.0, "sell")
        contract_id = r.split("id: ")[1].rstrip(")")
        result = await engine.accept_contract("firm_b", contract_id)
        assert "error" in result
        assert "insufficient" in result

    @pytest.mark.asyncio
    async def test_contract_buyer_insufficient_cash(self, engine: GameEngine):
        """Accept should fail if buyer can't afford it."""
        engine._firms["firm_a"].inventory[Commodity.METAL] = 100
        r = await engine.send_contract("firm_a", "firm_b", "metal", 100, 50.0, "sell")
        contract_id = r.split("id: ")[1].rstrip(")")
        result = await engine.accept_contract("firm_b", contract_id)
        assert "error" in result
        assert "insufficient cash" in result
