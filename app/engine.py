import asyncio
import time
from copy import deepcopy
from uuid import uuid4

from app.config import (
    CAR_SELL_PRICE,
    CONTRACT_EXPIRY_SECONDS,
    FACTORY_BUY_PRICE,
    FACTORY_PRODUCTION_SECONDS,
    GAME_DURATION_SECONDS,
    ORE_BUY_PRICE,
    STARTING_CASH,
    STARTING_FACTORY_COUNT,
    FIRM_CONFIGS,
)
from app.events import Event, EventBus, EventType
from app.models import (
    Commodity,
    Contract,
    ContractSide,
    FactoryJob,
    FactoryType,
    Firm,
    FACTORY_IO,
    Message,
)


class GameEngine:
    def __init__(self, event_bus: EventBus) -> None:
        self._lock = asyncio.Lock()
        self._event_bus = event_bus
        self._firms: dict[str, Firm] = {}
        self._contracts: dict[str, Contract] = {}
        self._messages: list[Message] = []
        self._factory_jobs: list[FactoryJob] = []
        self._game_running = False
        self._start_time: float = 0.0
        self.total_api_cost: float = 0.0
        self._tool_call_log: list[dict] = []

        # Per-agent asyncio.Event for wait/notify
        self._agent_wake_events: dict[str, asyncio.Event] = {}

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
        """Complete all in-progress factory jobs immediately.

        Adds output to inventory and resets running_factories to 0.
        Called before saving so we don't need to track partial progress.
        """
        for job in self._factory_jobs:
            _, output_commodity = FACTORY_IO[job.factory_type]
            firm = self._firms[job.firm_id]
            firm.inventory[output_commodity] += job.count
            firm.running_factories[job.factory_type] = max(
                0, firm.running_factories[job.factory_type] - job.count
            )
        self._factory_jobs.clear()

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

    def _notify_agent(self, firm_id: str) -> None:
        ev = self._agent_wake_events.get(firm_id)
        if ev:
            ev.set()

    async def agent_wait(self, firm_id: str, seconds: float) -> str:
        ev = self._agent_wake_events.get(firm_id)
        if not ev:
            return "error: unknown firm"
        ev.clear()
        try:
            await asyncio.wait_for(ev.wait(), timeout=min(seconds, self.time_remaining()))
            return "interrupted: new activity for your firm"
        except asyncio.TimeoutError:
            return "wait completed (timeout)"

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

    def get_contracts_snapshot(self) -> list[dict]:
        """Return all contracts, most recent first."""
        contracts = sorted(self._contracts.values(), key=lambda c: c.created_at, reverse=True)
        return [c.model_dump(mode="json") for c in contracts]

    def get_messages_snapshot(self) -> list[dict]:
        """Return all messages, most recent first."""
        msgs = sorted(self._messages, key=lambda m: m.timestamp, reverse=True)
        return [
            {
                "id": str(m.id),
                "from": m.sender_id,
                "to": m.recipient_id,
                "thread_id": m.thread_id,
                "content": m.content,
                "timestamp": m.timestamp,
            }
            for m in msgs
        ]

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
        await self._event_bus.publish(Event(
            type=EventType.INVENTORY_CHANGED, firm_id=firm_id,
            data={"commodity": "ore", "quantity": quantity, "action": "buy_ore"},
            timestamp=time.time(),
        ))
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
        await self._event_bus.publish(Event(
            type=EventType.INVENTORY_CHANGED, firm_id=firm_id,
            data={"commodity": "cars", "quantity": quantity, "action": "sell_cars"},
            timestamp=time.time(),
        ))
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
        await self._event_bus.publish(Event(
            type=EventType.FACTORY_PURCHASED, firm_id=firm_id,
            data={"factory_type": factory_type_str, "quantity": quantity},
            timestamp=time.time(),
        ))
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

        await self._event_bus.publish(Event(
            type=EventType.FACTORY_STARTED, firm_id=firm_id,
            data={
                "factory_type": factory_type_str,
                "count": count,
                "cost": round(total_cost, 2),
                "input": input_commodity.value,
                "output": output_commodity.value,
            },
            timestamp=time.time(),
        ))

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

        await self._event_bus.publish(Event(
            type=EventType.FACTORY_COMPLETED, firm_id=job.firm_id,
            data={
                "factory_type": job.factory_type.value,
                "count": job.count,
                "output": output_commodity.value,
            },
            timestamp=time.time(),
        ))

        # Wake the agent
        self._notify_agent(job.firm_id)

    # --- Contracts ---

    async def _expire_contract(self, contract_id: str) -> None:
        """Expire a contract after CONTRACT_EXPIRY_SECONDS if still pending."""
        await asyncio.sleep(CONTRACT_EXPIRY_SECONDS)
        async with self._lock:
            contract = self._contracts.get(contract_id)
            if not contract or contract.status != "pending":
                return
            contract.status = "expired"
        await self._event_bus.publish(Event(
            type=EventType.CONTRACT_REJECTED, firm_id=contract.sender_id,
            data={"contract_id": contract_id, "reason": "expired"},
            timestamp=time.time(),
        ))
        # Wake both parties so they know the contract expired
        self._notify_agent(contract.sender_id)
        self._notify_agent(contract.recipient_id)

    def _try_auto_resolve_locked(self, new_contract: Contract) -> dict | None:
        """Check if new contract can auto-resolve with an existing compatible one.

        Must be called with self._lock held. Returns resolution info dict or None.
        """
        # Determine buyer/seller for the new contract
        if new_contract.side == ContractSide.BUY:
            new_buyer = new_contract.sender_id
            new_seller = new_contract.recipient_id
        else:
            new_buyer = new_contract.recipient_id
            new_seller = new_contract.sender_id

        # Search for matching pending contract (oldest first)
        candidates = sorted(
            (c for c in self._contracts.values()
             if c.id != new_contract.id and c.status == "pending"
             and c.commodity == new_contract.commodity),
            key=lambda c: c.created_at,
        )

        for existing in candidates:
            if existing.side == ContractSide.BUY:
                exist_buyer = existing.sender_id
                exist_seller = existing.recipient_id
            else:
                exist_buyer = existing.recipient_id
                exist_seller = existing.sender_id

            # Must be same trade direction (same buyer and seller)
            if exist_buyer != new_buyer or exist_seller != new_seller:
                continue

            # Need one BUY and one SELL to have both price perspectives
            if new_contract.side == existing.side:
                continue

            # Determine buyer's and seller's prices
            if new_contract.side == ContractSide.BUY:
                buyer_price = new_contract.price_per_unit
                seller_price = existing.price_per_unit
            else:
                buyer_price = existing.price_per_unit
                seller_price = new_contract.price_per_unit

            # Compatible: buyer willing to pay >= seller's ask
            if buyer_price < seller_price:
                continue

            # Only auto-match exact quantities
            if new_contract.quantity != existing.quantity:
                continue

            # Execute the trade at the average price
            trade_qty = new_contract.quantity
            trade_price = (buyer_price + seller_price) / 2
            total_cost = trade_qty * trade_price

            buyer = self._firms[new_buyer]
            seller = self._firms[new_seller]

            # Validate both sides can actually fulfill
            if buyer.cash < total_cost:
                continue
            if seller.inventory[new_contract.commodity] < trade_qty:
                continue

            # Execute atomic transfer
            buyer.cash -= total_cost
            seller.cash += total_cost
            seller.inventory[new_contract.commodity] -= trade_qty
            buyer.inventory[new_contract.commodity] += trade_qty
            new_contract.status = "accepted"
            existing.status = "accepted"

            return {
                "buyer": new_buyer,
                "seller": new_seller,
                "quantity": trade_qty,
                "commodity": new_contract.commodity.value,
                "trade_price": trade_price,
                "total_cost": total_cost,
                "buyer_price": buyer_price,
                "seller_price": seller_price,
                "existing_contract_id": str(existing.id),
                "new_contract_id": str(new_contract.id),
            }

        return None

    async def send_contract(
        self,
        sender_id: str,
        recipient_id: str,
        commodity_str: str,
        quantity: int,
        price_per_unit: float,
        side_str: str,
    ) -> str:
        if sender_id == recipient_id:
            return "error: cannot send contract to yourself"
        if quantity <= 0:
            return "error: quantity must be positive"
        if price_per_unit < 0:
            return "error: price must be non-negative"
        if recipient_id not in self._firms:
            return f"error: unknown firm '{recipient_id}'"
        try:
            commodity = Commodity(commodity_str)
        except ValueError:
            return f"error: invalid commodity '{commodity_str}'. Valid: ore, metal, parts, cars"
        try:
            side = ContractSide(side_str)
        except ValueError:
            return f"error: invalid side '{side_str}'. Valid: buy, sell"

        contract = Contract(
            id=uuid4(),
            sender_id=sender_id,
            recipient_id=recipient_id,
            commodity=commodity,
            quantity=quantity,
            price_per_unit=price_per_unit,
            side=side,
            status="pending",
            created_at=time.time(),
        )

        async with self._lock:
            self._contracts[str(contract.id)] = contract
            auto = self._try_auto_resolve_locked(contract)

        await self._event_bus.publish(Event(
            type=EventType.CONTRACT_SENT, firm_id=sender_id,
            data={
                "contract_id": str(contract.id),
                "to": recipient_id,
                "commodity": commodity_str,
                "quantity": quantity,
                "price_per_unit": price_per_unit,
                "side": side_str,
            },
            timestamp=time.time(),
        ))

        if auto:
            await self._event_bus.publish(Event(
                type=EventType.CONTRACT_ACCEPTED, firm_id=sender_id,
                data={
                    "contract_id": auto["new_contract_id"],
                    "auto_resolved_with": auto["existing_contract_id"],
                    "buyer": auto["buyer"],
                    "seller": auto["seller"],
                    "commodity": auto["commodity"],
                    "quantity": auto["quantity"],
                    "total_price": auto["total_cost"],
                },
                timestamp=time.time(),
            ))
            # Wake both parties
            self._notify_agent(auto["buyer"])
            self._notify_agent(auto["seller"])

            return (
                f"auto-matched with existing contract! "
                f"{auto['quantity']} {auto['commodity']} transferred from "
                f"{auto['seller']} to {auto['buyer']} at "
                f"${auto['trade_price']:.2f}/unit (${auto['total_cost']:.2f} total). "
                f"Trade price averaged from ${auto['buyer_price']:.2f} bid "
                f"and ${auto['seller_price']:.2f} ask."
            )

        # No auto-match, just a normal pending contract
        self._notify_agent(recipient_id)

        # Schedule expiry
        asyncio.create_task(self._expire_contract(str(contract.id)))

        action = "buy" if side == ContractSide.BUY else "sell"
        return (
            f"contract sent to {recipient_id}: {action} {quantity} {commodity_str} "
            f"at ${price_per_unit:.2f}/unit (id: {contract.id})"
        )

    async def accept_contract(self, firm_id: str, contract_id: str) -> str:
        async with self._lock:
            contract = self._contracts.get(contract_id)
            if not contract:
                return f"error: contract '{contract_id}' not found"
            if contract.status != "pending":
                return f"error: contract is already {contract.status}"
            if contract.recipient_id != firm_id:
                if contract.sender_id == firm_id:
                    return (
                        f"error: you sent this contract — only the recipient "
                        f"({contract.recipient_id}) can accept it"
                    )
                return (
                    f"error: this contract was sent to "
                    f"{contract.recipient_id}, not to you"
                )

            # Determine buyer and seller
            if contract.side == ContractSide.BUY:
                buyer_id, seller_id = contract.sender_id, contract.recipient_id
            else:
                buyer_id, seller_id = contract.recipient_id, contract.sender_id

            buyer = self._firms[buyer_id]
            seller = self._firms[seller_id]
            total_cost = contract.quantity * contract.price_per_unit

            # Validate both sides
            if buyer.cash < total_cost:
                return (
                    f"error: buyer ({buyer_id}) has insufficient cash "
                    f"(need ${total_cost:.2f}, have ${buyer.cash:.2f}, "
                    f"short ${total_cost - buyer.cash:.2f})"
                )
            if seller.inventory[contract.commodity] < contract.quantity:
                have = seller.inventory[contract.commodity]
                short = contract.quantity - have
                hint = (
                    f" Try again when they have enough, "
                    f"or send a smaller contract for {have} units."
                    if have > 0
                    else " The seller has none right now."
                )
                return (
                    f"error: seller ({seller_id}) has insufficient "
                    f"{contract.commodity.value} "
                    f"(need {contract.quantity}, have {have}, "
                    f"short {short}).{hint}"
                )

            # Execute atomic transfer
            buyer.cash -= total_cost
            seller.cash += total_cost
            seller.inventory[contract.commodity] -= contract.quantity
            buyer.inventory[contract.commodity] += contract.quantity
            contract.status = "accepted"

        await self._event_bus.publish(Event(
            type=EventType.CONTRACT_ACCEPTED, firm_id=firm_id,
            data={
                "contract_id": contract_id,
                "buyer": buyer_id,
                "seller": seller_id,
                "commodity": contract.commodity.value,
                "quantity": contract.quantity,
                "total_price": total_cost,
            },
            timestamp=time.time(),
        ))

        # Wake both parties
        self._notify_agent(contract.sender_id)
        self._notify_agent(contract.recipient_id)

        return (
            f"contract accepted: {contract.quantity} {contract.commodity.value} "
            f"transferred from {seller_id} to {buyer_id} for ${total_cost:.2f}"
        )

    async def reject_contract(self, firm_id: str, contract_id: str) -> str:
        async with self._lock:
            contract = self._contracts.get(contract_id)
            if not contract:
                return f"error: contract '{contract_id}' not found"
            if contract.status != "pending":
                return f"error: contract is already {contract.status}"
            if contract.recipient_id != firm_id:
                if contract.sender_id == firm_id:
                    return (
                        f"error: you sent this contract — only the recipient "
                        f"({contract.recipient_id}) can reject it"
                    )
                return (
                    f"error: this contract was sent to "
                    f"{contract.recipient_id}, not to you"
                )
            contract.status = "rejected"

        await self._event_bus.publish(Event(
            type=EventType.CONTRACT_REJECTED, firm_id=firm_id,
            data={"contract_id": contract_id},
            timestamp=time.time(),
        ))

        self._notify_agent(contract.sender_id)

        return f"contract {contract_id} rejected"

    async def view_contracts(self, firm_id: str) -> list[dict]:
        async with self._lock:
            result = []
            for c in self._contracts.values():
                if c.status == "pending" and (c.sender_id == firm_id or c.recipient_id == firm_id):
                    result.append(c.model_dump(mode="json"))
            return result

    # --- Messaging ---

    async def send_message(
        self, sender_id: str, recipient_id: str, thread_id: str, content: str
    ) -> str:
        if recipient_id not in self._firms:
            return f"error: unknown firm '{recipient_id}'"
        if sender_id == recipient_id:
            return "error: cannot send message to yourself"

        msg = Message(
            id=uuid4(),
            sender_id=sender_id,
            recipient_id=recipient_id,
            thread_id=thread_id,
            content=content,
            timestamp=time.time(),
            read_by={sender_id},
        )

        async with self._lock:
            self._messages.append(msg)

        await self._event_bus.publish(Event(
            type=EventType.MESSAGE_SENT, firm_id=sender_id,
            data={
                "to": recipient_id,
                "thread_id": thread_id,
                "content": content,
            },
            timestamp=time.time(),
        ))

        self._notify_agent(recipient_id)

        return f"message sent to {recipient_id} (thread: {thread_id})"

    async def view_messages(self, firm_id: str) -> list[dict]:
        async with self._lock:
            result = []
            for msg in self._messages:
                if msg.recipient_id == firm_id and firm_id not in msg.read_by:
                    result.append({
                        "id": str(msg.id),
                        "from": msg.sender_id,
                        "thread_id": msg.thread_id,
                        "content": msg.content,
                        "timestamp": msg.timestamp,
                    })
                    msg.read_by.add(firm_id)
            return result

    # --- Firm state ---

    async def view_state(self, firm_id: str) -> dict:
        async with self._lock:
            firm = self._firms[firm_id]
            return {
                "firm_id": firm.id,
                "name": firm.name,
                "cash": round(firm.cash, 2),
                "inventory": {c.value: firm.inventory[c] for c in Commodity},
                "factories": {ft.value: firm.factories[ft] for ft in FactoryType},
                "running_factories": {ft.value: firm.running_factories[ft] for ft in FactoryType},
                "time_remaining": round(self.time_remaining(), 1),
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
