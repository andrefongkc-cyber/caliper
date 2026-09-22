"""Sketch constraints: the relation registry, the solver, and what they report (ADR 0008).

Pure Python on purpose: no native dependency, and only IEEE arithmetic plus `math`, so a
solve is reproducible. Nothing here imports the command layer; the bus calls in.
"""
