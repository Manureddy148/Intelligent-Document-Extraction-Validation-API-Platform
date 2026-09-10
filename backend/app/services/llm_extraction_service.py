"""Optional LLM-assisted extraction pass.

The deterministic layout parser handles clean documents well, but on poorly
scanned pages OCR loses row labels entirely and fields come back null. When an
API key is configured, this service asks a model to recover *only* the fields
that are still missing, working from the OCR text of the document.

It never overwrites a value the deterministic parser already grounded in a
source row, and it is instructed to return null rather than guess, so the
"do not invent values" rule still holds. Without an API key the service is
disabled and the pipeline is fully deterministic.
"""

import json

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_SYSTEM_PROMPT = (
    "You extract structured data from financial documents that have been read by OCR. "
    "Copy values exactly as they appear in the text - do not calculate, infer, convert or "
    "reformat them. If the document does not clearly show a field, return null for it. "
    "Never guess. For every value you do return, quote the source line it came from."
)


class LlmExtractionService:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.llm_api_key)

    def recover_missing_fields(
        self, document_text: str, document_type: str, missing_fields: list[str]
    ) -> dict[str, dict]:
        """Ask the model for the fields the rule-based extractor could not find."""
        if not self.enabled or not missing_fields or not document_text.strip():
            return {}

        try:
            raw = self._call_model(document_text, document_type, missing_fields)
        except Exception:
            logger.exception("LLM-assisted extraction failed; keeping rule-based result")
            return {}

        return self._normalise(raw, missing_fields)

    def _build_schema(self, missing_fields: list[str]) -> dict:
        field_schema = {
            "type": "object",
            "properties": {
                "value": {"type": ["string", "number", "null"]},
                "page_number": {"type": ["integer", "null"]},
                "source_text": {"type": ["string", "null"]},
            },
            "required": ["value", "page_number", "source_text"],
            "additionalProperties": False,
        }
        return {
            "type": "object",
            "properties": {name: field_schema for name in missing_fields},
            "required": list(missing_fields),
            "additionalProperties": False,
        }

    def _call_model(self, document_text: str, document_type: str, missing_fields: list[str]) -> dict:
        import anthropic

        client = anthropic.Anthropic(api_key=self.settings.llm_api_key)
        text = document_text[: self.settings.llm_max_text_chars]
        prompt = (
            f"This is the OCR text of a {document_type.replace('_', ' ')} document. "
            "Page breaks are marked with '--- page N ---'.\n\n"
            f"{text}\n\n"
            "Return these fields, using null for any the document does not clearly show: "
            f"{', '.join(missing_fields)}."
        )

        response = client.messages.create(
            model=self.settings.llm_model,
            max_tokens=self.settings.llm_max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": self._build_schema(missing_fields)}},
        )
        payload = next(block.text for block in response.content if block.type == "text")
        return json.loads(payload)

    def _normalise(self, raw: dict, missing_fields: list[str]) -> dict[str, dict]:
        recovered: dict[str, dict] = {}
        for name in missing_fields:
            entry = raw.get(name)
            if not isinstance(entry, dict) or entry.get("value") is None:
                continue
            recovered[name] = {
                "value": entry.get("value"),
                "page_number": entry.get("page_number"),
                "source_text": entry.get("source_text"),
                "extraction_method": "llm_assisted",
            }
        logger.info("LLM-assisted extraction recovered %s of %s missing fields", len(recovered), len(missing_fields))
        return recovered
