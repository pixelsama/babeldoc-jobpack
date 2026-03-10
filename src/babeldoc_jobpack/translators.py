from __future__ import annotations

from babeldoc.translator.translator import BaseTranslator


class EchoTranslator(BaseTranslator):
    """Translator used for workflow plumbing instead of real translation."""

    name = "jobpack_echo"
    model = "echo"

    def do_translate(self, text, rate_limit_params: dict | None = None):
        return "" if text is None else text

    def do_llm_translate(self, text, rate_limit_params: dict | None = None):
        return "" if text is None else text

