"""One customer report folder, immutable prior builds and legacy compatibility."""
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

from provenance_fixture import fictional_publication
from test_assessment_run_workflow import RunFixture
from synthetic_package_fixture import create_synthetic_package


@fictional_publication
class FlatAllocationTests(unittest.TestCase):
    def test_same_deliverables_folder_and_atomic_distinct_report_families(self):
        from Core.export_paths import new_deliverables_directory, reserve_report_names
        with tempfile.TemporaryDirectory() as directory:
            package=Path(directory)
            (package/'assessment-run.json').write_text('{}')
            folder=new_deliverables_directory(package)
            self.assertEqual(folder,package/'Deliverables')
            self.assertEqual(new_deliverables_directory(package),folder)
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures=[executor.submit(copy_context().run,reserve_report_names,folder,tenant_name='Example') for _ in range(2)]
                names=[future.result() for future in futures]
            self.assertEqual(sorted(name['number'] for name in names),[1,2])
            self.assertEqual(len({name['report_stem'] for name in names}),2)
            self.assertEqual(len(list(package.glob('build-*.json'))),2)
            self.assertEqual(list(folder.iterdir()),[])

    def test_completed_metadata_is_still_protected(self):
        from Core.publication_io import validate_destination
        with tempfile.TemporaryDirectory() as directory:
            package=Path(directory)
            (package/'assessment-run.json').write_text('{}')
            validate_destination(package/'Deliverables'/'new.html')
            validate_destination(package/'build-1.json',artifact_kind='build-reservation')
            for name in ('collection.json','run-seed.json','build-0.json','build-01.json','build-arbitrary.json'):
                with self.subTest(name=name), self.assertRaises(ValueError):
                    validate_destination(package/name,artifact_kind='build-reservation')

    def test_failed_build_receipts_only_its_partial_files(self):
        from contextlib import redirect_stdout
        import io
        from Core.assessment_package import render_with_failure_receipt
        from Core.export_paths import capture_report_build, new_deliverables_directory, reserve_report_names
        with tempfile.TemporaryDirectory() as directory:
            package=Path(directory)
            folder=new_deliverables_directory(package)
            first=reserve_report_names(folder,tenant_name='Example')
            original=folder/(first['report_stem']+'.html')
            original.write_text('original')
            def interrupted(*,output_dir):
                names=reserve_report_names(output_dir,tenant_name='Example')
                (output_dir/(names['report_stem']+'.html')).write_text('partial')
                # Another concurrent report family must stay out of this receipt.
                def concurrent():
                    with capture_report_build():
                        other=reserve_report_names(output_dir,tenant_name='Example')
                        (output_dir/(other['report_stem']+'.html')).write_text('other build')
                with ThreadPoolExecutor(max_workers=1) as executor:
                    executor.submit(copy_context().run,concurrent).result()
                raise ValueError('Synthetic interruption')
            with redirect_stdout(io.StringIO()),self.assertRaisesRegex(ValueError,'Synthetic interruption'):
                render_with_failure_receipt(interrupted,output_dir=folder,
                    receipt={'folder':package,'mode':'offline','tenant_id':None,
                             'collected_at':None,'evaluation_date':'2026-10-26'})
            receipt=json.loads((package/'operator-log.jsonl').read_text())
            self.assertEqual(receipt['status'],'failed')
            self.assertEqual(receipt['run_id'],'2')
            self.assertEqual(receipt['deliverables'],['Deliverables/AI Readiness and M365 Hardening - Example (2).html'])
            self.assertEqual(original.read_text(),'original')

    def test_legacy_packages_keep_numbered_builds(self):
        from Core.export_paths import new_deliverables_directory
        with tempfile.TemporaryDirectory() as directory:
            package=Path(directory)
            first=package/'Builds'/'1'
            first.mkdir(parents=True)
            (first/'original.html').write_text('original')
            self.assertEqual(new_deliverables_directory(package),package/'Builds'/'2')
            self.assertEqual((first/'original.html').read_text(),'original')
            self.assertFalse((package/'Deliverables').exists())


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets=[]
    def handle_starttag(self,tag,attributes):
        if tag=='a':
            self.targets.extend(value for key,value in attributes if key=='href' and value)


