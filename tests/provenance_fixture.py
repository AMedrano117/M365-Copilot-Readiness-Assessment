"""Declared fictional Git state for collector/output fixtures; never tenant data."""
from Core.repository_provenance import runtime_versions, publication_policy, EXPECTED_REMOTE


def fictional_revision():
    revision = dict(schema_version='1.0.0',repository_identifier=EXPECTED_REMOTE,remote_identifier=EXPECTED_REMOTE,
        staged_change_count=0,tracked_modification_count=0,untracked_entry_count=0,
        fingerprint_version='sha256-source-state-v2',generated_at='2026-10-13T12:00:00+00:00',
        run_configuration_hash='d'*64,collector_configuration_hash='e'*64,
        execution_environment={'os':'Fictional','python':'3.12.0'},
        source_state='CleanCommitted',commit_sha='a'*40,commit_tree_sha='b'*40,
        branch='fictional-validation',dirty_tree_fingerprint=None,
        qualification='Fictional committed source fixture; no real repository attribution',warnings=[],
        **runtime_versions())
    revision['publication'] = publication_policy(revision, policy='engineering-validation',
        purpose='Fictional test output', reason='No real repository attribution', acknowledged=True)
    return revision


def fixture_context(revision):
    """Mock repository I/O only inside explicitly fictional tests; no product bypass."""
    from contextlib import ExitStack
    from unittest.mock import patch
    from Core import repository_provenance as provenance
    stack = ExitStack()
    original_verify = provenance.verify_execution_source
    original_update = provenance.update_execution_configuration
    def fictional_active():
        current=provenance.current_revision()
        return current.get('branch')=='fictional-validation' and current.get('qualification')=='Fictional committed source fixture; no real repository attribution'
    def verify():
        if not fictional_active(): return original_verify()
    def update(args):
        if not fictional_active(): return original_update(args)
    stack.enter_context(provenance.producing_context(revision))
    stack.enter_context(patch.object(provenance,'verify_execution_source',side_effect=verify))
    stack.enter_context(patch.object(provenance,'update_execution_configuration',side_effect=update))
    return stack


def fictional_publication(cls):
    """Explicit synthetic writer context; CLI and negative tests establish their own policy."""
    import unittest
    from Core.repository_provenance import producing_context
    if not issubclass(cls, unittest.TestCase):
        return cls
    setup = cls.__dict__.get('setUpClass')
    if setup is not None:
        original_setup = cls.setUpClass
        def set_up(inner):
            with fixture_context(fictional_revision()):
                return original_setup()
        cls.setUpClass = classmethod(set_up)
    original = cls.run
    def run(self, result=None):
        revision = fictional_revision()
        if hasattr(self, "_asyncioTestContext"):
            from Core.repository_provenance import set_execution_revision
            self._asyncioTestContext.run(set_execution_revision, revision)
        with fixture_context(revision):
            return original(self, result)
    cls.run = run
    return cls
