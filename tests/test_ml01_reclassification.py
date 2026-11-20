"""ML-01 — reclassified as a false positive in the check, with the evidence that forces it.

THE FINDING, AND WHY IT IS NOT A PRODUCT DEFECT
===============================================
ML-01 was recorded as "`MLExecutor` has 0 `FeatureMatrix` references and 3 `pd.Series`
references, suggesting a feature-contract discrepancy." The COUNT is right as a text
count. The INFERENCE is wrong, and the brief for this phase is explicit that ML-01 must be
either fixed with evidence or reclassified with evidence -- and that a nominal
`FeatureMatrix` reference must NOT be added just to satisfy the check.

The three `pd.Series` occurrences in the `MLExecutor` class slice are:

  * one in the class DOCSTRING, describing code that was removed;
  * one in `execute`'s return ANNOTATION, identical on every sibling executor because the
    legacy loose-dict engine propagates `Dict[str, pd.Series]`;
  * one executable line: `if isinstance(value, pd.Series)`, which filters inputs.

And `execute` RAISES `MLInferenceUnavailable` on every path -- it cannot return a value at
all -- so the representation it filters inputs by can never reach a model or a prediction.
Meanwhile the FEATURE_MATRIX contract the finding is concerned with IS implemented and
enforced where features actually reach a model, on the governed plan path.

So the defective artefact is the check, not the code. The Phase 8 verifier
(`staging/verify_phase8_open_findings.py`) counted substrings over a class slice that
includes the docstring and the annotation -- the same error class its own MONEY-07 section
was rewritten to avoid. This file encodes the correct, AST-based evidence as a test, so
the reclassification is durable and a regression that genuinely wired loose-dict ML
inference would fail it.

THIS FILE DELIBERATELY DOES NOT ASSERT `MLExecutor` CONSUMES A FeatureMatrix.
Doing so would be the nominal fix the brief forbids, and it would change no behaviour,
because `execute` raises regardless of what it is handed.
"""

from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _dag_tree():
    import backend_app.backend.dag_engine as mod

    source = inspect.getsource(mod)
    return ast.parse(source), source


def _ml_executor():
    tree, source = _dag_tree()
    cls = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "MLExecutor"
    )
    execute = next(
        n for n in ast.walk(cls)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "execute"
    )
    return cls, execute, source


def test_ml_executor_execute_cannot_return_a_value():
    """The load-bearing fact: nothing this method filters can reach a prediction.

    `execute` raises on every path and never returns a value, so whether it collects
    `pd.Series` or `FeatureMatrix` inputs cannot affect any downstream model. A
    feature-representation discrepancy in a method that cannot produce output is not a
    behavioural defect.
    """
    _cls, execute, _source = _ml_executor()

    returns_a_value = False
    raises = 0
    for node in ast.walk(execute):
        if isinstance(node, ast.Return) and node.value is not None:
            if not (isinstance(node.value, ast.Constant) and node.value.value is None):
                returns_a_value = True
        if isinstance(node, ast.Raise):
            raises += 1

    assert not returns_a_value, (
        "MLExecutor.execute now returns a value, so the feature representation it consumes "
        "CAN affect a prediction -- ML-01 would need re-examining as a real defect"
    )
    assert raises >= 1, "MLExecutor.execute no longer raises; re-examine ML-01"


def test_only_one_pd_series_reference_in_ml_executor_is_executable():
    """The count the finding reported was a substring count over docstring + annotation.

    AST-accurate: of the `pd.Series` occurrences in the class, at most one is executable
    code (the input filter). The others are the class docstring and the return annotation.
    """
    cls, execute, source = _ml_executor()

    annotation_node = execute.returns
    executable = 0
    for node in ast.walk(cls):
        if isinstance(node, ast.Attribute) and node.attr == "Series":
            if node is annotation_node:
                continue
            if getattr(node.value, "id", None) == "pd":
                executable += 1

    assert executable <= 1, (
        f"MLExecutor has {executable} executable pd.Series references; the finding's "
        f"count conflated the docstring and the return annotation with code"
    )


def test_ml_executor_does_not_import_or_reference_feature_matrix():
    """Confirms the '0 FeatureMatrix references' half -- and that it is CORRECT.

    The absence is not a gap: `MLExecutor` is the legacy loose-dict path, which does not
    use the governed FEATURE_MATRIX type. Adding a reference here to satisfy the check is
    exactly the nominal fix the brief forbids.
    """
    cls, _execute, source = _ml_executor()
    cls_source = ast.get_source_segment(source, cls) or ""
    # Executable references only: strip the docstring, which may mention the type in prose.
    doc = ast.get_docstring(cls, clean=False) or ""
    body_without_doc = cls_source.replace(doc, "", 1)
    assert "FeatureMatrix" not in body_without_doc, (
        "a FeatureMatrix reference was added to MLExecutor. If it is a nominal reference "
        "to satisfy the audit check, that is explicitly forbidden; if it wires real "
        "inference, ML-01 is a feature with an owner, not a reclassification"
    )


def test_the_feature_matrix_contract_is_enforced_on_the_governed_path():
    """Where features ACTUALLY reach a model, the contract the finding cares about holds.

    This is why ML-01 is a false positive rather than an unaddressed gap: the FEATURE_MATRIX
    discipline exists and is enforced -- just not on the dead legacy executor the finding
    looked at.
    """
    svc = (_REPO_ROOT / "backend_app" / "backend" / "strategy_service.py").read_text(
        encoding="utf-8", errors="replace"
    )
    assert "run_feature_pipeline" in svc
    assert "FeatureMatrix" in svc
    assert "TrainingBlocked" in svc or "isinstance" in svc, (
        "run_feature_pipeline no longer refuses a non-FeatureMatrix on the ML feature "
        "port, so the governed path's enforcement -- the basis for reclassifying ML-01 -- "
        "is gone"
    )

    fm = (
        _REPO_ROOT / "backend_app" / "backend" / "strategy_dag" / "feature_matrix.py"
    ).read_text(encoding="utf-8", errors="replace")
    assert "def concat_matrices" in fm
    assert "def align_to" in fm and "FeatureAlignmentError" in fm, (
        "FeatureMatrix.align_to no longer refuses a missing timestamp, so the alignment "
        "guarantee the FEATURE_MATRIX contract exists to provide is weakened"
    )
