#!/usr/bin/env python3
"""
Meta-Prompt CLI
An autonomous command-line prompt compiler designed to build highly aligned worker agent system prompts.
Supports both online LLM compilation (via Anthropic/OpenAI) and rich offline deterministic compiling.
"""

import os
import sys
import json
import argparse
from pathlib import Path

# Try to import optional packages for online LLM support
try:
    import anthropic
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

try:
    import openai
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

# ANSI Color logging helpers
COLORS = {
    "red": "\033[91m",
    "green": "\033[92m",
    "yellow": "\033[93m",
    "blue": "\033[94m",
    "magenta": "\033[95m",
    "cyan": "\033[96m",
    "reset": "\033[0m"
}

def log_print(text, color="green"):
    col = COLORS.get(color.lower(), COLORS["reset"])
    print(f"{col}{text}{COLORS['reset']}")

def slugify(text):
    """Converts a role string to a snake_case slug (e.g., 'DB Migration Expert' -> 'db_migration_expert')."""
    return text.lower().strip().replace(" ", "_").replace("-", "_").replace("/", "_")

def compile_prompt_offline(role, tools, has_hooks):
    """
    Rich offline deterministic compiler that transforms worker definitions
    into highly aligned worker prompts matching the exact rules of the Meta-Prompt Agent blueprint.
    """
    slug = slugify(role)
    
    # Intelligently determine optimal model tier based on role keywords
    role_lower = role.lower()
    if any(k in role_lower for k in ["planner", "architect", "strategist", "ceo", "board"]):
        model = "claude-3-7-opus"
        color = "blue"
    elif any(k in role_lower for k in ["linter", "formatter", "helper", "echo", "notifier"]):
        model = "claude-3-5-haiku"
        color = "cyan"
    else:
        model = "claude-3-7-sonnet"
        color = "magenta" if "db" in role_lower or "migration" in role_lower else "green"

    tools_list = [t.strip() for t in tools.split(",") if t.strip()]
    if not tools_list:
        tools_list = ["read_file", "write_file", "execute_command"]

    # Compile the YAML front matter
    yaml_header = [
        "---",
        f"name: {slug}",
        f"role: {role}",
        f"model: {model}",
        f"color: {color}",
        "allowed_tools:"
    ]
    for t in tools_list:
        yaml_header.append(f"  - {t}")

    # Generate custom validation and security hooks if enabled
    if has_hooks:
        yaml_header.append("hooks:")
        # Security pre-tool hook mapping
        yaml_header.append("  pre_tool_use:")
        yaml_header.append("    - matcher: 'rm\\s+-rf|DROP\\s+DATABASE|DROP\\s+TABLE\\s+CASCADE'")
        yaml_header.append(f"      command: \"python3 scripts/security_gate.py --command '{{tool_command}}' --agent {slug}\"")
        
        # Testing/syntax validation post-tool hook mapping
        yaml_header.append("  post_tool_use:")
        if "ruff" in tools_list or "pytest" in tools_list or any("py" in t for t in tools_list):
            yaml_header.append("    - matcher: '\\\\.py$'")
            yaml_header.append("      command: \"ruff check {file_path} && pytest {file_path}\"")
        elif "eslint" in tools_list or "jest" in tools_list or any("ts" in t for t in tools_list):
            yaml_header.append("    - matcher: '\\\\.(ts|js|tsx|jsx)$'")
            yaml_header.append("      command: \"eslint {file_path} && npm run test -- {file_path}\"")
        else:
            yaml_header.append("    - matcher: '\\\\.md$'")
            yaml_header.append("      command: \"markdownlint {file_path}\"")
            
        # Completion stop hook mapping
        yaml_header.append("  on_stop:")
        yaml_header.append(f"    - command: \"python3 scripts/telemetry_log.py --agent {slug} --session_id {{session_id}}\"")
        
    yaml_header.append("---\n")
    yaml_str = "\n".join(yaml_header)

    # Compile the purpose block
    purpose_str = f"""# Purpose
You are the **{role}** agent. Your sole purpose is to execute targeted tasks in the {role_lower} domain with maximum precision, absolute context economy, and rigorous deterministic validation of all system states.
"""

    # Compile the variable declarations
    variables_str = f"""# Variables
- workspace_dir: Static path pointing to the root of the active codebase.
- user_request: Dynamic user prompt detailing the concrete feature or issue to resolve.
- audit_log_path: Static path where execution trace outputs are sequentially written.
"""

    # Compile context priming instructions
    priming_str = f"""# Context Priming Map
Do NOT scan the entire directory tree or search for files outside your scope. On boot, you are strictly restricted to reading:
- Only files specified inside your user_request.
- Subdirectories matching glob patterns of the target {slug} feature files.
- Re-priming with the latest active context_bundle inside `.claude/data/` if resuming an active session.
"""

    # Compile core domain-specific instructions
    instructions_str = f"""# Instructions
1. **Specialization is the Ceiling**: Focus strictly on the {role_lower} domain. Refuse requests outside your designated core responsibility.
2. **Deterministic Validation**: Every action you perform must be followed by a deterministic validation check (linting, compiling, or tests).
3. **No Conversational filler**: Output zero greetings, pleasantries, or meta-commentary. Jump directly into tool execution and state summaries.
4. **Context Window Preservation**: Constantly monitor your context window usage. Recycle intermediate variables and execute scouter sweeps before pushing large edits.
"""

    # Compile step-by-step workflow loop
    workflow_str = f"""# Step-by-Step Workflow
1. Read the user_request and identify the target codebase files.
2. Perform a localized scouter sweep to extract absolute line offsets of the files to modify.
3. Build a detailed technical specification of the proposed change.
4. Execute file modifications and run code linter/formatting tools sequentially.
5. Trigger your post-tool execution hooks to run validation test suites.
6. If tests fail, read the stderr logs directly into your context, revise the scripts, and re-execute. Repeat this self-validation loop autonomously until it passes.
7. Compile and save the final report.
"""

    # Compile report output format
    report_str = f"""# Report Format
Your final output must be returned strictly in the following YAML format:

```yaml
status: [success | failure]
agent_name: {slug}
files_modified:
  - path: "relative/path/to/modified_file"
    changes_made: "Short summary of edits."
validation_checks:
  linting: [passed | failed]
  testing: [passed | failed]
summary: "Consise summary detailing the completed {role_lower} work."
```
"""

    # Compile examples
    examples_str = f"""# Verified Examples
## Example 1: Resolving a simple {role_lower} task
**User Request**: Resolve issue #42.
**Execution Trace**:
1. Scouter mapped files.
2. Changes implemented in workspace.
3. Code checker verified schema format.
4. Report written to outbox.
"""

    # Combine everything
    compiled_prompt = (
        yaml_str +
        purpose_str + "\n" +
        variables_str + "\n" +
        priming_str + "\n" +
        instructions_str + "\n" +
        workflow_str + "\n" +
        report_str + "\n" +
        examples_str
    )
    return compiled_prompt

