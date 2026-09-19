"""
Prompt Architect - AI-powered prompt generation and optimization

Features:
- Task analysis with Google Search grounding
- Optimized prompt generation
- Quality review
- Prompt versioning & caching (local + Gemini context caching)
- Variable substitution
- Error handling with graceful degradation
"""

from google import genai
from typing import Dict, List, Optional, Any
from enum import Enum
from dataclasses import dataclass, asdict, field
import json
import hashlib
from pathlib import Path
from datetime import datetime


class PromptLevel(str, Enum):
    """Complexity levels for prompts."""
    SIMPLE = "simple"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    EXPERT = "expert"


@dataclass
class GroundingSource:
    """Source from Google Search grounding."""
    title: str
    uri: str


@dataclass
class PromptVariable:
    """Variable definition for a prompt."""
    name: str
    variable_type: str
    description: str
    required: bool
    default: Optional[str] = None


@dataclass
class WorkflowStep:
    """Step in a workflow."""
    step_number: int
    title: str
    description: str


@dataclass
class PromptMetadata:
    """Metadata about the generated prompt."""
    recommended_model: str
    temperature: float
    thinking_budget: Optional[int] = None
    cached_content_token_count: Optional[int] = None


@dataclass
class PromptAnalysis:
    """Analysis of a task for prompt generation."""
    task_summary: str
    identified_requirements: List[str]
    recommended_level: str
    recommended_target_model: str
    suggested_tools: List[str]
    complexity_factors: List[str]
    potential_challenges: List[str]
    grounding_sources: Optional[List[GroundingSource]] = None
    cache_key: Optional[str] = None


@dataclass
class GeneratedPrompt:
    """Complete generated prompt."""
    title: str
    level: str
    purpose: str
    variables: List[PromptVariable]
    workflow: List[WorkflowStep]
    design_rationale: str
    rendered_prompt: str
    metadata: PromptMetadata
    cache_key: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def render(self, values: Optional[Dict[str, Any]] = None) -> str:
        """
        Render the prompt with variable substitution.

        Args:
            values: Dict of variable names to values

        Returns:
            Rendered prompt with variables substituted

        Raises:
            ValueError: If required variables are missing
        """
        if not values:
            values = {}

        required_vars = [v.name for v in self.variables if v.required]
        missing = [v for v in required_vars if v not in values]

        if missing:
            raise ValueError(f"Missing required variables: {', '.join(missing)}")

        for var in self.variables:
            if var.name not in values and var.default is not None:
                values[var.name] = var.default

        rendered = self.rendered_prompt
        for name, value in values.items():
            rendered = rendered.replace(f"{{{{{name}}}}}", str(value))
            rendered = rendered.replace(f"${{{name}}}", str(value))
            rendered = rendered.replace(f"[{name}]", str(value))

        return rendered


class PromptCache:
    """Local JSON-based cache for analysis and prompts."""

    def __init__(self, cache_dir: str = "./.prompt_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.analysis_dir = self.cache_dir / "analysis"
        self.prompt_dir = self.cache_dir / "prompts"
        self.analysis_dir.mkdir(exist_ok=True)
        self.prompt_dir.mkdir(exist_ok=True)

    def _get_key(self, *args) -> str:
        return hashlib.md5("|".join(map(str, args)).encode()).hexdigest()

    def get_analysis(self, query: str, complexity: Optional[str]) -> Optional[Dict]:
        key = self._get_key(query, complexity)
        path = self.analysis_dir / f"{key}.json"
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    data['cache_key'] = key
                    return data
            except Exception:
                return None
        return None

    def save_analysis(self, query: str, complexity: Optional[str], data: Dict):
        key = self._get_key(query, complexity)
        path = self.analysis_dir / f"{key}.json"
        data['cached_at'] = datetime.now().isoformat()
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

    def get_prompt(self, query: str, analysis_key: str, model: str, include_examples: bool) -> Optional[Dict]:
        key = self._get_key(query, analysis_key, model, include_examples)
        path = self.prompt_dir / f"{key}.json"
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    data['cache_key'] = key
                    return data
            except Exception:
                return None
        return None

    def save_prompt(self, query: str, analysis_key: str, model: str, include_examples: bool, data: Dict):
        key = self._get_key(query, analysis_key, model, include_examples)
        path = self.prompt_dir / f"{key}.json"
        data['created_at'] = datetime.now().isoformat()
        data['cache_key'] = key
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)


