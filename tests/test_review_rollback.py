"""Offline lifecycle regressions; every generated file lives in TemporaryDirectory."""
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SOURCE = Path(os.environ.get('AOP_SOURCE', Path(__file__).resolve().parents[1] / 'aop.py'))
spec = importlib.util.spec_from_file_location('aop_under_test', SOURCE)
aop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(aop)


class ReviewRollbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_cwd, self.old_root = os.getcwd(), aop.ROOT
        os.chdir(self.tmp.name)
        aop.ROOT = str(Path(self.tmp.name).resolve())
        self.target = Path(self.tmp.name) / 'pkg' / 'sample.py'
        self.contract = Path(self.tmp.name) / 'tests' / 'test_sample.py'
        self.target.parent.mkdir()
        self.before = b'ORIGINAL\r\n'
        self.plan = {'file': 'pkg/sample.py', 'test_file': 'tests/test_sample.py',
                     'summary': 'synthetic feature', 'test_code': 'assert True\n'}

    def tearDown(self):
        os.chdir(self.old_cwd)
        aop.ROOT = self.old_root
        self.tmp.cleanup()

    def invoke(self, review_result='reviewed', review_error=None, gate=(True, '')):
        reviewer = mock.Mock(return_value=review_result, side_effect=review_error)
        with mock.patch.multiple(aop,
                scout=mock.Mock(return_value=(self.plan, str(self.target), str(self.contract))),
                failing_tests=mock.Mock(return_value=(set(), None)),
                build=mock.Mock(return_value='def feature(): return 2\n'),
                gate=mock.Mock(return_value=gate), review=reviewer):
            aop.run('synthetic feature')

    def test_review_transport_failure_restores_exact_existing_bytes(self):
        self.target.write_bytes(self.before)
        with self.assertRaisesRegex(RuntimeError, 'review unavailable'):
            self.invoke(review_error=RuntimeError('review unavailable'))
        self.assertEqual(self.target.read_bytes(), self.before)
        self.assertFalse(self.contract.exists())

    def test_review_cancellation_removes_new_target_and_contract(self):
        with self.assertRaises(KeyboardInterrupt):
            self.invoke(review_error=KeyboardInterrupt())
        self.assertFalse(self.target.exists())
        self.assertFalse(self.contract.exists())

    def test_success_keeps_the_validated_work(self):
        self.target.write_bytes(self.before)
        self.invoke()
        self.assertEqual(self.target.read_text(), 'def feature(): return 2\n')
        self.assertEqual(self.contract.read_text(), 'assert True\n')

    def test_exhausted_gate_still_restores_the_declared_files(self):
        self.target.write_bytes(self.before)
        with self.assertRaises(SystemExit):
            self.invoke(gate=(False, 'synthetic gate refusal'))
        self.assertEqual(self.target.read_bytes(), self.before)
        self.assertFalse(self.contract.exists())


class BufferedIOFailureTests(unittest.TestCase):
    """A successful write() can still fail when its buffered data is closed."""
    def failing_open(self, *args, **kwargs):
        class DiskFull(io.RawIOBase):
            def writable(self):
                return True

            def write(self, data):
                raise OSError('synthetic disk full during buffered flush')

        stream = io.BufferedWriter(DiskFull())
        if 'b' not in args[1]:
            stream = io.TextIOWrapper(stream)
        self.streams.append(stream)
        return stream

    def setUp(self):
        self.streams = []

    def tearDown(self):
        for stream in self.streams:
            try:
                stream.close()
            except OSError:
                pass

    def test_generated_write_surfaces_buffered_flush_failure(self):
        with mock.patch.object(aop, 'open', side_effect=self.failing_open, create=True):
            with self.assertRaisesRegex(OSError, 'synthetic disk full'):
                aop.write('synthetic.py', 'value = 1\n')

    def test_restore_surfaces_buffered_flush_failure(self):
        with mock.patch.object(aop, 'open', side_effect=self.failing_open, create=True):
            with self.assertRaisesRegex(OSError, 'synthetic disk full'):
                aop.restore({'synthetic.py': b'ORIGINAL\r\n'})


if __name__ == '__main__':
    unittest.main()
