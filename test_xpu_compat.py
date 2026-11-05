import contextlib
import io
import unittest
from types import SimpleNamespace

import torch
from transformers.models.rt_detr.image_processing_rt_detr import RTDetrImageProcessor
from xpu_compat import enable_xpu_postprocess_workaround


class XpuPostprocess(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        enable_xpu_postprocess_workaround()

    def test_install_is_idempotent(self):
        before = RTDetrImageProcessor.post_process_object_detection
        enable_xpu_postprocess_workaround()
        self.assertIs(before, RTDetrImageProcessor.post_process_object_detection)

    def test_cpu_values_remain_correct(self):
        self._check('cpu')

    @unittest.skipUnless(torch.xpu.is_available(), 'Intel GPU required')
    def test_xpu_matches_cpu_for_scores_labels_and_boxes(self):
        self._check('xpu')

    def _check(self, device):
        processor = RTDetrImageProcessor()
        # Deterministic detections exercise topk, box gathering and boolean filtering.
        logits = torch.tensor([[[4., -4.], [-4., 3.], [-3., -4.]]])
        boxes = torch.tensor([[[.5,.5,.2,.2],[.3,.4,.1,.2],[.8,.8,.1,.1]]])
        reference = processor.post_process_object_detection(
            SimpleNamespace(logits=logits, pred_boxes=boxes), target_sizes=torch.tensor([[100, 200]]))
        inputs = SimpleNamespace(logits=logits.to(device), pred_boxes=boxes.to(device))
        actual = processor.post_process_object_detection(
            inputs, target_sizes=torch.tensor([[100, 200]], device=device))
        self.assertEqual(inputs.logits.device.type, device)
        self.assertEqual(reference[0]['labels'].tolist(), [0, 1])
        for key in ('scores', 'labels', 'boxes'):
            torch.testing.assert_close(actual[0][key].cpu(), reference[0][key])
        self.assertTrue(all(v.device.type == 'cpu' for v in actual[0].values()))


if __name__ == '__main__':
    unittest.main()