class PromptArchitect:
    """
    AI-powered prompt architect using Gemini 3 Pro.

    Features:
    - Task analysis with Google Search grounding
    - Optimized prompt generation
    - Quality review
    - Local caching to avoid redundant API calls
    - Gemini context caching for large prompts (saving ~75% cost)
    - Variable substitution
    - Graceful error handling
    """

    PRO_MODEL = 'gemini-3.1-pro-preview'
    FLASH_MODEL = 'gemini-3.6-flash'

    def __init__(
        self,
        client: Optional[genai.Client] = None,
        api_key: Optional[str] = None,
        use_cache: bool = True,
        cache_dir: str = "./.prompt_cache",
        use_gemini_cache: bool = True
    ):
        """
        Initialize prompt architect.

        Args:
            client:           Optional existing GenAI client
            api_key:          Optional API key (if client not provided)
            use_cache:        Enable local caching
            cache_dir:        Directory for cache files
            use_gemini_cache: Enable Gemini's native context caching
        """
        if client:
            self.client = client
        else:
            self.client = genai.Client(api_key=api_key) if api_key else genai.Client()

        self.use_cache = use_cache
        self.cache = PromptCache(cache_dir) if use_cache else None
        # Explicit context caching is not part of the Interactions API (token reuse
        # is handled implicitly server-side). The flag is retained for backwards
        # compatibility but no longer manages an explicit cache object.
        self.use_gemini_cache = use_gemini_cache

    def _generation_config(self, level: str) -> Dict[str, Any]:
        """
        Build the Interactions API ``generation_config`` dict for a reasoning level.

        Maps the LOW/MEDIUM/HIGH labels onto the API's ``thinking_level`` string
        ("minimal", "low", "medium", "high").
        """
        normalized = (level or "HIGH").strip().lower()
        if normalized not in {"minimal", "low", "medium", "high"}:
            normalized = "high"
        return {"thinking_level": normalized}

    def analyze_task(
        self,
        query: str,
        complexity_hint: Optional[str] = None,
        use_cached: bool = True,
        thinking_level: str = "HIGH",
    ) -> PromptAnalysis:
        """
        Analyze a task to determine requirements and complexity.

        Args:
            query:           The task or agent role description to analyze.
            complexity_hint: Optional hint about complexity ('simple', 'advanced', etc.)
            use_cached:      Return cached result if available.
            thinking_level:  Reasoning depth — 'LOW', 'MEDIUM', or 'HIGH'.
                             Use 'LOW' when calling from spawn_agent for speed.
        """
        # Check cache first
        if use_cached and self.cache:
            cached = self.cache.get_analysis(query, complexity_hint)
            if cached:
                print("💾 Using cached analysis")
                return PromptAnalysis(**{
                    k: v for k, v in cached.items()
                    if k != 'cached_at' and k != 'cache_key'
                })

        is_auto = not complexity_hint or complexity_hint == 'auto'

        analysis_prompt = f"""Analyze the following task description for prompt generation:

TASK DESCRIPTION:
{query}

USER PREFERENCE: {"AI-determined" if is_auto else complexity_hint}

RESEARCH REQUIREMENT:
If this task involves specific technologies (e.g., Next.js, Tailwind, specialized databases,
APIs, or modern coding patterns), use Google Search to find the latest official documentation
or best practices (2024-2025).

Provide a thorough analysis according to the schema.
Also recommend which Gemini model this specific prompt should be optimized for
(e.g., 'gemini-3.6-flash' for speed/simple logic or 'gemini-3.1-pro-preview'
for deep reasoning/complex research)."""

        analysis_schema = {
            "type": "object",
            "properties": {
                "task_summary": {"type": "string"},
                "identified_requirements": {"type": "array", "items": {"type": "string"}},
                "recommended_level": {
                    "type": "string",
                    "enum": [level.value for level in PromptLevel],
                },
                "recommended_target_model": {
                    "type": "string",
                    "description": "The specific model name",
                },
                "suggested_tools": {"type": "array", "items": {"type": "string"}},
                "complexity_factors": {"type": "array", "items": {"type": "string"}},
                "potential_challenges": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "task_summary", "identified_requirements", "recommended_level",
                "recommended_target_model", "complexity_factors", "potential_challenges",
            ],
        }

        try:
            interaction = self.client.interactions.create(
                model=self.PRO_MODEL,
                input=analysis_prompt,
                generation_config=self._generation_config(thinking_level),
                tools=[{"type": "google_search"}],
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": analysis_schema,
                },
            )

            analysis_data = json.loads(interaction.output_text)

            # Extract grounding sources from url_citation annotations on output text.
            grounding_sources = []
            seen_uris = set()
            for step in interaction.steps:
                if step.type != "model_output":
                    continue
                for block in (step.content or []):
                    for annotation in (getattr(block, "annotations", None) or []):
                        if getattr(annotation, "type", None) != "url_citation":
                            continue
                        uri = getattr(annotation, "url", None)
                        if uri and uri not in seen_uris:
                            seen_uris.add(uri)
                            grounding_sources.append(
                                GroundingSource(
                                    title=getattr(annotation, "title", None) or uri,
                                    uri=uri,
                                )
                            )

            analysis = PromptAnalysis(
                **analysis_data,
                grounding_sources=grounding_sources if grounding_sources else None
            )

            if self.cache:
                self.cache.save_analysis(query, complexity_hint, asdict(analysis))

            return analysis

        except Exception as e:
            print(f"⚠️  Analysis error: {e}")
            return PromptAnalysis(
                task_summary=query,
                identified_requirements=[query],
                recommended_level="intermediate",
                recommended_target_model="gemini-3.6-flash",
                suggested_tools=[],
                complexity_factors=["Error during analysis"],
                potential_challenges=[str(e)]
            )

    def generate_prompt(
        self,
        query: str,
        analysis: PromptAnalysis,
        target_model: str = "auto",
        include_examples: bool = True,
        use_cached: bool = True,
        thinking_level: str = "HIGH",
    ) -> GeneratedPrompt:
        """
        Generate an optimized prompt using Gemini context caching where appropriate.

        Args:
            query:           Original task or agent role description.
            analysis:        Result from analyze_task().
            target_model:    Override the model recommended by analysis.
            include_examples: Whether to ask for usage examples in the prompt.
            use_cached:      Return cached result if available.
            thinking_level:  Reasoning depth — 'LOW', 'MEDIUM', or 'HIGH'.
                             Use 'LOW' when calling from spawn_agent for speed.
        """
        effective_model = (
            analysis.recommended_target_model
            if target_model == 'auto'
            else target_model
        )

        # Check cache
        if use_cached and self.cache:
            cached = self.cache.get_prompt(
                query,
                analysis.cache_key or "",
                effective_model,
                include_examples
            )
            if cached:
                print("💾 Using cached prompt")
                return GeneratedPrompt(
                    title=cached["title"],
                    level=cached["level"],
                    purpose=cached["purpose"],
                    variables=[PromptVariable(**v) for v in cached["variables"]],
                    workflow=[WorkflowStep(**s) for s in cached["workflow"]],
                    design_rationale=cached["design_rationale"],
                    rendered_prompt=cached["rendered_prompt"],
                    metadata=PromptMetadata(**cached["metadata"]),
                    cache_key=cached.get("cache_key"),
                    created_at=cached.get("created_at", datetime.now().isoformat())
                )

        # Build research context — inlined directly into the prompt. (The
        # Interactions API reuses repeated tokens via implicit caching, so there
        # is no explicit cache object to create for large research blocks.)
        research_context = ""
        if analysis.grounding_sources:
            research_context = "RESEARCH DATA:\n"
            for source in analysis.grounding_sources:
                research_context += f"- {source.title}\n  {source.uri}\n"

        generation_prompt = f"""Generate an optimized agentic prompt based on this analysis:

ORIGINAL TASK:
{query}

ANALYSIS RESULTS:
{json.dumps(asdict(analysis), indent=2, default=str)}

{research_context}

GENERATION REQUIREMENTS:
- Target Model: {effective_model}
- Include Examples: {include_examples}
- Complexity Level: {analysis.recommended_level}
- Optimize for: Clarity, completeness, robustness
- Variables format: {{{{variable_name}}}}

If research data is provided above, use it to ensure technical accuracy."""

        try:
            interaction = self.client.interactions.create(
                model=self.PRO_MODEL,
                input=generation_prompt,
                generation_config=self._generation_config(thinking_level),
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": self._get_prompt_schema(),
                },
            )

            prompt_data = json.loads(interaction.output_text)

            cached_tokens = None
            usage = getattr(interaction, "usage", None)
            if usage is not None:
                cached_tokens = getattr(usage, "cached_content_token_count", None) or getattr(usage, "cached_tokens", None)

            prompt = GeneratedPrompt(
                title=prompt_data["title"],
                level=prompt_data["level"],
                purpose=prompt_data["purpose"],
                variables=[PromptVariable(**var) for var in prompt_data["variables"]],
                workflow=[WorkflowStep(**step) for step in prompt_data["workflow"]],
                design_rationale=prompt_data["design_rationale"],
                rendered_prompt=prompt_data["rendered_prompt"],
                metadata=PromptMetadata(
                    **prompt_data["metadata"],
                    cached_content_token_count=cached_tokens
                )
            )

            if self.cache:
                self.cache.save_prompt(
                    query,
                    analysis.cache_key or "",
                    effective_model,
                    include_examples,
                    asdict(prompt)
                )

            return prompt

        except Exception as e:
            print(f"⚠️  Generation error: {e}")
            raise

    def _get_prompt_schema(self) -> Dict[str, Any]:
        """JSON schema for prompt generation (Interactions API response_format)."""
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "level": {"type": "string", "enum": [l.value for l in PromptLevel]},
                "purpose": {"type": "string"},
                "variables": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "variable_type": {"type": "string"},
                            "description": {"type": "string"},
                            "required": {"type": "boolean"},
                            "default": {"type": "string"},
                        },
                        "required": ["name", "variable_type", "description", "required"],
                    },
                },
                "workflow": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step_number": {"type": "number"},
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                        },
                        "required": ["step_number", "title", "description"],
                    },
                },
                "design_rationale": {"type": "string"},
                "rendered_prompt": {"type": "string"},
                "metadata": {
                    "type": "object",
                    "properties": {
                        "recommended_model": {"type": "string"},
                        "temperature": {"type": "number"},
                        "thinking_budget": {"type": "number"},
                    },
                    "required": ["recommended_model", "temperature"],
                },
            },
            "required": ["title", "level", "purpose", "variables", "workflow",
                         "design_rationale", "rendered_prompt", "metadata"],
        }

    def review_prompt(self, query: str, prompt: GeneratedPrompt) -> str:
        """Review prompt for quality."""
        review_prompt = f"Review this prompt for task '{query}':\n\n{prompt.rendered_prompt}"
        try:
            interaction = self.client.interactions.create(
                model=self.FLASH_MODEL,
                input=review_prompt,
                generation_config=self._generation_config("LOW"),
            )
            return interaction.output_text or "Review unavailable."
        except Exception as e:
            return f"Review failed: {e}"

    def create_prompt(self, task: str, context: Optional[str] = None) -> GeneratedPrompt:
        """Legacy-compatible method for CLI. Uses HIGH thinking (full quality)."""
        query = f"{task}\n\nContext: {context}" if context else task
        analysis = self.analyze_task(query)
        return self.generate_prompt(query, analysis)

    def create_prompt_fast(self, task: str, context: Optional[str] = None) -> GeneratedPrompt:
        """
        Fast variant for spawn_agent and automated pipelines.
        Uses LOW thinking and skips review — still web-researches the domain.
        """
        query = f"{task}\n\nContext: {context}" if context else task
        analysis = self.analyze_task(query, thinking_level="LOW")
        return self.generate_prompt(query, analysis, thinking_level="LOW")

    def create_optimized_prompt(
        self,
        task_description: str,
        complexity_hint: Optional[str] = None,
        target_model: str = "auto",
        include_review: bool = True,
        use_cache: bool = True,
        thinking_level: str = "HIGH",
    ) -> Dict[str, Any]:
        """High-level workflow."""
        analysis = self.analyze_task(
            task_description, complexity_hint, use_cache, thinking_level=thinking_level
        )
        prompt = self.generate_prompt(
            task_description, analysis, target_model,
            use_cached=use_cache, thinking_level=thinking_level
        )

        result = {"analysis": asdict(analysis), "prompt": asdict(prompt)}
        if include_review:
            result["review"] = self.review_prompt(task_description, prompt)
        return result


# ─── Convenience functions ────────────────────────────────────────────────────

def create_prompt(task: str, **kwargs) -> GeneratedPrompt:
    return PromptArchitect().create_prompt(task, context=kwargs.get("context"))


def render_prompt(prompt: GeneratedPrompt, **values) -> str:
    """Quick helper to render a prompt with variables."""
    return prompt.render(values)
