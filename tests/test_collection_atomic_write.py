"""Short Windows locks do not lose complete atomic collection checkpoints."""

import errno
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import call, patch

from Core.offline_collection import _write_collection_json


def windows_lock(code=5):
    error = PermissionError(errno.EACCES, 'Synthetic Windows file lock')
    error.winerror = code
    return error


class AtomicCollectionWriteTests(unittest.TestCase):
    def test_one_or_two_short_windows_locks_eventually_save_the_complete_checkpoint(self):
        for code in (5, 32, 33):
            for locks in (1, 2):
                with self.subTest(winerror=code, locks=locks), tempfile.TemporaryDirectory() as directory:
                    target = Path(directory) / 'collection.json'
                    target.write_text('{"previous":true}', encoding='utf-8')
                    payload = {'complete': True, 'source': {'name': 'Jos\u00e9', 'unknown': None,
                                                          'records': [0, False, {'status': 'partial'}]}}
                    replace = Path.replace
                    attempts = []

                    def briefly_locked(source, destination):
                        attempts.append(source)
                        if len(attempts) <= locks:
                            raise windows_lock(code)
                        return replace(source, destination)

                    with patch('Core.offline_collection._platform_name', 'nt'), \
                         patch.object(Path, 'replace', autospec=True, side_effect=briefly_locked), \
                         patch('Core.offline_collection.time.sleep') as delay:
                        _write_collection_json(target, payload)
                    self.assertEqual(json.loads(target.read_text(encoding='utf-8')), payload)
                    self.assertEqual(len(attempts), locks + 1)
                    self.assertEqual(len(set(attempts)), 1, 'Retries must use the same completed temporary document.')
                    self.assertEqual(delay.call_args_list, [call(0.05 * position) for position in range(1, locks + 1)])
                    self.assertEqual(list(Path(directory).glob('*.tmp')), [])

    def test_final_windows_lock_failure_retains_the_previous_document_and_removes_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'collection.json'
            target.write_text('{"previous":[true,0,null]}', encoding='utf-8')
            previous = target.read_bytes()
            with patch('Core.offline_collection._platform_name', 'nt'), \
                 patch.object(Path, 'replace', side_effect=windows_lock()) as replace, \
                 patch('Core.offline_collection.time.sleep') as delay:
                with self.assertRaises(PermissionError):
                    _write_collection_json(target, {'new': 'This must not partially overwrite the checkpoint.'})
            self.assertEqual(replace.call_count, 3)
            self.assertEqual(delay.call_args_list, [call(0.05), call(0.1)])
            self.assertEqual(target.read_bytes(), previous)
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])

    def test_unrelated_io_or_permission_error_is_reported_without_delay(self):
        for failure in (OSError(errno.ENOSPC, 'Synthetic full disk'),
                        PermissionError(errno.EACCES, 'Synthetic Unix permission error'),
                        windows_lock(1314)):
            with self.subTest(error=str(failure)), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / 'collection.json'
                target.write_text('{"previous":true}', encoding='utf-8')
                previous = target.read_bytes()
                with patch('Core.offline_collection._platform_name', 'nt'), \
                     patch.object(Path, 'replace', side_effect=failure) as replace, \
                     patch('Core.offline_collection.time.sleep') as delay:
                    with self.assertRaises(type(failure)):
                        _write_collection_json(target, {'new': True})
                self.assertEqual(replace.call_count, 1)
                delay.assert_not_called()
                self.assertEqual(target.read_bytes(), previous)
                self.assertEqual(list(Path(directory).glob('*.tmp')), [])

    def test_non_windows_errors_are_not_retried_even_with_a_windows_error_attribute(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'collection.json'
            with patch('Core.offline_collection._platform_name', 'posix'), \
                 patch.object(Path, 'replace', side_effect=windows_lock()) as replace, \
                 patch('Core.offline_collection.time.sleep') as delay:
                with self.assertRaises(PermissionError):
                    _write_collection_json(target, {'new': True})
            self.assertEqual(replace.call_count, 1)
            delay.assert_not_called()
            self.assertFalse(target.exists())
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])

    def test_unlocked_atomic_write_saves_without_delay(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'collection.json'
            with patch('Core.offline_collection.time.sleep') as delay:
                _write_collection_json(target, {'complete': True})
            self.assertEqual(json.loads(target.read_text(encoding='utf-8')), {'complete': True})
            delay.assert_not_called()


if __name__ == '__main__':
    unittest.main()
