import asyncio
import time
from copy import deepcopy
from uuid import uuid4

from app.config import (
    CAR_SELL_PRICE,
    FACTORY_BUY_PRICE,
    FACTORY_PRODUCTION_SECONDS,
    GAME_DURATION_SECONDS,
    ORE_BUY_PRICE,
    STARTING_CASH,
    STARTING_FACTORY_COUNT,
    FIRM_CONFIGS,
)
from app.models import (
    Commodity,
    Order,
    OrderSide,
    FactoryJob,
    FactoryType,
    Firm,
    FACTORY_IO,
)


class GameEngine:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._firms: dict[str, Firm] = {}
        self._orders: dict[str, Order] = {}
        self._factory_jobs: list[FactoryJob] = []
        self._game_running = False
        self._start_time: float = 0.0
        self.total_api_cost: float = 0.0
        self._tool_call_log: list[dict] = []
        self._reasoning_log: list[dict] = []
        self._activity_log: list[str] = []

        # Per-agent asyncio.Event for wait/notify
        self._agent_wake_events: dict[str, asyncio.Event] = {}
        self._agent_wake_reasons: dict[str, str] = {}

    def log_activity(self, event_type: str, firm_id: str | None = None, data: dict | None = None) -> None:
        parts = [f"[{event_type}]"]
        if firm_id:
            parts.append(firm_id)
        if data:
            parts.append(str(data))
        self._activity_log.append(" ".join(parts))

    def get_activity_log(self) -> list[str]:
        return list(self._activity_log)

    def setup_starting_state(self) -> None:
        for cfg in FIRM_CONFIGS:
            firm_id = cfg["id"]
            factory_type = FactoryType(cfg["factory_type"])

            factories = {ft: 0 for ft in FactoryType}
            factories[factory_type] = STARTING_FACTORY_COUNT

            self._firms[firm_id] = Firm(
                id=firm_id,
                name=cfg["name"],
                cash=STARTING_CASH,
                inventory={c: 0 for c in Commodity},
                factories=factories,
                running_factories={ft: 0 for ft in FactoryType},
            )
            self._agent_wake_events[firm_id] = asyncio.Event()

    def restore_from_save(self, save_data: dict) -> None:
        """Restore firm states from a save file instead of using defaults."""
        firms_data = save_data["firms"]
        for firm_id, fdata in firms_data.items():
            self._firms[firm_id] = Firm(
                id=firm_id,
                name=fdata["name"],
                cash=fdata["cash"],
                inventory={Commodity(k): v for k, v in fdata["inventory"].items()},
                factories={FactoryType(k): v for k, v in fdata["factories"].items()},
                running_factories={ft: 0 for ft in FactoryType},
            )
            self._agent_wake_events[firm_id] = asyncio.Event()

    def finalize_factory_jobs(self) -> None:
        """Complete all in-progress factory jobs immediately."""
        for job in self._factory_jobs:
            _, output_commodity = FACTORY_IO[job.factory_type]
            firm = self._firms[job.firm_id]
            firm.inventory[output_commodity] += job.count
            firm.running_factories[job.factory_type] = max(
                0, firm.running_factories[job.factory_type] - job.count
            )
        self._factory_jobs.clear()

    def finalize_orders(self) -> None:
        """Return escrowed resources from open orders back to firms."""
        for order in self._orders.values():
            if order.status != "open":
                continue
            firm = self._firms[order.firm_id]
            if order.side == OrderSide.BUY:
                firm.cash += order.quantity * order.price_per_unit
            else:
                firm.inventory[order.commodity] += order.quantity
            order.status = "cancelled"

    def to_save_dict(self) -> dict:
        """Return a serializable dict of all firm states."""
        result = {}
        for firm_id, firm in self._firms.items():
            result[firm_id] = {
                "name": firm.name,
                "cash": round(firm.cash, 2),
                "inventory": {c.value: firm.inventory[c] for c in Commodity},
                "factories": {ft.value: firm.factories[ft] for ft in FactoryType},
            }
        return result

    def get_tool_call_trace_for_save(self) -> dict[str, list[dict]]:
        """Return tool call trace grouped by firm for saving."""
        trace: dict[str, list[dict]] = {}
        for entry in self._tool_call_log:
            fid = entry["firm_id"]
            trace.setdefault(fid, []).append({
                "tool": entry["tool"],
                "args": entry["args"],
                "result": entry["result"],
                "timestamp": entry["timestamp"],
            })
        return trace

    # --- Reasoning summary tracking ---

    def record_reasoning_summary(
        self, firm_id: str, summary: str, timestamp: float
    ) -> None:
        self._reasoning_log.append({
            "firm_id": firm_id,
            "summary": summary,
            "timestamp": timestamp,
        })

    def get_reasoning_trace_for_save(self) -> dict[str, list[dict]]:
        """Return reasoning summaries grouped by firm for saving."""
        trace: dict[str, list[dict]] = {}
        for entry in self._reasoning_log:
            fid = entry["firm_id"]
            trace.setdefault(fid, []).append({
                "summary": entry["summary"],
                "timestamp": entry["timestamp"],
            })
        return trace

    def get_full_trace(self) -> dict[str, list[dict]]:
        """Return merged reasoning + tool call trace per firm, sorted by time."""
        trace: dict[str, list[dict]] = {}
        for entry in self._reasoning_log:
            fid = entry["firm_id"]
            trace.setdefault(fid, []).append({
                "type": "reasoning",
                "summary": entry["summary"],
                "timestamp": entry["timestamp"],
            })
        for entry in self._tool_call_log:
            fid = entry["firm_id"]
            trace.setdefault(fid, []).append({
                "type": "tool_call",
                "tool": entry["tool"],
                "args": entry["args"],
                "timestamp": entry["timestamp"],
            })
        for fid in trace:
            trace[fid].sort(key=lambda e: e["timestamp"])
        return trace

    def start_game(self) -> None:
        self._start_time = time.time()
        self._game_running = True

    def time_remaining(self) -> float:
        if not self._game_running:
            return 0.0
        elapsed = time.time() - self._start_time
        return max(0.0, GAME_DURATION_SECONDS - elapsed)

    @property
    def game_running(self) -> bool:
        return self._game_running and self.time_remaining() > 0

    def stop_game(self) -> None:
        self._game_running = False

    # --- Wake/notify for wait tool ---

    def _notify_agent(self, firm_id: str, reason: str) -> None:
        ev = self._agent_wake_events.get(firm_id)
        if ev:
            self._agent_wake_reasons[firm_id] = reason
            ev.set()

    async def agent_wait(self, firm_id: str, seconds: float) -> str:
        ev = self._agent_wake_events.get(firm_id)
        if not ev:
            return "error: unknown firm"
        ev.clear()
        self._agent_wake_reasons.pop(firm_id, None)
        start = time.monotonic()
        try:
            await asyncio.wait_for(ev.wait(), timeout=min(seconds, self.time_remaining()))
            elapsed = time.monotonic() - start
            reason = self._agent_wake_reasons.pop(firm_id, "unknown")
            return f"interrupted after {elapsed:.1f}s (of {seconds:.0f}s requested): {reason}"
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - start
            return f"wait completed after {elapsed:.1f}s (timeout)"

    # --- Tool call tracking ---

    def record_tool_call(
        self, firm_id: str, tool_name: str, arguments: dict, result: str, timestamp: float
    ) -> None:
        self._tool_call_log.append({
            "firm_id": firm_id,
            "tool": tool_name,
            "args": arguments,
            "result": result,
            "timestamp": timestamp,
        })

    def get_latest_tool_calls(self) -> dict[str, dict]:
        """Return the most recent tool call for each firm."""
        latest: dict[str, dict] = {}
        for entry in self._tool_call_log:
            latest[entry["firm_id"]] = entry
        return latest

    def get_tool_call_log(self) -> list[dict]:
        return list(self._tool_call_log)

    # --- State snapshots ---

    def get_firm_snapshot(self, firm_id: str) -> Firm:
        return deepcopy(self._firms[firm_id])

    def get_all_firms_snapshot(self) -> list[Firm]:
        return [deepcopy(f) for f in self._firms.values()]

    def get_state_snapshot(self) -> dict:
        return {
            "firms": {fid: f.model_dump() for fid, f in self._firms.items()},
            "time_remaining": self.time_remaining(),
            "game_running": self.game_running,
        }

    def get_orders_snapshot(self) -> list[dict]:
        """Return all orders, most recent first."""
        orders = sorted(self._orders.values(), key=lambda o: o.created_at, reverse=True)
        return [o.model_dump(mode="json") for o in orders]

    def get_factory_jobs_snapshot(self) -> list[dict]:
        """Return all active factory jobs, soonest completion first."""
        now = time.time()
        active = [j for j in self._factory_jobs if j.completes_at > now]
        active.sort(key=lambda j: j.completes_at)
        return [
            {
                "firm_id": j.firm_id,
                "factory_type": j.factory_type.value,
                "count": j.count,
                "started_at": j.started_at,
                "completes_at": j.completes_at,
                "seconds_left": round(j.completes_at - now, 1),
            }
            for j in active
        ]

    # --- Production cost ---

    def _production_cost_per_unit(self, n: int) -> float:
        if n <= 0:
            return 2.0
        return 1.0 + 1.0 / (n ** 0.3)

    # --- Ore / Cars ---

    async def buy_ore(self, firm_id: str, quantity: int) -> str:
        if quantity <= 0:
            return "error: quantity must be positive"
        async with self._lock:
            firm = self._firms[firm_id]
            total_cost = quantity * ORE_BUY_PRICE
            if firm.cash < total_cost:
                max_affordable = int(firm.cash / ORE_BUY_PRICE)
                return (
                    f"error: insufficient cash to buy {quantity} ore "
                    f"(need ${total_cost:.2f}, have ${firm.cash:.2f}). "
                    f"You can afford up to {max_affordable} ore."
                )
            firm.cash -= total_cost
            firm.inventory[Commodity.ORE] += quantity
        self.log_activity("inventory_changed", firm_id, {"commodity": "ore", "quantity": quantity, "action": "buy_ore"})
        return f"bought {quantity} ore for ${total_cost:.2f}"

    async def sell_cars(self, firm_id: str, quantity: int) -> str:
        if quantity <= 0:
            return "error: quantity must be positive"
        async with self._lock:
            firm = self._firms[firm_id]
            if firm.inventory[Commodity.CARS] < quantity:
                have = firm.inventory[Commodity.CARS]
                running = firm.running_factories[FactoryType.CAR]
                if running > 0:
                    return (
                        f"error: you only have {have} cars (need {quantity}). "
                        f"You have {running} car factories currently running — "
                        f"wait for production to complete."
                    )
                return (
                    f"error: you only have {have} cars (need {quantity}). "
                    f"Run car factories with parts to produce more."
                )
            total_revenue = quantity * CAR_SELL_PRICE
            firm.inventory[Commodity.CARS] -= quantity
            firm.cash += total_revenue
        self.log_activity("inventory_changed", firm_id, {"commodity": "cars", "quantity": quantity, "action": "sell_cars"})
        return f"sold {quantity} cars for ${total_revenue:.2f}"

    # --- Factory purchase ---

    async def buy_factory(self, firm_id: str, factory_type_str: str, quantity: int) -> str:
        if quantity <= 0:
            return "error: quantity must be positive"
        try:
            factory_type = FactoryType(factory_type_str)
        except ValueError:
            return f"error: invalid factory type '{factory_type_str}'. Valid: metal, part, car"
        async with self._lock:
            firm = self._firms[firm_id]
            total_cost = quantity * FACTORY_BUY_PRICE
            if firm.cash < total_cost:
                max_affordable = int(firm.cash / FACTORY_BUY_PRICE)
                return (
                    f"error: insufficient cash to buy {quantity} {factory_type_str} factories "
                    f"(need ${total_cost:.2f}, have ${firm.cash:.2f}). "
                    f"You can afford up to {max_affordable}."
                )
            firm.cash -= total_cost
            firm.factories[factory_type] += quantity
        self.log_activity("factory_purchased", firm_id, {"factory_type": factory_type_str, "quantity": quantity})
        return f"bought {quantity} {factory_type_str} factory(s) for ${total_cost:.2f}"

    # --- Factory operations ---

    async def start_factories(self, firm_id: str, factory_type_str: str, count: int) -> str:
        if count <= 0:
            return "error: count must be positive"
        try:
            factory_type = FactoryType(factory_type_str)
        except ValueError:
            return f"error: invalid factory type '{factory_type_str}'. Valid: metal, part, car"

        input_commodity, output_commodity = FACTORY_IO[factory_type]

        async with self._lock:
            firm = self._firms[firm_id]
            total = firm.factories[factory_type]
            running = firm.running_factories[factory_type]
            idle = total - running
            if count > idle:
                if idle == 0 and running > 0:
                    return (
                        f"error: all {total} of your {factory_type_str} factories "
                        f"are currently running. Wait for production to complete "
                        f"or buy more factories."
                    )
                return (
                    f"error: only {idle} idle {factory_type_str} factories "
                    f"(requested {count}). {running} of your {total} are "
                    f"currently running."
                )
            have_input = firm.inventory[input_commodity]
            if have_input < count:
                if have_input == 0:
                    return (
                        f"error: you have no {input_commodity.value}. "
                        f"Each {factory_type_str} factory requires 1 "
                        f"{input_commodity.value} to run."
                    )
                return (
                    f"error: insufficient {input_commodity.value} "
                    f"(have {have_input}, need {count}). "
                    f"You can run up to {have_input} factories with "
                    f"your current {input_commodity.value}."
                )
            cost_per_unit = self._production_cost_per_unit(firm.factories[factory_type])
            total_cost = cost_per_unit * count
            if firm.cash < total_cost:
                return (
                    f"error: insufficient cash to run {count} factories "
                    f"(need ${total_cost:.2f}, have ${firm.cash:.2f}, "
                    f"short ${total_cost - firm.cash:.2f})"
                )

            # Deduct inputs
            firm.inventory[input_commodity] -= count
            firm.cash -= total_cost
            firm.running_factories[factory_type] += count

            now = time.time()
            job = FactoryJob(
                id=uuid4(),
                firm_id=firm_id,
                factory_type=factory_type,
                count=count,
                started_at=now,
                completes_at=now + FACTORY_PRODUCTION_SECONDS,
            )
            self._factory_jobs.append(job)

        self.log_activity("factory_started", firm_id, {
            "factory_type": factory_type_str,
            "count": count,
            "cost": round(total_cost, 2),
            "input": input_commodity.value,
            "output": output_commodity.value,
        })

        # Schedule completion
        asyncio.create_task(self._complete_factory_job(job))

        return (
            f"started {count} {factory_type_str} factory(s): "
            f"{count} {input_commodity.value} -> {count} {output_commodity.value} "
            f"in {FACTORY_PRODUCTION_SECONDS}s (cost ${total_cost:.2f})"
        )

    async def _complete_factory_job(self, job: FactoryJob) -> None:
        delay = job.completes_at - time.time()
        if delay > 0:
            await asyncio.sleep(delay)

        _, output_commodity = FACTORY_IO[job.factory_type]

        async with self._lock:
            firm = self._firms[job.firm_id]
            firm.inventory[output_commodity] += job.count
            firm.running_factories[job.factory_type] -= job.count

        self.log_activity("factory_completed", job.firm_id, {
            "factory_type": job.factory_type.value,
            "count": job.count,
            "output": output_commodity.value,
        })

        # Wake the agent
        self._notify_agent(job.firm_id, f"factory completed: {job.count} {output_commodity.value} produced")

    # --- Order book ---

    def _try_match_order(self, new_order: Order) -> dict | None:
        """Try to match a new order against existing open orders.

        Must be called with self._lock held. Returns match info or None.
        Matches best price first (lowest ask for buys, highest bid for sells).
        """
        # Find compatible orders from OTHER firms
        candidates = [
            o for o in self._orders.values()
            if o.status == "open"
            and o.commodity == new_order.commodity
            and o.firm_id != new_order.firm_id
            and o.side != new_order.side
        ]

        if not candidates:
            return None

        if new_order.side == OrderSide.BUY:
            # Buyer wants cheapest seller
            candidates.sort(key=lambda o: o.price_per_unit)
            best = candidates[0]
            if new_order.price_per_unit < best.price_per_unit:
                return None  # buyer's price too low
            trade_price = best.price_per_unit  # taker pays maker's price
        else:
            # Seller wants highest buyer
            candidates.sort(key=lambda o: -o.price_per_unit)
            best = candidates[0]
            if new_order.price_per_unit > best.price_per_unit:
                return None  # seller's price too high
            trade_price = best.price_per_unit  # taker gets maker's price

        # Trade the minimum of both quantities
        trade_qty = min(new_order.quantity, best.quantity)
        total_cost = trade_qty * trade_price

        if new_order.side == OrderSide.BUY:
            buyer_id, seller_id = new_order.firm_id, best.firm_id
        else:
            buyer_id, seller_id = best.firm_id, new_order.firm_id

        buyer = self._firms[buyer_id]
        seller = self._firms[seller_id]

        # Resources are already escrowed, so just transfer:
        # - Buyer escrowed cash at their price; refund difference if trade_price < buyer's price
        # - Seller escrowed goods; they get cash
        # For the new order (taker):
        if new_order.side == OrderSide.BUY:
            # New order is buy: cash was escrowed at new_order.price_per_unit
            # Trade happens at best.price_per_unit (which is <= new_order.price_per_unit)
            # Refund the difference to buyer
            refund_per_unit = new_order.price_per_unit - trade_price
            buyer.cash += refund_per_unit * trade_qty
            # Give goods to buyer
            buyer.inventory[new_order.commodity] += trade_qty
            # Give cash to seller
            seller.cash += total_cost
        else:
            # New order is sell: goods were escrowed from seller (new_order)
            # Trade happens at best.price_per_unit (which is >= new_order.price_per_unit)
            # Refund difference to buyer (the existing order)
            refund_per_unit = best.price_per_unit - trade_price
            buyer.cash += refund_per_unit * trade_qty
            # Give goods to buyer
            buyer.inventory[new_order.commodity] += trade_qty
            # Give cash to seller
            seller.cash += total_cost

        # Update order quantities
        if trade_qty == best.quantity:
            best.status = "filled"
        else:
            best.quantity -= trade_qty

        if trade_qty == new_order.quantity:
            new_order.status = "filled"
        else:
            new_order.quantity -= trade_qty
            # Need to update escrowed amount for remaining quantity
            # (already correct since we only transferred trade_qty worth)

        return {
            "buyer": buyer_id,
            "seller": seller_id,
            "quantity": trade_qty,
            "commodity": new_order.commodity.value,
            "trade_price": trade_price,
            "total_cost": total_cost,
            "matched_order_id": str(best.id),
            "new_order_id": str(new_order.id),
        }

    async def post_buy_order(
        self, firm_id: str, commodity_str: str, quantity: int, price_per_unit: float
    ) -> str:
        if quantity <= 0:
            return "error: quantity must be positive"
        if price_per_unit <= 0:
            return "error: price must be positive"
        try:
            commodity = Commodity(commodity_str)
        except ValueError:
            return f"error: invalid commodity '{commodity_str}'. Valid: ore, metal, parts, cars"

        escrow_cost = quantity * price_per_unit

        order = Order(
            id=uuid4(),
            firm_id=firm_id,
            commodity=commodity,
            quantity=quantity,
            price_per_unit=price_per_unit,
            side=OrderSide.BUY,
            status="open",
            created_at=time.time(),
        )

        async with self._lock:
            firm = self._firms[firm_id]
            if firm.cash < escrow_cost:
                return (
                    f"error: insufficient cash (need ${escrow_cost:.2f} in escrow, "
                    f"have ${firm.cash:.2f})"
                )
            # Escrow cash
            firm.cash -= escrow_cost
            self._orders[str(order.id)] = order

            # Try to match
            matches = []
            while order.status == "open":
                match = self._try_match_order(order)
                if match:
                    matches.append(match)
                else:
                    break

        self.log_activity("order_posted", firm_id, {
            "order_id": str(order.id),
            "side": "buy",
            "commodity": commodity_str,
            "quantity": quantity,
            "price_per_unit": price_per_unit,
        })

        if matches:
            for match in matches:
                self.log_activity("order_filled", firm_id, match)
                # Wake the other party
                other = match["seller"] if match["buyer"] == firm_id else match["buyer"]
                self._notify_agent(other, f"order filled: {match['quantity']} {match['commodity']} at ${match['trade_price']:.2f}/unit")

            total_filled = sum(m["quantity"] for m in matches)
            total_spent = sum(m["total_cost"] for m in matches)
            result = f"buy order filled: bought {total_filled} {commodity_str} for ${total_spent:.2f}"
            if order.status == "open":
                result += f" (remaining {order.quantity} units still on order book at ${price_per_unit:.2f}/unit, id: {order.id})"
            return result

        # No match — order sits on book
        self._notify_all_agents_except(firm_id, f"new buy order: {quantity} {commodity_str} at ${price_per_unit:.2f}/unit")
        return (
            f"buy order posted: {quantity} {commodity_str} at ${price_per_unit:.2f}/unit "
            f"(${escrow_cost:.2f} escrowed). Order id: {order.id}"
        )

    async def post_sell_order(
        self, firm_id: str, commodity_str: str, quantity: int, price_per_unit: float
    ) -> str:
        if quantity <= 0:
            return "error: quantity must be positive"
        if price_per_unit <= 0:
            return "error: price must be positive"
        try:
            commodity = Commodity(commodity_str)
        except ValueError:
            return f"error: invalid commodity '{commodity_str}'. Valid: ore, metal, parts, cars"

        order = Order(
            id=uuid4(),
            firm_id=firm_id,
            commodity=commodity,
            quantity=quantity,
            price_per_unit=price_per_unit,
            side=OrderSide.SELL,
            status="open",
            created_at=time.time(),
        )

        async with self._lock:
            firm = self._firms[firm_id]
            if firm.inventory[commodity] < quantity:
                have = firm.inventory[commodity]
                return (
                    f"error: insufficient {commodity_str} "
                    f"(have {have}, need {quantity})"
                )
            # Escrow goods
            firm.inventory[commodity] -= quantity
            self._orders[str(order.id)] = order

            # Try to match
            matches = []
            while order.status == "open":
                match = self._try_match_order(order)
                if match:
                    matches.append(match)
                else:
                    break

        self.log_activity("order_posted", firm_id, {
            "order_id": str(order.id),
            "side": "sell",
            "commodity": commodity_str,
            "quantity": quantity,
            "price_per_unit": price_per_unit,
        })

        if matches:
            for match in matches:
                self.log_activity("order_filled", firm_id, match)
                other = match["buyer"] if match["seller"] == firm_id else match["seller"]
                self._notify_agent(other, f"order filled: {match['quantity']} {match['commodity']} at ${match['trade_price']:.2f}/unit")

            total_filled = sum(m["quantity"] for m in matches)
            total_earned = sum(m["total_cost"] for m in matches)
            result = f"sell order filled: sold {total_filled} {commodity_str} for ${total_earned:.2f}"
            if order.status == "open":
                result += f" (remaining {order.quantity} units still on order book at ${price_per_unit:.2f}/unit, id: {order.id})"
            return result

        # No match — order sits on book
        self._notify_all_agents_except(firm_id, f"new sell order: {quantity} {commodity_str} at ${price_per_unit:.2f}/unit")
        return (
            f"sell order posted: {quantity} {commodity_str} at ${price_per_unit:.2f}/unit "
            f"({quantity} {commodity_str} escrowed). Order id: {order.id}"
        )

    async def cancel_order(self, firm_id: str, order_id: str) -> str:
        async with self._lock:
            order = self._orders.get(order_id)
            if not order:
                return f"error: order '{order_id}' not found"
            if order.firm_id != firm_id:
                return "error: this is not your order"
            if order.status != "open":
                return f"error: order is already {order.status}"

            # Return escrowed resources
            firm = self._firms[firm_id]
            if order.side == OrderSide.BUY:
                firm.cash += order.quantity * order.price_per_unit
            else:
                firm.inventory[order.commodity] += order.quantity
            order.status = "cancelled"

        self.log_activity("order_cancelled", firm_id, {"order_id": order_id})
        if order.side == OrderSide.BUY:
            return f"buy order cancelled, ${order.quantity * order.price_per_unit:.2f} returned to cash"
        else:
            return f"sell order cancelled, {order.quantity} {order.commodity.value} returned to inventory"

    def _notify_all_agents_except(self, exclude_firm_id: str, reason: str) -> None:
        for firm_id in self._firms:
            if firm_id != exclude_firm_id:
                self._notify_agent(firm_id, reason)

    # --- Firm state (includes order book) ---

    async def view_state(self, firm_id: str) -> dict:
        async with self._lock:
            firm = self._firms[firm_id]
            # Collect all open orders
            open_orders = []
            for o in self._orders.values():
                if o.status == "open":
                    open_orders.append({
                        "id": str(o.id),
                        "firm": o.firm_id,
                        "side": o.side.value,
                        "commodity": o.commodity.value,
                        "quantity": o.quantity,
                        "price_per_unit": o.price_per_unit,
                    })
            # Sort by commodity then price
            open_orders.sort(key=lambda o: (o["commodity"], o["price_per_unit"]))

            return {
                "firm_id": firm.id,
                "name": firm.name,
                "cash": round(firm.cash, 2),
                "inventory": {c.value: firm.inventory[c] for c in Commodity},
                "factories": {ft.value: firm.factories[ft] for ft in FactoryType},
                "running_factories": {ft.value: firm.running_factories[ft] for ft in FactoryType},
                "time_remaining": round(self.time_remaining(), 1),
                "order_book": open_orders,
            }

    # --- Results ---

    def get_results(self) -> list[dict]:
        results = []
        for firm in self._firms.values():
            results.append({
                "firm_id": firm.id,
                "name": firm.name,
                "cash": round(firm.cash, 2),
                "inventory": {c.value: firm.inventory[c] for c in Commodity},
                "factories": {ft.value: firm.factories[ft] for ft in FactoryType},
            })
        results.sort(key=lambda r: r["cash"], reverse=True)
        return results