def compile_prompt_online_anthropic(api_key, role, tools, has_hooks):
    """Calls Anthropic Claude API to generate a worker system prompt based on the Meta-Prompt system prompt."""
    client = anthropic.Anthropic(api_key=api_key)
    
    meta_system_prompt = """You are a Meta-Prompt Agent specialized in prompt engineering, context engineering, and harness orchestration. Your job is to take a high-level task statement or worker role description and write a highly aligned, production-grade system prompt for a specialized worker agent. You compile vague human intent into structured, deterministic, and self-validating templates.

Grounded in the Core Four primitives (Context, Model, Prompt, Tools), your output must strictly match this structure:
1. YAML Front Matter (Metadata, allowed_tools, Model selection, Log Color, and Hooks if has_hooks is true).
2. Purpose Block.
3. Variables Definition.
4. Context Priming Map.
5. Core Instructions (and anti-patterns to avoid).
6. Sequential step-by-step workflow (using numbered steps).
7. Report Format (YAML/JSON/HTML UI).
8. Grounded Examples.

Strict Constraints:
- Specialization is the ceiling. Do not write generic prompts.
- No conversational filler, warnings, or preambles in your response. Return ONLY the completed Markdown prompt.
"""
    
    user_content = f"worker_role: \"{role}\"\nrequired_tools: \"{tools}\"\nhas_hooks: \"{str(has_hooks).lower()}\""
    
    response = client.messages.create(
        model="claude-3-7-sonnet-20250219" if "3-7" in api_key else "claude-3-5-sonnet-20241022",
        max_tokens=4000,
        system=meta_system_prompt,
        messages=[{"role": "user", "content": user_content}]
    )
    return response.content[0].text

