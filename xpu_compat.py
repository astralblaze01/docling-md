"""Keep RT-DETR inference on Intel GPU, but filter its detections on CPU.

Some Intel XPU driver/runtime combinations corrupt boolean indexing/nonzero:
https://github.com/pytorch/pytorch/issues/200713
The small logits/boxes tensors are copied after inference. Model weights and
forward passes remain on XPU. No installed dependency files are modified.
"""
from functools import wraps
import logging
from types import SimpleNamespace

log = logging.getLogger('docling-md')


def enable_xpu_postprocess_workaround() -> None:
    from transformers.models.rt_detr.image_processing_rt_detr import RTDetrImageProcessor

    original = RTDetrImageProcessor.post_process_object_detection
    if getattr(original, '_docling_xpu_safe', False):
        return

    @wraps(original)
    def postprocess(self, outputs, threshold=0.5, target_sizes=None, **kwargs):
        if outputs.logits.device.type == 'xpu':
            # Preserve the caller's ModelOutput and move only the two fields
            # consumed by this processor. The copies also synchronize inference.
            outputs = SimpleNamespace(
                logits=outputs.logits.detach().cpu(),
                pred_boxes=outputs.pred_boxes.detach().cpu(),
            )
            if hasattr(target_sizes, 'cpu'):
                target_sizes = target_sizes.cpu()
        return original(self, outputs, threshold=threshold,
                        target_sizes=target_sizes, **kwargs)

    postprocess._docling_xpu_safe = True
    RTDetrImageProcessor.post_process_object_detection = postprocess
    log.info('Intel XPU 호환 모드: 모델 추론=GPU, RT-DETR 결과 후처리=CPU')
