"""Optional LLM-assisted extraction pass.

The deterministic layout parser handles clean documents well, but on poorly
scanned pages OCR loses row labels entirely and fields come back null. When an
API key is configured, this service asks a model to recover *only* the fields
that are still missing.

Two providers are supported and selected by whichever key is set:

* **Gemini** reads the rendered page *image*. On these documents that matters
  more than the model does - where OCR dropped a row label, the label is still
  plainly there on the page, and no amount of reasoning over broken OCR text
  will bring it back.
* **Anthropic** reads the OCR text, as a text-only fallback.

The rules that keep the output honest hold either way: a value the deterministic
parser already grounded in a source row is never overwritten, every recovered
value must come back with the source text it was read from, and the model is
told to return null rather than guess. Without an API key the service is
disabled and the pipeline is fully deterministic.
"""

import base64
import json
import time
import urllib.error
import urllib.request

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_SYSTEM_PROMPT = (
    "You read financial documents and report exactly what is printed on them. "
    "Copy values character for character - never calculate, infer, convert, round or "
    "reformat them. Bracketed figures such as (1,234) are negative. If a field is not "
    "clearly shown, or you cannot read it with confidence, return null for it: a null is "
    "correct and a guess is not. For every value you return, quote the line it came from "
    "in source_text and give its page number."
)