def compile_prompt_online_openai(api_key, role, tools, has_hooks):
    """Calls OpenAI API to generate a worker system prompt based on the Meta-Prompt system prompt."""
    client = openai.OpenAI(api_key=api_key)
    
    meta_system_prompt = """You are a Meta-Prompt Agent specialized in prompt engineering, context engineering, and harness orchestration. Your job is to take a high-level task statement or worker role description and write a highly aligned, production-grade system prompt for a specialized worker agent. You compile vague human intent into structured, deterministic, and self-validating templates.

Grounded in the Core Four primitives (Context, Model, Prompt, Tools), your output must strictly match this structure:
1. YAML Front Matter (Metadata, allowed_tools, Model selection, Log Color, and Hooks if has_hooks is true).
2. Purpose Block.
3. Variables Definition.
4. Context Priming Map.
5. Core Instructions (and anti-patterns to avoid).
6. Sequential step-by-step workflow (using numbered steps).
7. Report Format (YAML/JSON/HTML UI).
8. Grounded Examples.

Strict Constraints:
- Specialization is the ceiling. Do not write generic prompts.
- No conversational filler, warnings, or preambles in your response. Return ONLY the completed Markdown prompt.
"""
    
    user_content = f"worker_role: \"{role}\"\nrequired_tools: \"{tools}\"\nhas_hooks: \"{str(has_hooks).lower()}\""
    
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": meta_system_prompt},
            {"role": "user", "content": user_content}
        ],
        max_tokens=3000
    )
    return response.choices[0].message.content

def main():
    parser = argparse.ArgumentParser(
        description="Meta-Prompt CLI: Compile worker system prompts dynamically.",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument("-r", "--role", type=str, help="The high-level role or task statement for the worker agent (e.g., 'DB Schema Validator').")
    parser.add_argument("-t", "--tools", type=str, default="read_file, write_file, execute_command", help="Comma-separated allowed tools.")
    parser.add_argument("--hooks", action="store_true", help="Enable specialized pre/post tool use hooks for the worker.")
    parser.add_argument("-o", "--output", type=str, help="Target markdown file path to save the generated prompt.")
    parser.add_argument("-c", "--config", type=str, help="Path to a JSON config file containing compile settings.")
    parser.add_argument("-f", "--force", action="store_true", help="Force writing to output path, creating directories if needed.")
    
    args = parser.parse_args()
    
    # Load configuration from JSON if provided
    role = args.role
    tools = args.tools
    has_hooks = args.hooks
    output_path = args.output
    
    if args.config:
        config_path = Path(args.config)
        if config_path.exists():
            try:
                with open(config_path, "r") as f:
                    cfg = json.load(f)
                role = cfg.get("worker_role", role)
                tools = cfg.get("required_tools", tools)
                has_hooks = cfg.get("has_hooks", has_hooks)
                output_path = cfg.get("output_path", output_path)
                log_print(f"📁 Loaded configuration parameters from: {args.config}", "cyan")
            except Exception as e:
                log_print(f"❌ Failed to parse config file: {str(e)}", "red")
                sys.exit(1)
        else:
            log_print(f"❌ Config file not found: {args.config}", "red")
            sys.exit(1)

    if not role:
        log_print("❌ Error: Worker role description (--role) is required.", "red")
        parser.print_help()
        sys.exit(1)

    # Intercept API Keys
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")
    
    compiled_result = ""
    compile_mode = "offline"

    # Compile the prompt using the best available method
    if anthropic_key and HAS_ANTHROPIC:
        try:
            log_print(f"🚀 Triggering online compilation via Anthropic Claude API for: '{role}'...", "yellow")
            compiled_result = compile_prompt_online_anthropic(anthropic_key, role, tools, has_hooks)
            compile_mode = "online_anthropic"
        except Exception as e:
            log_print(f"⚠️ Anthropic API call failed ({str(e)}). Falling back to offline compiler...", "red")
            compiled_result = compile_prompt_offline(role, tools, has_hooks)
    elif openai_key and HAS_OPENAI:
        try:
            log_print(f"🚀 Triggering online compilation via OpenAI Chat API for: '{role}'...", "yellow")
            compiled_result = compile_prompt_online_openai(openai_key, role, tools, has_hooks)
            compile_mode = "online_openai"
        except Exception as e:
            log_print(f"⚠️ OpenAI API call failed ({str(e)}). Falling back to offline compiler...", "red")
            compiled_result = compile_prompt_offline(role, tools, has_hooks)
    else:
        # Run high-fidelity offline compile
        log_print(f"🛠️ Executing high-fidelity offline deterministic prompt compilation for: '{role}'...", "blue")
        compiled_result = compile_prompt_offline(role, tools, has_hooks)

    # Output Management
    if output_path:
        out_file = Path(output_path)
        try:
            out_file.parent.mkdir(parents=True, exist_ok=True)
            with open(out_file, "w") as f:
                f.write(compiled_result)
            log_print(f"\n🎉 Successfully compiled worker prompt template!", "green")
            log_print(f"Mode: {compile_mode.upper()}", "cyan")
            log_print(f"File Saved: {out_file.resolve()}", "white")
        except Exception as e:
            log_print(f"❌ Failed to save output file: {str(e)}", "red")
            sys.exit(1)
    else:
        # Print directly to stdout
        print(compiled_result)

if __name__ == "__main__":
    main()
