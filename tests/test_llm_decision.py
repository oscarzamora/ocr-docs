"""Tests for the LLM ↔ keyword decision rule used in cli.process (Step 5)."""

import pytest

from ocr_router.cli import _apply_llm_decision
from ocr_router.llm import ClassificationResult


def _llm(category: str, conf: float, issuer: str | None = None):
    return ClassificationResult(category=category, confidence=conf, issuer=issuer)


def test_below_threshold_keeps_keyword_and_adds_hint():
    issues: list[str] = []
    label, cat, iss = _apply_llm_decision(
        keyword_category="Bills",
        llm_result=_llm("Insurance", 0.4, "Allstate"),
        threshold=0.6,
        issues=issues,
    )
    assert label == "keyword-llm-low-conf"
    assert cat == "Bills"
    assert iss is None
    assert any("Insurance" in i for i in issues)


def test_below_threshold_no_hint_when_agree():
    issues: list[str] = []
    label, cat, iss = _apply_llm_decision(
        keyword_category="Bills",
        llm_result=_llm("Bills", 0.4, "FPL"),
        threshold=0.6,
        issues=issues,
    )
    assert label == "keyword-llm-low-conf"
    assert cat == "Bills"
    # No hint when LLM agrees, even at low confidence
    assert issues == []


def test_agreement_above_threshold_returns_agree():
    issues: list[str] = []
    label, cat, iss = _apply_llm_decision(
        keyword_category="Bills",
        llm_result=_llm("Bills", 0.95, "FPL"),
        threshold=0.6,
        issues=issues,
    )
    assert label == "agree"
    assert cat == "Bills"
    assert iss == "FPL"            # LLM issuer carried through
    assert issues == []


def test_keyword_uncategorized_defers_to_llm_silently():
    issues: list[str] = []
    label, cat, iss = _apply_llm_decision(
        keyword_category="Uncategorized",
        llm_result=_llm("Receipts", 0.88, "B&H Photo"),
        threshold=0.6,
        issues=issues,
    )
    assert label == "hybrid-llm"
    assert cat == "Receipts"
    assert iss == "B&H Photo"
    # No HITL flag — keyword had nothing to disagree with
    assert issues == []


def test_disagreement_above_threshold_flags_hitl_and_takes_llm():
    issues: list[str] = []
    label, cat, iss = _apply_llm_decision(
        keyword_category="Bills",
        llm_result=_llm("Credit Card Statements", 0.91, "AMEX"),
        threshold=0.6,
        issues=issues,
    )
    assert label == "hybrid-llm-disagree"
    assert cat == "Credit Card Statements"
    assert iss == "AMEX"
    assert any("AMEX" in i or "Credit Card" in i for i in issues)


def test_empty_keyword_string_treated_like_uncategorized():
    issues: list[str] = []
    label, cat, iss = _apply_llm_decision(
        keyword_category="",
        llm_result=_llm("Paystubs", 0.8, "Microsoft"),
        threshold=0.6,
        issues=issues,
    )
    assert label == "hybrid-llm"
    assert cat == "Paystubs"
    assert iss == "Microsoft"
    assert issues == []


def test_threshold_boundary_is_inclusive_for_above():
    issues: list[str] = []
    # Exactly at the threshold should be treated as "above"
    label, cat, _ = _apply_llm_decision(
        keyword_category="Bills",
        llm_result=_llm("Bills", 0.6, "FPL"),
        threshold=0.6,
        issues=issues,
    )
    assert label == "agree"
    assert cat == "Bills"
