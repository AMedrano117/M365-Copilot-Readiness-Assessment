"""Declared fictional Git state for collector/output fixtures; never tenant data."""
from Core.repository_provenance import runtime_versions


def fictional_revision():
    return dict(schema_version='1.0.0',repository_identifier='fictional-repository',
        source_state='CleanCommitted',commit_sha='a'*40,commit_tree_sha='b'*40,
        branch='fictional-validation',dirty_tree_fingerprint=None,
        qualification='Fictional committed source fixture; no real repository attribution',warnings=[],
        **runtime_versions())
