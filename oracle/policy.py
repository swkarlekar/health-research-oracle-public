"""Focused scope questions and conservative PII checks; no raw query logging."""
import re
from datetime import date

from .corpus import HarnessError

SCOPE_QUESTIONS = {
    "program": "Which insurance program and jurisdiction should the research cover? 'Unknown' is acceptable.",
    "plan_type": "Which plan category should the research cover (for example MLTC or mainstream managed care)? 'Unknown' is acceptable.",
    "research_date": "Which governing date or period should this research cover? Use YYYY-MM-DD or unknown; do not provide personal dates.",
}


def inferred_scope(question):
    """Use scope explicitly present in prose; never infer client eligibility."""
    plan = next((p for p in ('MLTC', 'HARP', 'CDPAP') if re.search(r'\b' + p + r'\b', question, re.I)), None)
    if plan is None and re.search(r'\bmanaged long[ -]term care\b', question, re.I):
        plan = 'MLTC'
    if plan is None and re.search(r'\bnon-MAGI\b', question, re.I):
        plan = 'not plan specific'
    if plan is None and re.search(r'\bmanaged care\b', question, re.I):
        plan = 'managed care (type unspecified)'
    program = 'New York Medicaid' if re.search(r'\b(?:New York|NY)\b', question, re.I) and re.search(r'\bMedicaid\b', question, re.I) else None
    return {'program': program, 'plan_type': plan, 'research_date': 'unknown'}


def check_input(question, scope):
    if not isinstance(question, str) or not question.strip() or len(question) > 1600:
        raise HarnessError("Research question must contain 1 to 1600 characters")
    text = " ".join([question] + [str(v) for v in scope.values() if v])
    # Best-effort detection, not a complete PII classifier. Never echo detected input.
    if re.search(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}|\b\d{3}-\d{2}-\d{4}\b|\b\d{3}[-. ]\d{3}[-. ]\d{4}\b", text):
        raise HarnessError("Remove identifying information and ask a general document research question")
    if re.search(r"\b(?:member\s*(?:id|number)|social security|date of birth|dob)\s*[:=]", text, re.I):
        raise HarnessError("Remove identifying information and ask a general document research question")
    for value in scope.values():
        if value is not None and (not isinstance(value, str) or len(value) > 160):
            raise HarnessError("Research scope values must be strings of at most 160 characters")
    governing = scope.get("research_date")
    if governing and governing.lower() != "unknown":
        try:
            date.fromisoformat(governing)
        except ValueError as exc:
            raise HarnessError("Research date must be YYYY-MM-DD or unknown") from exc


def missing_scope(scope):
    return [question for key, question in SCOPE_QUESTIONS.items() if not (scope.get(key) or "").strip()]


def clarification_result(question, scope):
    """Scope clarification needs neither a database, model configuration, nor network."""
    check_input(question, scope)
    questions = missing_scope(scope)
    if not questions:
        return None
    return {"status": "clarification_needed", "scope": dict(scope, question=question),
            "findings": [], "quotes": [], "clarifying_questions": questions,
            "uncertainties": [], "model_invoked": False,
            "harness": {"searches": 0, "model_calls": 0}}


def check_response_boundary(response):
    if any(not any(character.isalpha() for character in finding['text'])
           for finding in response['findings']):
        raise HarnessError("Response contains a finding without substantive text")
    # Source quotations are deliberately excluded: attributed imperative rules are allowed.
    prose = " ".join([f["text"] for f in response["findings"]]
                     + [u["description"] for u in response["uncertainties"]]
                     + response["scope"]["assumptions"])
    questions = " ".join(response["clarifying_questions"] +
                         [u["question_for_user"] for u in response["uncertainties"] if u["question_for_user"]])
    if re.search(r"\b(?:you should|your client should|advise (?:the|your) client|recommend that|file an appeal now)\b", prose, re.I):
        raise HarnessError("Response failed the research-only boundary check")
    if re.search(r"\b(?:name|birth|dob|address|email|phone|telephone|member (?:id|number)|diagnos\w*|social security|client documents|medical records)\b", questions, re.I):
        raise HarnessError("Clarification failed the non-identifying scope check")
