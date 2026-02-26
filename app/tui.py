"""Beautiful terminal UI for the Daegu Economy Simulator."""

import asyncio
import time
from datetime import datetime

from rich.align import Align
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from app.config import GAME_DURATION_SECONDS
from app.events import Event


FIRM_STYLES = {
    "firm_a": {"color": "bright_cyan", "tag": "A", "name": "Firm A"},
    "firm_b": {"color": "#f5a623", "tag": "B", "name": "Firm B"},
    "firm_c": {"color": "#50fa7b", "tag": "C", "name": "Firm C"},
}

RANK_LABELS = ["1st", "2nd", "3rd"]
RANK_STYLES = ["bold bright_yellow", "bold white", "dim white"]

STATUS_STYLES = {
    "pending": ("dim yellow", "?"),
    "accepted": ("green", "~"),
    "rejected": ("red", "x"),
}


class GameDisplay:
    """Real-time full-screen terminal display for the economy simulator."""

    def __init__(self, engine) -> None:
        self._engine = engine
        self._console = Console()
        self._live: Live | None = None
        self._game_over = False
        self._results: list[dict] | None = None

    # --- Event handling ---

    async def handle_event(self, event: Event) -> None:
        """EventBus subscriber — refresh display on any event."""
        self._refresh()

    # --- Layout: Game Screen ---

    def _render(self) -> Layout:
        if self._game_over and self._results:
            return self._render_results_screen()
        return self._render_game_screen()

    def _render_game_screen(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=5),
            Layout(name="body"),
        )
        layout["body"].split_column(
            Layout(name="firms", size=16),
            Layout(name="panes"),
        )
        layout["firms"].split_row(
            Layout(name="firm_a"),
            Layout(name="firm_b"),
            Layout(name="firm_c"),
        )
        layout["panes"].split_row(
            Layout(name="contracts"),
            Layout(name="messages"),
            Layout(name="factory_runs"),
        )

        layout["header"].update(self._render_header())

        state = self._engine.get_state_snapshot()
        firms = state.get("firms", {})
        for fid in ["firm_a", "firm_b", "firm_c"]:
            layout[fid].update(self._render_firm_card(fid, firms.get(fid, {})))

        layout["contracts"].update(self._render_contracts())
        layout["messages"].update(self._render_messages())
        layout["factory_runs"].update(self._render_factory_runs())
        return layout

    def _render_header(self) -> Panel:
        remaining = self._engine.time_remaining()
        elapsed = GAME_DURATION_SECONDS - remaining
        progress = elapsed / GAME_DURATION_SECONDS if GAME_DURATION_SECONDS > 0 else 0
        progress = max(0.0, min(1.0, progress))
        mins, secs = divmod(int(remaining), 60)

        title = Text(justify="center")
        title.append("D A E G U", style="bold bright_white")
        title.append("   Economy Simulator", style="dim")

        chain = Text(justify="center")
        chain.append("$1 ", style="dim")
        chain.append("ORE", style="bold bright_cyan")
        chain.append(" --> ", style="dim")
        chain.append("METAL", style="bold bright_cyan")
        chain.append(" --> ", style="dim")
        chain.append("PARTS", style="bold #f5a623")
        chain.append(" --> ", style="dim")
        chain.append("CARS", style="bold #50fa7b")
        chain.append(" $10", style="dim")

        bar_width = 40
        filled = int(bar_width * progress)
        bar = Text(justify="center")
        bar.append("━" * filled, style="bright_cyan")
        bar.append("╸" if filled < bar_width else "", style="bright_cyan")
        remaining_width = bar_width - filled - (1 if filled < bar_width else 0)
        bar.append("━" * remaining_width, style="grey23")
        bar.append(f"  {mins}:{secs:02d}", style="bold white")

        return Panel(
            Group(Align.center(title), Align.center(chain), Align.center(bar)),
            border_style="bright_cyan",
            padding=(0, 1),
        )

    def _render_firm_card(self, firm_id: str, data: dict) -> Panel:
        s = FIRM_STYLES[firm_id]
        color = s["color"]
        cash = data.get("cash", 0)
        inv = data.get("inventory", {})
        facs = data.get("factories", {})
        running = data.get("running_factories", {})

        lines: list[Text] = []

        # Cash — big and prominent
        cash_text = Text()
        cash_text.append(f" ${cash:,.2f}", style=f"bold {color}")
        lines.append(cash_text)
        lines.append(Text())

        # Inventory bars
        for comm in ["ore", "metal", "parts", "cars"]:
            qty = inv.get(comm, 0)
            line = Text()
            line.append(f" {comm:<6} ", style="dim")
            if qty > 0:
                bar_len = min(qty, 12)
                line.append("█" * bar_len, style=color)
                if qty > 12:
                    line.append("▸", style=color)
                line.append(f" {qty}", style="bold")
            else:
                line.append("·", style="grey30")
            lines.append(line)

        lines.append(Text())

        # Factory breakdown by type
        active = sum(running.values())
        total = sum(facs.values())
        fac_header = Text()
        fac_header.append(" Factories  ", style="dim")
        fac_header.append(str(active), style=f"bold {color}")
        fac_header.append(f"/{total} running", style="dim")
        lines.append(fac_header)
        for ft in ["metal", "part", "car"]:
            count = facs.get(ft, 0)
            run_count = running.get(ft, 0)
            ft_line = Text()
            ft_line.append(f"   {ft:<6}", style="dim")
            if count > 0:
                ft_line.append(f" {count}", style="bold")
                if run_count > 0:
                    ft_line.append(f" ({run_count} running)", style="dim")
            else:
                ft_line.append(" ·", style="grey30")
            lines.append(ft_line)

        return Panel(
            Group(*lines),
            title=f"[bold {color}]{s['name']}[/]",
            border_style=color,
            padding=(0, 0),
        )

    def _render_contracts(self) -> Panel:
        contracts = self._engine.get_contracts_snapshot()
        pane_height = max(3, self._console.height - 21)
        visible = contracts[:pane_height]

        if not visible:
            content = Text(" No contracts yet...", style="dim italic")
        else:
            lines: list[Text] = []
            for c in visible:
                sender = c.get("sender_id", "?")
                recipient = c.get("recipient_id", "?")
                s_style = FIRM_STYLES.get(sender, {})
                r_style = FIRM_STYLES.get(recipient, {})
                s_tag = s_style.get("tag", "?") if s_style else "?"
                r_tag = r_style.get("tag", "?") if r_style else "?"
                s_color = s_style.get("color", "grey70") if s_style else "grey70"

                status = c.get("status", "pending")
                st_color, st_icon = STATUS_STYLES.get(status, ("dim", "?"))

                commodity = c.get("commodity", "?")
                qty = c.get("quantity", 0)
                price = c.get("price_per_unit", 0)
                side = c.get("side", "?")

                line = Text()
                line.append(f" {st_icon} ", style=st_color)
                line.append(f"{s_tag}", style=f"bold {s_color}")
                line.append(f" {side} ", style="dim")
                line.append(f"{qty} {commodity}", style="bold")
                line.append(f" @${price:.2f}", style="dim")
                line.append(f" -> {r_tag}", style="dim")
                lines.append(line)
            content = Group(*lines)

        return Panel(
            content,
            title="[bold]Contracts[/]",
            border_style="#c678dd",
            padding=(0, 0),
        )

    def _render_messages(self) -> Panel:
        messages = self._engine.get_messages_snapshot()
        pane_height = max(3, self._console.height - 21)
        visible = messages[:pane_height]

        if not visible:
            content = Text(" No messages yet...", style="dim italic")
        else:
            lines: list[Text] = []
            for m in visible:
                sender = m.get("from", "?")
                recipient = m.get("to", "?")
                s_style = FIRM_STYLES.get(sender, {})
                r_style = FIRM_STYLES.get(recipient, {})
                s_tag = s_style.get("tag", "?") if s_style else "?"
                r_tag = r_style.get("tag", "?") if r_style else "?"
                s_color = s_style.get("color", "grey70") if s_style else "grey70"

                dt = datetime.fromtimestamp(m.get("timestamp", 0))
                msg_content = m.get("content", "")[:45]

                line = Text()
                line.append(f" {dt.strftime('%H:%M:%S')} ", style="grey50")
                line.append(f"{s_tag}", style=f"bold {s_color}")
                line.append(f"->{r_tag} ", style="dim")
                line.append(msg_content, style="")
                lines.append(line)
            content = Group(*lines)

        return Panel(
            content,
            title="[bold]Messages[/]",
            border_style="#61afef",
            padding=(0, 0),
        )

    def _render_factory_runs(self) -> Panel:
        jobs = self._engine.get_factory_jobs_snapshot()
        pane_height = max(3, self._console.height - 21)
        visible = jobs[:pane_height]

        if not visible:
            content = Text(" No active runs...", style="dim italic")
        else:
            lines: list[Text] = []
            for j in visible:
                firm_id = j.get("firm_id", "?")
                f_style = FIRM_STYLES.get(firm_id, {})
                f_tag = f_style.get("tag", "?") if f_style else "?"
                f_color = f_style.get("color", "grey70") if f_style else "grey70"

                factory_type = j.get("factory_type", "?")
                count = j.get("count", 0)
                secs_left = j.get("seconds_left", 0)

                # Progress bar
                total_duration = j.get("completes_at", 0) - j.get("started_at", 0)
                elapsed = total_duration - secs_left
                progress = elapsed / total_duration if total_duration > 0 else 0
                progress = max(0.0, min(1.0, progress))
                bar_width = 8
                filled = int(bar_width * progress)
                bar_str = "█" * filled + "░" * (bar_width - filled)

                line = Text()
                line.append(f" {f_tag} ", style=f"bold {f_color}")
                line.append(f"{count}x {factory_type:<5} ", style="bold")
                line.append(bar_str, style=f_color)
                line.append(f" {secs_left:.0f}s", style="dim")
                lines.append(line)
            content = Group(*lines)

        return Panel(
            content,
            title="[bold]Factory Runs[/]",
            border_style="#e5c07b",
            padding=(0, 0),
        )

    # --- Layout: Results Screen ---

    def _render_results_screen(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="top", size=6),
            Layout(name="results"),
            Layout(name="footer", size=3),
        )

        # Title
        title = Text(justify="center")
        title.append("D A E G U", style="bold bright_white")
        title.append("   Economy Simulator", style="dim")

        subtitle = Text(justify="center")
        subtitle.append("━━━ ", style="grey50")
        subtitle.append("GAME OVER", style="bold bright_white")
        subtitle.append(" ━━━", style="grey50")

        layout["top"].update(Panel(
            Group(Align.center(Text()), Align.center(title), Align.center(Text()), Align.center(subtitle)),
            border_style="bright_cyan",
            padding=(0, 1),
        ))

        # Scoreboard
        results = self._results or []
        max_cash = max((r["cash"] for r in results), default=1) or 1

        lines: list[Text] = [Text()]

        for i, r in enumerate(results):
            fid = r["firm_id"]
            s = FIRM_STYLES.get(fid, {"color": "white", "name": fid, "tag": "?"})
            color = s["color"]
            rank_label = RANK_LABELS[i] if i < len(RANK_LABELS) else f"#{i + 1}"
            rank_style = RANK_STYLES[i] if i < len(RANK_STYLES) else "dim"

            # Rank + name
            header = Text()
            header.append(f"     {rank_label}   ", style=rank_style)
            header.append(f"{s['name']}", style=f"bold {color}")
            lines.append(header)

            # Cash bar
            cash_line = Text()
            cash_line.append("            ")
            bar_width = 32
            bar_len = int(bar_width * r["cash"] / max_cash) if max_cash > 0 else 0
            cash_line.append("█" * bar_len, style=color)
            cash_line.append(f"  ${r['cash']:,.2f}", style=f"bold {color}")
            lines.append(cash_line)

            # Inventory
            inv = r.get("inventory", {})
            inv_line = Text()
            inv_line.append("            ")
            for comm in ["ore", "metal", "parts", "cars"]:
                qty = inv.get(comm, 0)
                inv_line.append(f"{comm} ", style="dim")
                inv_line.append(f"{qty}", style="bold" if qty > 0 else "dim")
                inv_line.append("  ", style="")
            lines.append(inv_line)

            # Factories
            facs = r.get("factories", {})
            fac_line = Text()
            fac_line.append("            factories  ", style="dim")
            for ft in ["metal", "part", "car"]:
                count = facs.get(ft, 0)
                fac_line.append(f"{ft} ", style="dim")
                fac_line.append(f"{count}", style="bold" if count > 0 else "dim")
                fac_line.append("  ", style="")
            lines.append(fac_line)

            lines.append(Text())

        layout["results"].update(Panel(
            Group(*lines),
            title="[bold]Final Standings[/]",
            border_style="grey50",
            padding=(1, 2),
        ))

        # Footer
        footer = Text(justify="center")
        footer.append("Exiting in a few seconds...", style="dim italic")
        layout["footer"].update(Align.center(footer, vertical="middle"))

        return layout

    # --- Lifecycle ---

    def _refresh(self) -> None:
        if self._live:
            self._live.update(self._render())

    async def run_refresh_loop(self) -> None:
        """Periodically refresh the display for the timer and progress bar."""
        try:
            while not self._game_over:
                self._refresh()
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass

    def show_results(self, results: list[dict]) -> None:
        """Switch display to the results scoreboard."""
        self._game_over = True
        self._results = results
        self._refresh()

    def start(self) -> None:
        """Enter full-screen mode and start the live display."""
        self._live = Live(
            self._render(),
            console=self._console,
            screen=True,
        )
        self._live.start()

    def stop(self) -> None:
        """Exit full-screen mode."""
        if self._live:
            self._live.stop()
            self._live = None

    def print_summary(self, results: list[dict]) -> None:
        """Print a compact summary to the normal terminal after the TUI exits."""
        c = self._console
        c.print()
        c.print("[bold bright_cyan]D A E G U[/]  [dim]Economy Simulator[/]  [bold]Final Results[/]")
        c.print()
        max_cash = max((r["cash"] for r in results), default=1) or 1
        for i, r in enumerate(results):
            s = FIRM_STYLES.get(r["firm_id"], {"color": "white", "name": r["firm_id"]})
            color = s["color"]
            rank = RANK_LABELS[i] if i < len(RANK_LABELS) else f"#{i + 1}"
            bar_len = int(20 * r["cash"] / max_cash)
            c.print(
                f"  {rank}  [{color}]{s['name']}[/]  "
                f"[{color}]{'█' * bar_len}[/]  "
                f"[bold]${r['cash']:,.2f}[/]"
            )
        c.print()
