"""View layer: Rich terminal presentation, status spinner, diff and confirmation."""

from __future__ import annotations

from difflib import unified_diff
from pathlib import Path

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
        self.status = self.console.status("[bold cyan]🧠 Preparando agente…[/bold cyan]", spinner="dots12")
        self.status_active = False

    def notify(self, message: str) -> None:
        self.status.update(f"[bold cyan]🧠 {message}[/bold cyan]")
        if not self.status_active:
            self.status.start()
            self.status_active = True

    def display_failure_evidence(self, evidence: FailureEvidence) -> None:
        self.stop()
        self.console.print()
        
        # Error summary panel
        title = f"[bold red]❌ Test Fallido ({evidence.language.upper()})[/bold red]"
        target_display = "Desconocido"
        if evidence.target_file:
            try:
                target_display = str(evidence.target_file.relative_to(self.root.resolve()))
            except ValueError:
                target_display = str(evidence.target_file.name)

        error_panel = Panel(
            f"[bold white]Mensaje de error:[/bold white]\n[red]{evidence.error_message}[/red]\n\n"
            f"[bold white]Archivo fuente sugerido:[/bold white]\n"
            f"[yellow]{target_display}[/yellow]",
            title=title,
            border_style="red",
            expand=False
        )
        self.console.print(error_panel)

        # Stack trace table
        if evidence.frames:
            table = Table(title="[bold dim]Stack Trace Detectado[/bold dim]", show_header=True, header_style="bold magenta", expand=True)
            table.add_column("#", style="dim", width=4)
            table.add_column("Archivo", style="cyan")
            table.add_column("Línea", style="green", justify="right")
            table.add_column("Función", style="yellow")
            table.add_column("Código", style="white")

            for idx, frame in enumerate(evidence.frames):
                try:
                    rel_path = frame.file_path.relative_to(self.root.resolve())
                except ValueError:
                    rel_path = frame.file_path.name
                
                # Highlight the target file we will modify
                style = "bold yellow" if frame.file_path == evidence.target_file else ""
                
                table.add_row(
                    str(idx + 1),
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
            "[bold yellow]⚠️ Docker no disponible o no corriendo.[/bold yellow]\n"
            "Realizando fallback automático a [bold cyan]LocalSubprocessSandbox[/bold cyan] seguro\n"
            "con límites de recursos POSIX (`resource.setrlimit`).",
            title="[bold yellow]Sandbox Fallback[/bold yellow]",
            border_style="yellow",
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

        self.console.print(Panel.fit(
            f"[bold green]✓ Reviewer Agent aprobó la propuesta[/bold green]\n"
            f"Archivo: [cyan]{rel_target}[/cyan]", 
            title="Propuesta Lista para Aplicar",
            border_style="green"
        ))
        
        diff = "\n".join(unified_diff(
            original.splitlines(), 
            proposed.splitlines(), 
            fromfile=f"original/{target.name}", 
            tofile=f"propuesta/{target.name}", 
            lineterm=""
        ))
        self.console.print(Syntax(diff or "(sin cambios)", "diff", theme="monokai", line_numbers=True))
        self.console.print()
        
        return self.assume_yes or Confirm.ask("¿Aplicar este parche?", default=True, console=self.console)

    def stop(self) -> None:
        if self.status_active:
            self.status.stop()
            self.status_active = False

    def success(self, message: str) -> None:
        self.stop()
        self.console.print(Panel(f"[bold green]🎉 Éxito:[/bold green] {message}", border_style="green"))

    def error(self, message: str) -> None:
        self.stop()
        self.console.print(Panel(f"[bold red]⛔ Reparación detenida:[/bold red]\n{message}", border_style="red"))