class LlmExtractionService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._model_used: str | None = None

    def _model_chain(self) -> list[str]:
        """The configured model first, then the fallbacks, without duplicates."""
        chain = [self.settings.gemini_model, *self.settings.gemini_fallback_models]
        return list(dict.fromkeys(model for model in chain if model))

    @property
    def provider(self) -> str | None:
        """Which provider to use, chosen by whichever key is configured."""
        if self.settings.gemini_api_key:
            return "gemini"
        if self.settings.llm_api_key:
            return "anthropic"
        return None

    @property
    def enabled(self) -> bool:
        return self.provider is not None

    @property
    def model(self) -> str | None:
        if self.provider == "gemini":
            # Report the model that actually answered, not the one configured.
            return self._model_used or self.settings.gemini_model
        if self.provider == "anthropic":
            return self.settings.llm_model
        return None

    def recover_missing_fields(
        self,
        document_text: str,
        document_type: str,
        missing_fields: list[str],
        page_images: list[bytes] | None = None,
        periods: list[str] | None = None,
    ) -> dict[str, dict]:
        """Ask the model for the fields the rule-based extractor could not find."""
        if not self.enabled or not missing_fields:
            return {}
        if not document_text.strip() and not page_images:
            return {}

        periods = periods or []
        try:
            if self.provider == "gemini":
                raw = self._call_gemini(
                    document_text, document_type, missing_fields, page_images or [], periods
                )
            else:
                raw = self._call_anthropic(document_text, document_type, missing_fields, periods)
        except Exception:
            # A model outage must never fail a document the deterministic path
            # already read; the rule-based result stands on its own.
            logger.exception("LLM-assisted extraction failed; keeping the rule-based result")
            return {}

        return self._normalise(raw, missing_fields, periods)

    # ---- prompt and schema ------------------------------------------------

    def _value_schema(self, periods: list[str], *, for_gemini: bool) -> dict:
        """The shape of one field's value.

        A statement carries one figure per reporting period, and the rule-based
        extractor already returns those as an object keyed by period. The model
        has to answer in the same shape or the two halves of `extracted_data`
        would have different types for the same field, and the per-period
        financial checks could not read what it returned.
        """
        scalar = {"type": "string", "nullable": True} if for_gemini else {"type": ["string", "number", "null"]}
        if not periods:
            return scalar
        per_period = {
            "type": "object",
            "properties": {period: dict(scalar) for period in periods},
            "required": list(periods),
        }
        if not for_gemini:
            per_period["additionalProperties"] = False
        return per_period

    def _build_schema(self, missing_fields: list[str], periods: list[str], *, for_gemini: bool) -> dict:
        # Gemini's schema dialect has no "additionalProperties" and takes a
        # single type rather than a union, so nullability is expressed with
        # "nullable" instead.
        if for_gemini:
            field_schema = {
                "type": "object",
                "properties": {
                    "value": self._value_schema(periods, for_gemini=True),
                    "page_number": {"type": "integer", "nullable": True},
                    "source_text": {"type": "string", "nullable": True},
                },
                "required": ["value", "page_number", "source_text"],
            }
            return {
                "type": "object",
                "properties": {name: field_schema for name in missing_fields},
                "required": list(missing_fields),
            }

        field_schema = {
            "type": "object",
            "properties": {
                "value": self._value_schema(periods, for_gemini=False),
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

    def _instruction(self, document_type: str, missing_fields: list[str], periods: list[str]) -> str:
        lines = [
            f"This is a {document_type.replace('_', ' ')} document. Read these fields from it, "
            f"returning null for any the document does not clearly show: {', '.join(missing_fields)}.",
            "Report figures exactly as printed, including their sign.",
        ]
        if periods:
            lines.append(
                "The statement reports these columns: "
                + ", ".join(f'"{period}"' for period in periods)
                + ". Give each field one value per column, matching the column its figure is printed "
                "under. Use null for a column where that row has no figure."
            )
        return "\n".join(lines)

    # ---- providers --------------------------------------------------------

    def _call_gemini(
        self,
        document_text: str,
        document_type: str,
        missing_fields: list[str],
        page_images: list[bytes],
        periods: list[str],
    ) -> dict:
        parts: list[dict] = [{"text": self._instruction(document_type, missing_fields, periods)}]
        for image in page_images[: self.settings.max_pages]:
            parts.append({"inline_data": {"mime_type": "image/png", "data": base64.b64encode(image).decode()}})
        if document_text.strip():
            parts.append(
                {
                    "text": "For reference, an OCR reading of the same document follows. It is "
                    "unreliable - prefer what you can see on the page.\n\n"
                    + document_text[: self.settings.llm_max_text_chars]
                }
            )

        body = {
            "systemInstruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": self.settings.llm_max_tokens,
                "response_mime_type": "application/json",
                "response_schema": self._build_schema(missing_fields, periods, for_gemini=True),
            },
        }
        headers = {
            "Content-Type": "application/json",
            # Header rather than a query parameter so the key stays out of
            # request logs and proxy access logs.
            "x-goog-api-key": self.settings.gemini_api_key or "",
        }
        # A free-tier model can be at capacity for minutes at a time, and a
        # document should not lose its recovery pass because one model is busy.
        payload, model_used = None, None
        for model in self._model_chain():
            try:
                payload = self._post_json(_GEMINI_ENDPOINT.format(model=model), body, headers)
                model_used = model
                break
            except Exception as exc:
                logger.info("Vision model '%s' unavailable (%s); trying the next", model, exc)
        if payload is None:
            raise RuntimeError("no configured vision model was available")
        if model_used != self.settings.gemini_model:
            logger.info("Vision pass served by fallback model '%s'", model_used)
        self._model_used = model_used

        candidates = payload.get("candidates") or []
        if not candidates:
            raise ValueError(f"Gemini returned no candidates: {json.dumps(payload)[:300]}")
        # A truncated answer is invalid JSON, and the decoder's "unterminated
        # string" says nothing about the cause; name it instead.
        finish_reason = candidates[0].get("finishReason")
        if finish_reason == "MAX_TOKENS":
            raise ValueError(
                f"Vision model hit the {self.settings.llm_max_tokens}-token output limit "
                f"reading {len(missing_fields)} fields; raise IDEV_LLM_MAX_TOKENS"
            )
        text = "".join(part.get("text", "") for part in candidates[0]["content"]["parts"])
        return json.loads(text)

    # Overload and rate-limit responses are routine on a free tier and say
    # nothing about the request; anything else is a real error and retrying it
    # would only waste the caller's time.
    _RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

    def _post_json(self, url: str, body: dict, headers: dict) -> dict:
        data = json.dumps(body).encode()
        last_error: Exception | None = None
        for attempt in range(self.settings.llm_max_attempts):
            request = urllib.request.Request(url, data=data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.settings.llm_timeout_seconds) as response:
                    return json.loads(response.read())
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:400]
                last_error = exc
                if exc.code not in self._RETRYABLE_STATUS:
                    logger.warning("Vision model rejected the request (HTTP %s): %s", exc.code, detail)
                    raise
                logger.info("Vision model returned HTTP %s (attempt %s); retrying", exc.code, attempt + 1)
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
                logger.info("Vision model call failed (attempt %s): %s; retrying", attempt + 1, exc)

            if attempt + 1 < self.settings.llm_max_attempts:
                time.sleep(2 ** attempt)

        assert last_error is not None
        raise last_error

    def _call_anthropic(
        self, document_text: str, document_type: str, missing_fields: list[str], periods: list[str]
    ) -> dict:
        import anthropic

        client = anthropic.Anthropic(api_key=self.settings.llm_api_key)
        prompt = (
            f"{self._instruction(document_type, missing_fields, periods)}\n\n"
            "Page breaks in the OCR text below are marked '--- page N ---'.\n\n"
            + document_text[: self.settings.llm_max_text_chars]
        )
        response = client.messages.create(
            model=self.settings.llm_model,
            max_tokens=self.settings.llm_max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_config={
                "format": {"type": "json_schema", "schema": self._build_schema(missing_fields, periods, for_gemini=False)}
            },
        )
        return json.loads(next(block.text for block in response.content if block.type == "text"))

    # ---- result handling --------------------------------------------------

    def _normalise(self, raw: dict, missing_fields: list[str], periods: list[str]) -> dict[str, dict]:
        recovered: dict[str, dict] = {}
        for name in missing_fields:
            entry = raw.get(name)
            if not isinstance(entry, dict) or entry.get("value") is None:
                continue
            # A value with nothing to trace it to is exactly what this pipeline
            # promises not to produce, whoever produced it.
            if not (entry.get("source_text") or "").strip():
                logger.info("Discarding LLM value for '%s': no source text to ground it", name)
                continue
            value = entry.get("value")
            if periods and isinstance(value, dict):
                value = {period: _coerce(value.get(period)) for period in periods}
                if all(figure is None for figure in value.values()):
                    continue
            else:
                value = _coerce(value)
            recovered[name] = {
                "value": value,
                "page_number": entry.get("page_number"),
                "source_text": entry.get("source_text"),
                "extraction_method": "llm_assisted",
            }
        logger.info(
            "LLM-assisted extraction (%s) recovered %s of %s missing fields",
            self.provider, len(recovered), len(missing_fields),
        )
        return recovered


def _coerce(value):
    """Gemini returns every scalar as a string; restore numbers to numbers."""
    if not isinstance(value, str):
        return value
    cleaned = value.strip().replace(",", "")
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    cleaned = cleaned.strip("()")
    try:
        number = float(cleaned)
    except ValueError:
        return value.strip()
    number = -number if negative else number
    return int(number) if number.is_integer() and abs(number) < 1e15 else number
