import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import server


class Commands(unittest.TestCase):
    def test_bench_dispatches_and_exits_without_mcp(self):
        with patch.object(server, '_bench') as bench, patch.object(server.mcp, 'run') as run:
            server.main(['bench', 'sample.pdf', '--repeats', '3'])
        bench.assert_called_once_with('sample.pdf', 3)
        run.assert_not_called()

    def test_unknown_command_exits_instead_of_waiting_for_mcp(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            server.main(['benhc', 'sample.pdf'])
        self.assertEqual(caught.exception.code, 2)

    def test_force_is_forwarded_to_conversion(self):
        with patch.object(server, '_ensure', return_value=('key', {'pages': 3, 'est_tokens': 30, 'seconds': 1}, None)) as ensure:
            server.main(['convert', 'sample.pdf', '--force'])
        self.assertTrue(ensure.call_args.kwargs['force'])

    def test_benchmark_runs_without_document_cache(self):
        conv = Mock()
        with patch.object(server, '_resolve', return_value=Path('sample.pdf')), \
             patch.object(server, '_device_info', return_value={'torch': 'test', 'available': ['cpu']}), \
             patch.object(server, '_get_converter', return_value=conv), \
             patch.object(server, '_ensure') as ensure, contextlib.redirect_stdout(io.StringIO()) as out:
            server._bench('sample.pdf', repeats=2)
        self.assertEqual(conv.convert.call_count, 3)  # warmup + two measurements
        ensure.assert_not_called()
        self.assertIn('CPU만 측정', out.getvalue())

    def test_gpu_failure_retries_document_on_cpu(self):
        gpu, cpu = Mock(), Mock()
        gpu.convert.side_effect = RuntimeError('GPU failure')
        doc = cpu.convert.return_value.document
        doc.export_to_markdown.return_value = '# Test'
        doc.num_pages.return_value = 1
        doc.iterate_items.return_value = []
        doc.pictures = doc.tables = []
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as cache, \
             patch.object(server, 'CACHE_DIR', Path(cache)), \
             patch.object(server, '_device_info', return_value={'selected': 'xpu'}), \
             patch.object(server, '_get_converter', side_effect=[gpu, cpu]) as get:
            meta = server._build_cache(Path('sample.pdf'), 'test', False, False)
        self.assertEqual(meta['device'], 'cpu')
        self.assertEqual(get.call_args_list[1].args[2], 'cpu')


if __name__ == '__main__':
    unittest.main()