@fictional_publication
class FlatExecutionTests(RunFixture):
    def execute(self,*options):
        from test_reassessment_cli import NestedReassessmentExecutionTests
        return NestedReassessmentExecutionTests.execute(self,*options)

    def initial_package(self):
        source=create_synthetic_package(self.root/'source')
        self.execute('--collection-input',source,'--run-type','Initial')
        return next(self.root.glob('Reports/*/*/assessment-history.json')).parent

    def test_replay_keeps_one_folder_original_bytes_and_only_new_files_in_receipt(self):
        package=self.initial_package()
        folder=package/'Deliverables'
        originals={path:path.read_bytes() for path in folder.iterdir()}
        self.assertEqual(len(originals),4)
        history=package/'assessment-history.json'
        protected={path:path.read_bytes() for path in [history,package/'collection.json',*package.glob('assessment-results/*.json')]}
        with patch('Core.assessment_result.build_assessment_result',side_effect=AssertionError('Replay reevaluated evidence')):
            self.execute('--collection-input',package/'collection.json')
        for path,contents in {**originals,**protected}.items():
            self.assertEqual(path.read_bytes(),contents,str(path))
        self.assertEqual(len(list(folder.iterdir())),8)
        self.assertFalse((package/'Builds').exists())
        self.assertFalse((package/'Renders').exists())
        receipts=[json.loads(line) for line in (package/'operator-log.jsonl').read_text().splitlines()]
        self.assertEqual([receipt['run_id'] for receipt in receipts],['1','2'])
        for receipt in receipts:
            self.assertEqual(len(receipt['deliverables']),4)
            self.assertTrue(all(Path(path).parent==Path('Deliverables') for path in receipt['deliverables']))
        self.assertFalse(set(receipts[0]['deliverables']) & set(receipts[1]['deliverables']))
        for page in folder.glob('*.html'):
            links=Links()
            links.feed(page.read_text(encoding='utf-8'))
            for target in links.targets:
                value=urlsplit(target)
                if not value.scheme and value.path:
                    self.assertTrue((page.parent/unquote(value.path)).exists(),target)

    def test_reassessment_has_own_deliverables_and_does_not_copy_previous_reports(self):
        from Core.assessment_history import read_history
        package=self.initial_package()
        before={path:path.read_bytes() for path in (package/'Deliverables').iterdir()}
        history=package/'assessment-history.json'
        baseline=read_history(history)['InitialRunId']
        self.execute('--collection-input',package/'collection.json','--reassess',history,'--baseline-run-id',baseline)
        run=package/'Runs'/'1'
        self.assertTrue((run/'Deliverables').is_dir())
        self.assertFalse((run/'Builds').exists())
        self.assertEqual(len(list((run/'Deliverables').iterdir())),4)
        self.assertEqual(len(list(run.glob('build-*.json'))),1)
        self.assertEqual(len(read_history(history)['Runs']),2)
        for path,contents in before.items():
            self.assertEqual(path.read_bytes(),contents)

    def test_old_completed_builds_remain_in_place_during_replay(self):
        package=self.initial_package()
        legacy=package/'Builds'/'1'
        legacy.parent.mkdir()
        source=package/'Deliverables'
        self.assertTrue(source.resolve().is_relative_to(self.root.resolve()))
        self.assertTrue(legacy.resolve().is_relative_to(self.root.resolve()))
        source.rename(legacy)
        log=package/'operator-log.jsonl'
        values=[json.loads(line) for line in log.read_text().splitlines()]
        for value in values:
            value['deliverables']=[path.replace('Deliverables/','Builds/1/') for path in value['deliverables']]
        log.write_text(''.join(json.dumps(value)+'\n' for value in values))
        originals={path:path.read_bytes() for path in legacy.iterdir()}
        self.execute('--collection-input',package/'collection.json')
        self.execute('--collection-input',package/'collection.json')
        self.assertFalse((package/'Deliverables').exists())
        self.assertEqual(len(list((package/'Renders').iterdir())),2)
        for path,contents in originals.items():
            self.assertEqual(path.read_bytes(),contents)
