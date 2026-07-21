"""View layer: Rich terminal presentation, status spinner, diff and confirmation UI."""

from __future__ import annotations

from difflib import unified_diff
from pathlib import Path

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.syntax import Syntax
from rich.table import Table

from .model import FailureEvidence


class TerminalView:
    def __init__(self, root: Path, assume_yes: bool):
        self.root = root
        self.assume_yes = assume_yes
        self.console = Console()
        self.status = self.console.status("[bold cyan]❖ [SYSTEM] Initializing diagnosis…[/bold cyan]", spinner="dots12")
        self.status_active = False
        self.print_header()

    def print_header(self) -> None:
        """Prints a high-tech header at application startup."""
        self.console.print(Panel(
            "[bold neon_cyan]⚡ ❖ HEALING NETWORK v1.1.0 ❖ ⚡[/bold neon_cyan]\n"
            "[dim cyan]AUTOMATED SOURCE DIAGNOSIS & NEURAL CODE PATCHING FACILITY[/dim cyan]",
            border_style="cyan",
            box=box.DOUBLE
        ))

    def notify(self, message: str) -> None:
        # Status updates
        self.status.update(f"[bold cyan]◈ [DIAGNOSTIC] {message}…[/bold cyan]")
        if not self.status_active:
            self.status.start()
            self.status_active = True

    def display_failure_evidence(self, evidence: FailureEvidence) -> None:
        self.stop()
        self.console.print()
        
        # High contrast diagnostic failure header
        self.console.print("[bold red]⚡ CRITICAL INTEGRITY EXCEPTION DETECTED ⚡[/bold red]")
        
        target_display = "Desconocido"
        if evidence.target_files:
            rel_files = []
            for t in evidence.target_files:
                try:
                    rel_files.append(str(t.relative_to(self.root.resolve())))
                except ValueError:
                    rel_files.append(str(t.name))
            target_display = ", ".join(rel_files)

        error_panel = Panel(
            f"[bold white]EXCEPTION METRIC:[/bold white] [bright_red]{evidence.error_message}[/bright_red]\n\n"
            f"[bold white]TARGET SOURCE COMPONENT(S):[/bold white] [yellow]{target_display}[/yellow]\n"
            f"[bold white]DIAGNOSTIC ENGINE:[/bold white] [magenta]PARSER v2.4 (Deep Frame Scanner)[/magenta]",
            title=f"[bold bright_red]❖ EXCEPTION LOG ({evidence.language.upper()}) ❖[/bold bright_red]",
            border_style="red",
            box=box.ROUNDED,
            expand=False
        )
        self.console.print(error_panel)

        # Telemetry frames table
        if evidence.frames:
            table = Table(
                title="[bold cyan]❖ TELEMETRY: PARSED STACK TRACE FRAMES ❖[/bold cyan]", 
                show_header=True, 
                header_style="bold magenta", 
                expand=True,
                box=box.MINIMAL_DOUBLE_HEAD
            )
            table.add_column("IDX", style="dim cyan", width=6)
            table.add_column("SOURCE MODULE", style="cyan")
            table.add_column("LINE", style="green", justify="right")
            table.add_column("SCOPE/FUNCTION", style="yellow")
            table.add_column("CONTEXT / CODE FRAME", style="white")

            for idx, frame in enumerate(evidence.frames):
                try:
                    rel_path = frame.file_path.relative_to(self.root.resolve())
                except ValueError:
                    rel_path = frame.file_path.name
                
                # Highlight the target files we will modify in high-contrast neon yellow
                if frame.file_path in evidence.target_files:
                    style = "bold yellow"
                    idx_str = f"◈ {idx + 1}"
                else:
                    style = "dim white"
                    idx_str = str(idx + 1)
                
                table.add_row(
                    idx_str,
                    f"[{style}]{rel_path}[/{style}]",
                    f"[{style}]{frame.line_number}[/{style}]",
                    f"[{style}]{frame.function_name or '-'}[/{style}]",
                    f"[{style}]{frame.code_line or '-'}[/{style}]"
                )
            self.console.print(table)
            self.console.print()

    def display_sandbox_fallback(self) -> None:
        self.stop()
        warning_panel = Panel(
            "[bold yellow]⚡ DOCKER ENGINE RETRIEVAL FAIL: CONTAINER ENVIRONMENT UNAVAILABLE[/bold yellow]\n"
            "Initiating automatic fallback process to [bold cyan]LocalSubprocessSandbox[/bold cyan] module...\n"
            "System isolation parameters: [bold green]ACTIVE[/bold green] (POSIX limits enforced)",
            title="❖ SECURE SANDBOX DEVIATION WARNING ❖",
            border_style="bold yellow",
            box=box.ROUNDED,
            expand=False
        )
        self.console.print(warning_panel)
        self.console.print()

    def approve(self, target: Path, original: str, proposed: str) -> bool:
        self.stop()
        try:
            rel_target = target.relative_to(self.root.resolve())
        except ValueError:
            rel_target = target.name

        self.console.print(Panel(
            f"[bold green]✔ PATCH COMPILED SUCCESSFULLY[/bold green]\n"
            f"COMPONENT: [cyan]{rel_target}[/cyan]\n"
            f"INTEGRITY AUDIT: [bold green]PASSED[/bold green] (Approved by Reviewer Agent)", 
            title="❖ PROPOSED SYSTEM RECONSTRUCTION ❖",
            border_style="green",
            box=box.ROUNDED
        ))
        
        diff = "\n".join(unified_diff(
            original.splitlines(), 
            proposed.splitlines(), 
            fromfile=f"original/{target.name}", 
            tofile=f"reconstructed/{target.name}", 
            lineterm=""
        ))
        
        # Display the diff inside a glowing panel
        diff_panel = Panel(
            Syntax(diff or "(sin cambios)", "diff", theme="monokai", line_numbers=True),
            border_style="bold green",
            title="[bold green]◈ GLOWING PATCH COMPARISON ◈[/bold green]",
            box=box.DOUBLE
        )
        self.console.print(diff_panel)
        self.console.print()
        
        return self.assume_yes or Confirm.ask("[bold cyan]▶ Authorize neural patch injection?[/bold cyan]", default=True, console=self.console)

    def ask_rollback(self) -> bool:
        self.stop()
        self.console.print()
        self.console.print("[bold yellow]◈ [SYSTEM] neural patch applied temporarily to working tree.[/bold yellow]")
        self.console.print("[dim yellow]Puedes realizar pruebas manuales en tu terminal/entorno ahora mismo.[/dim yellow]")
        keep = Confirm.ask(
            "[bold cyan]▶ ¿Deseas conservar los cambios aplicados? (Si seleccionas 'No', se realizará un ROLLBACK automático)[/bold cyan]", 
            default=True, 
            console=self.console
        )
        return keep

    def stop(self) -> None:
        if self.status_active:
            self.status.stop()
            self.status_active = False

    def display_roi(self, report: dict) -> None:
        self.stop()
        self.console.print()
        
        last_record = report["history"][-1] if report["history"] else {}
        ai_time = last_record.get("ai_time_seconds", 0.0)
        human_saved = last_record.get("human_time_saved_minutes", 0)
        total_saved_hours = report.get("total_man_hours_saved_minutes", 0) / 60.0
        
        roi_panel = Panel(
            f"[bold green]◈ CURRENT RUN DIAGNOSTICS:[/bold green]\n"
            f"  • AI Healing Cycle Duration : [cyan]{ai_time:.2f} seconds[/cyan]\n"
            f"  • Avoided Manual Debugging  : [yellow]{human_saved} minutes[/yellow]\n\n"
            f"[bold green]◈ TELEMETRY METRIC ACCUMULATED:[/bold green]\n"
            f"  • Total Operations Resolved : [cyan]{report.get('total_fixes', 0)} repairs[/cyan]\n"
            f"  • Total Man-Hours Preserved : [yellow]{total_saved_hours:.2f} engineering hours[/yellow]",
            title="[bold green]❖ TELEMETRY LOG: ROI & METRICS DASHBOARD ❖[/bold green]",
            border_style="bold green",
            box=box.DOUBLE,
            expand=False
        )
        self.console.print(roi_panel)
        self.console.print()

    def success(self, message: str) -> None:
        self.stop()
        self.console.print(Panel(
            f"[bold green]🚀 PATCH INJECTED & VALIDATED IN ISOLATION WORKSPACE[/bold green]\n"
            f"[white]{message}[/white]",
            title="❖ HEALING PROCESS SUCCESSFUL ❖",
            border_style="bold green",
            box=box.DOUBLE
        ))

    def error(self, message: str) -> None:
        self.stop()
        self.console.print(Panel(
            f"[bold red]⚡ HEALING PROCESS TERMINATED PREMATURELY[/bold red]\n"
            f"[white]Reason: {message}[/white]",
            title="❖ SYSTEM RECONSTRUCTION ERROR ❖",
            border_style="bold red",
            box=box.DOUBLE
        ))
