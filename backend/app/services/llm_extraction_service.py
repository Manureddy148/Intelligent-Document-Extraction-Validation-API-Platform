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
import re
import time
import urllib.error
import urllib.request

from app.core.config import Settings
from app.core.logging import get_logger
from app.utils.text_parsing import parse_number

logger = get_logger(__name__)

_WHITESPACE_RE = re.compile(r"\s+")
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

    def _provider_chain(self) -> list[str]:
        """Every configured provider, best first.

        Gemini leads because it reads the page image; Anthropic follows as a
        text-only second opinion over the OCR output.
        """
        chain = []
        if self.settings.gemini_api_key:
            chain.append("gemini")
        if self.settings.llm_api_key:
            chain.append("anthropic")
        return chain

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
        raw = None
        for provider in self._provider_chain():
            try:
                if provider == "gemini":
                    raw = self._call_gemini(
                        document_text, document_type, missing_fields, page_images or [], periods
                    )
                else:
                    raw = self._call_anthropic(document_text, document_type, missing_fields, periods)
                break
            except Exception:
                # A free tier can be exhausted or at capacity for a whole run, so
                # the fallback that matters is to a different provider, not just
                # a different model of the same one.
                logger.exception("Recovery via %s failed; trying the next provider", provider)

        if raw is None:
            # Whatever went wrong upstream, a document the deterministic path
            # already read must not fail because of it.
            logger.warning("No provider could serve the recovery pass; keeping the rule-based result")
            return {}

        return self._normalise(raw, missing_fields, periods)

    def recover_line_items(self, document_type: str, page_images: list[bytes]) -> list[dict]:
        """Read an item table when the rule-based parser found none.

        Noisy receipts defeat row-shape matching: columns run together, the
        quantity marker is misread, or the table has no header to anchor on. The
        page itself still shows the table. Only called when no items were found,
        so a row the parser did read is never replaced by a model's reading.
        """
        if self.provider != "gemini" or not page_images:
            return []

        item_schema = {
            "type": "object",
            "properties": {
                "description": {"type": "string"},
                "quantity": {"type": "string", "nullable": True},
                "unit_price": {"type": "string", "nullable": True},
                "amount": {"type": "string", "nullable": True},
                "source_text": {"type": "string"},
                "page_number": {"type": "integer", "nullable": True},
            },
            "required": ["description", "quantity", "unit_price", "amount", "source_text"],
        }
        instruction = (
            f"This is a {document_type.replace('_', ' ')}. List the line items in its table, one "
            "entry per printed row, in order. Copy each figure exactly as shown and quote the row "
            "it came from in source_text. Use null for a column the row does not show - never "
            "calculate a missing quantity or unit price from the other columns. Return an empty "
            "list if the document has no item table."
        )
        parts: list[dict] = [{"text": instruction}]
        for image in page_images[: self.settings.max_pages]:
            parts.append({"inline_data": {"mime_type": "image/png", "data": base64.b64encode(image).decode()}})

        body = {
            "systemInstruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": self.settings.llm_max_tokens,
                "response_mime_type": "application/json",
                "response_schema": {"type": "array", "items": item_schema},
            },
        }
        try:
            payload = self._gemini_request(body)
        except Exception:
            logger.exception("Line-item recovery failed; keeping the rule-based result")
            return []

        rows: list[dict] = []
        for entry in payload if isinstance(payload, list) else []:
            if not isinstance(entry, dict):
                continue
            description = _WHITESPACE_RE.sub(" ", entry.get("description") or "").strip()
            source_text = _WHITESPACE_RE.sub(" ", entry.get("source_text") or "").strip()
            amount = _coerce(entry.get("amount"))
            # A row with no description, no printed amount, or nothing to trace
            # it back to is not evidence of a line on the page.
            if not description or not source_text or not isinstance(amount, (int, float)):
                continue
            quantity, unit_price = _coerce(entry.get("quantity")), _coerce(entry.get("unit_price"))
            quantity = quantity if isinstance(quantity, (int, float)) else None
            unit_price = unit_price if isinstance(unit_price, (int, float)) else None
            # The same rule the rule-based parser uses: if quantity x rate does
            # not come out near the printed amount, the columns were read wrong.
            # The amount is what the page shows, so it is kept; asserting a
            # quantity and rate that do not multiply out would report a
            # discrepancy belonging to the reading, not to the document.
            if quantity is not None and unit_price is not None:
                product = quantity * unit_price
                if product <= 0 or not 0.8 <= product / amount <= 1.25:
                    logger.info(
                        "Dropping quantity/rate for '%s': %s x %s does not reach %s",
                        description[:40], quantity, unit_price, amount,
                    )
                    quantity = unit_price = None
            rows.append(
                {
                    "description": description,
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "amount": amount,
                    "page_number": entry.get("page_number"),
                    "source_text": source_text,
                    "extraction_method": "llm_assisted",
                }
            )
        logger.info("Line-item recovery read %s rows from the page", len(rows))
        return rows

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
        return self._gemini_request(body)

    def _gemini_request(self, body: dict):
        """Send one request, retrying and falling through the model chain.

        Shared by the field and line-item passes: both need the same handling of
        a free tier that is routinely at capacity.
        """
        headers = {
            "Content-Type": "application/json",
            # Header rather than a query parameter so the key stays out of
            # request logs and proxy access logs.
            "x-goog-api-key": self.settings.gemini_api_key or "",
        }
        # Retries across several models compound: one document spent 134s in
        # here while the free tier was busy. The whole pass gets one budget, and
        # the document falls back to its deterministic result when it runs out.
        deadline = time.monotonic() + self.settings.llm_total_budget_seconds
        payload, model_used = None, None
        for model in self._model_chain():
            if time.monotonic() >= deadline:
                logger.info("Vision pass gave up after %ss; keeping the rule-based result",
                            self.settings.llm_total_budget_seconds)
                break
            try:
                payload = self._post_json(_GEMINI_ENDPOINT.format(model=model), body, headers, deadline)
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
        if candidates[0].get("finishReason") == "MAX_TOKENS":
            raise ValueError(
                f"Vision model hit the {self.settings.llm_max_tokens}-token output limit; "
                "raise IDEV_LLM_MAX_TOKENS"
            )
        text = "".join(part.get("text", "") for part in candidates[0]["content"]["parts"])
        return json.loads(text)

    # Overload and rate-limit responses are routine on a free tier and say
    # nothing about the request; anything else is a real error and retrying it
    # would only waste the caller's time.
    _RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

    def _post_json(self, url: str, body: dict, headers: dict, deadline: float) -> dict:
        data = json.dumps(body).encode()
        last_error: Exception | None = None
        for attempt in range(self.settings.llm_max_attempts):
            if time.monotonic() >= deadline:
                break
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
                backoff = 2 ** attempt
                if time.monotonic() + backoff >= deadline:
                    break
                time.sleep(backoff)

        raise last_error or TimeoutError("vision pass ran out of time")

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
    """Gemini returns every scalar as a string; restore numbers to numbers.

    Parsed with the same reader the deterministic path uses, so bracketed
    negatives and comma decimals are handled identically. A European invoice
    printing "5,00" means five, and stripping the comma as a thousands
    separator would report five hundred.
    """
    if not isinstance(value, str):
        return value
    text = _WHITESPACE_RE.sub(" ", value).strip()
    if not text:
        return None
    number = parse_number(text)
    if number is None:
        return text
    return int(number) if float(number).is_integer() and abs(number) < 1e15 else number
