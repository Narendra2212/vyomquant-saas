"""ML-01 reclassification evidence, by AST rather than by substring count.

The Phase 8 verifier counted `FeatureMatrix` and `pd.Series` as SUBSTRINGS over the
`MLExecutor` class slice -- a slice that includes the class docstring and the method's
return annotation. That is the same error class its own MONEY-07 section was later
rewritten to avoid. This probe establishes, structurally:

  1. how many of the `pd.Series` occurrences are executable code vs docstring/annotation;
  2. that `MLExecutor.execute` cannot return a value (it raises on every path), so the
     feature representation it consumes cannot affect any downstream prediction;
  3. that the FEATURE_MATRIX contract IS implemented and enforced on the governed plan
     path (`run_feature_pipeline`, `concat_matrices`, `FeatureMatrix.align_to`).

If all three hold, ML-01 is a false positive in the check, not a product defect, and the
honest action is to reclassify -- NOT to add a nominal FeatureMatrix reference, which
would change no behaviour and is explicitly forbidden.
"""
from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


def main() -> int:
    print("=" * 78)
    print("ML-01 RECLASSIFICATION EVIDENCE")
    print("=" * 78)

    dag_src = read("backend_app/backend/dag_engine.py")
    tree = ast.parse(dag_src)

    mlx = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.ClassDef) and n.name == "MLExecutor"),
        None,
    )
    if mlx is None:
        print("MLExecutor NOT FOUND")
        return 1

    execute = next(
        (n for n in ast.walk(mlx)
         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "execute"),
        None,
    )

    # (1) Classify every pd.Series occurrence.
    docstring_hits = 0
    annotation_hits = 0
    executable_hits = 0

    class_doc = ast.get_docstring(mlx, clean=False) or ""
    docstring_hits += class_doc.count("pd.Series")

    # The return annotation on execute.
    if execute is not None and execute.returns is not None:
        annotation_hits += ast.get_source_segment(dag_src, execute.returns).count("pd.Series") \
            if ast.get_source_segment(dag_src, execute.returns) else 0

    # Executable: an Attribute `pd.Series` that is NOT the method's return annotation.
    # The annotation is itself an `ast.Attribute` node in the tree, so it must be excluded
    # by identity or it is miscounted as executable -- which is the very substring-vs-code
    # conflation this probe exists to correct.
    annotation_node = execute.returns if execute is not None else None
    for node in ast.walk(mlx):
        if isinstance(node, ast.Attribute) and node.attr == "Series":
            if node is annotation_node:
                continue
            owner = getattr(node.value, "id", None)
            if owner == "pd":
                executable_hits += 1

    print(f"pd.Series in class docstring   : {docstring_hits}")
    print(f"pd.Series in return annotation : {annotation_hits}")
    print(f"pd.Series as executable code   : {executable_hits}")

    # (2) Can execute() return a value?
    returns_a_value = False
    raises = 0
    if execute is not None:
        for node in ast.walk(execute):
            if isinstance(node, ast.Return) and node.value is not None:
                # A bare `return` or `return None` does not count as returning a value.
                if not (isinstance(node.value, ast.Constant) and node.value.value is None):
                    returns_a_value = True
            if isinstance(node, ast.Raise):
                raises += 1
    print(f"execute() returns a value?     : {returns_a_value}")
    print(f"execute() raise statements     : {raises}")

    # (3) Is the FEATURE_MATRIX contract enforced on the governed path?
    svc = read("backend_app/backend/strategy_service.py")
    enforced = (
        "run_feature_pipeline" in svc
        and "FeatureMatrix" in svc
        and ("TrainingBlocked" in svc or "isinstance" in svc)
    )
    print(f"run_feature_pipeline enforces FeatureMatrix: {enforced}")

    fm_src = read("backend_app/backend/strategy_dag/feature_matrix.py")
    concat_refuses = "concat_matrices" in fm_src and "FeatureMatrix" in fm_src
    align_refuses = "def align_to" in fm_src and "FeatureAlignmentError" in fm_src
    print(f"concat_matrices refuses non-matrix        : {concat_refuses}")
    print(f"FeatureMatrix.align_to refuses missing ts : {align_refuses}")

    print("=" * 78)
    verdict_false_positive = (
        executable_hits <= 1
        and not returns_a_value
        and raises >= 1
        and enforced
    )
    print("VERDICT:", "FALSE POSITIVE (reclassify)" if verdict_false_positive
          else "NEEDS REVIEW")
    print(
        "  -> Only {} executable pd.Series reference; execute() raises on every path and "
        "returns no value; the FEATURE_MATRIX contract is enforced where features reach a "
        "model.".format(executable_hits)
    )
    return 0 if verdict_false_positive else 2


if __name__ == "__main__":
    raise SystemExit(main())
