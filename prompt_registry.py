"""
prompt_registry.py — The Prompt Registry

A centralized, file-backed store of reusable prompt templates, mirroring
the .gemini/commands/ slash-command pattern. Each template is a markdown
file with optional YAML front matter (metadata + an optional system
instruction override) and {{variable}} placeholders in the body.

Maps to the three concepts from the lesson:
  1. Dynamic Variable Interpolation -> PromptRegistry.render()
  2. Higher-Order Prompts (Hops)    -> render() accepts a CompiledPrompt
                                        as a variable value, so one
                                        template can be nested inside
                                        another
  3. System Prompt Overwriting      -> front matter's `system_instruction`
                                        travels with the compiled prompt
                                        and gets passed straight to
                                        interactions.create()
"""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml  # uv add pyyaml

COMMANDS_DIR = Path(__file__).parent / "commands"
VAR_PATTERN = re.compile(r"\{\{(\w+)\}\}")


@dataclass
class CompiledPrompt:
    body: str
    system_instruction: str | None
    description: str | None
    source_name: str


class PromptRegistry:
    def __init__(self, commands_dir: Path = COMMANDS_DIR):
        self.commands_dir = commands_dir

    def _load_raw(self, name: str) -> tuple[dict, str]:
        path = self.commands_dir / f"{name}.md"
        if not path.exists():
            raise FileNotFoundError(f"No prompt template named '{name}' in {self.commands_dir}")
        text = path.read_text()

        if text.startswith("---"):
            _, front_matter, body = text.split("---", 2)
            metadata = yaml.safe_load(front_matter) or {}
        else:
            metadata, body = {}, text

        return metadata, body.strip()

    def render(self, name: str, **variables) -> CompiledPrompt:
        """Compile a template by name, substituting {{var}} placeholders.

        A value can be a plain string OR another CompiledPrompt — passing
        a CompiledPrompt in is what makes this a Higher-Order Prompt: the
        inner template's *body* gets spliced into the outer one, but its
        own system_instruction is discarded (the outer template's wins,
        since only one system_instruction can be sent per call).
        """
        metadata, body = self._load_raw(name)

        def substitute(match: re.Match) -> str:
            key = match.group(1)
            if key not in variables:
                raise KeyError(f"Template '{name}' requires variable '{{{{{key}}}}}' but it wasn't provided")
            value = variables[key]
            return value.body if isinstance(value, CompiledPrompt) else str(value)

        compiled_body = VAR_PATTERN.sub(substitute, body)

        return CompiledPrompt(
            body=compiled_body,
            system_instruction=metadata.get("system_instruction"),
            description=metadata.get("description"),
            source_name=name,
        )

    def list_templates(self) -> list[str]:
        """Return names of all available markdown prompt templates."""
        if not self.commands_dir.exists():
            return []
        return sorted(p.stem for p in self.commands_dir.glob("*.md"))


if __name__ == "__main__":
    import sys

    registry = PromptRegistry()

    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        templates = registry.list_templates()
        print("Usage: python prompt_registry.py <template_name> [var=value ...]\n")
        if templates:
            print("Available templates:")
            for name in templates:
                print(f"  - {name}")
        else:
            print(f"No templates found in {registry.commands_dir}")
        sys.exit(0 if len(sys.argv) >= 2 and sys.argv[1] in ("-h", "--help") else 1)

    template_name = sys.argv[1]
    kwargs = {}
    for arg in sys.argv[2:]:
        if "=" in arg:
            k, v = arg.split("=", 1)
            kwargs[k] = v
        else:
            print(f"Warning: ignoring invalid argument '{arg}' (expected key=value)", file=sys.stderr)

    try:
        compiled = registry.render(template_name, **kwargs)
        print(compiled.body)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)