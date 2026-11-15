"""ONNX embedding channels: BGE-small-zh text vectors and Chinese-CLIP image/text vectors.

Both embedders are offline and CPU-only (onnxruntime + tokenizers + PIL/numpy);
torch/transformers/jieba/cv2 are deliberately not used. Heavy imports happen
inside __init__ so the module stays importable (and fakeable) without the
runtimes installed. Missing model files raise EmbedderUnavailable; callers log
and disable the channel instead of failing the build.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

LOGGER = logging.getLogger('material_embed')


class EmbedderUnavailable(RuntimeError):
    """Model directory lacks the expected files or the runtime is not installed."""


def _find_model(directory, names):
    # Hugging Face ONNX exports keep weights in onnx/ beside the tokenizer files.
    for folder in (Path(directory), Path(directory) / 'onnx'):
        for name in names:
            path = folder / name
            if path.is_file():
                return path
    return None


def try_text_embedder(model_dir, **kwargs):
    """(embedder | None, reason) — the graceful-disable path used by CLIs."""
    try:
        return TextEmbedder(model_dir, **kwargs), None
    except EmbedderUnavailable as error:
        LOGGER.warning('material_embed:text_channel_disabled:%s', error)
        return None, str(error)


def try_clip(model_dir, **kwargs):
    try:
        return ImageTextCLIP(model_dir, **kwargs), None
    except EmbedderUnavailable as error:
        LOGGER.warning('material_embed:image_channel_disabled:%s', error)
        return None, str(error)


def _normalize(matrix):
    matrix = np.asarray(matrix, dtype=np.float32)
    if matrix.ndim != 2:
        raise EmbedderUnavailable('expected a 2-D embedding matrix')
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.maximum(norms, 1e-12)


def _sessions(model_path, threads):
    try:
        import onnxruntime
    except ImportError as error:  # pragma: no cover - depends on host
        raise EmbedderUnavailable('onnxruntime not installed') from error
    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = int(threads)
    options.inter_op_num_threads = 1
    return onnxruntime.InferenceSession(str(model_path), sess_options=options,
                                        providers=['CPUExecutionProvider'])


def _static_length(session, input_name):
    for item in session.get_inputs():
        if item.name == input_name and len(item.shape) == 2:
            length = item.shape[1]
            if isinstance(length, int) and length > 0:
                return length
    return None


def _pick_output(session, wanted):
    names = [item.name for item in session.get_outputs()]
    for name in names:
        if wanted in name.lower():
            return name
    return names[0] if names else None


class TextEmbedder:
    """BGE-small-zh-v1.5 via ONNX: CLS pooling, L2 normalised, query instruction prefix."""

    QUERY_INSTRUCTION = '为这个句子生成表示以用于检索相关文章：'
    MODEL_FILES = ('model.onnx', 'model_int8.onnx', 'model_quantized.onnx')

    def __init__(self, model_dir, *, threads=2, batch_size=32, max_length=512):
        directory = Path(model_dir)
        model = _find_model(directory, self.MODEL_FILES)
        tokenizer_path = directory / 'tokenizer.json'
        if model is None or not tokenizer_path.is_file():
            raise EmbedderUnavailable('text model files missing in ' + str(directory))
        try:
            import tokenizers
        except ImportError as error:  # pragma: no cover - depends on host
            raise EmbedderUnavailable('tokenizers not installed') from error
        self.model_path, self.tokenizer_path = str(model), str(tokenizer_path)
        self.batch_size = max(1, int(batch_size))
        self.tokenizer = tokenizers.Tokenizer.from_file(str(tokenizer_path))
        limit = int(max_length or 512)
        self.tokenizer.enable_truncation(max_length=limit)
        self.session = _sessions(model, threads)
        self._needs_token_type = any(item.name == 'token_type_ids' for item in self.session.get_inputs())
        self._static_length = _static_length(self.session, 'input_ids')
        self._output = _pick_output(self.session, 'last_hidden_state')
        sample = self.encode(['维度探测'])
        self.dim = int(sample.shape[1])

    def _feed(self, texts):
        encodings = self.tokenizer.encode_batch([str(text) for text in texts])
        length = self._static_length or max(len(encoding.ids) for encoding in encodings)
        input_ids = np.zeros((len(encodings), length), dtype=np.int64)
        attention = np.zeros((len(encodings), length), dtype=np.int64)
        for row, encoding in enumerate(encodings):
            ids = list(encoding.ids)[:length]
            mask = list(encoding.attention_mask)[:length]
            input_ids[row, :len(ids)] = ids
            attention[row, :len(mask)] = mask
        feed = {'input_ids': input_ids, 'attention_mask': attention}
        if self._needs_token_type:
            feed['token_type_ids'] = np.zeros_like(input_ids)
        return feed

    def encode(self, texts):
        """Documents: no instruction prefix, batched, CLS pooled, L2 normalised."""
        texts = list(texts)
        rows = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start:start + self.batch_size]
            feed = self._feed(batch)
            output = self.session.run([self._output], feed)[0]
            if output.ndim == 3:  # last_hidden_state -> [CLS]
                output = output[:, 0]
            rows.append(_normalize(output))
        return np.concatenate(rows, axis=0) if rows else np.zeros((0, self.dim), dtype=np.float32)

    def encode_query(self, texts):
        """Queries: instruction prefix, then the same encoder path as documents."""
        return self.encode([self.QUERY_INSTRUCTION + str(text) for text in texts])


class ImageTextCLIP:
    """Chinese-CLIP ViT-B/16 via a merged ONNX export (text and image towers).

    The Xenova-style single model takes input_ids/attention_mask/pixel_values and
    returns text_embeds/image_embeds; the unused tower gets a dummy feed because
    its outputs are simply not read. Image preprocessing is implemented with
    PIL + numpy from preprocessor_config.json (no torchvision/cv2).
    """

    MODEL_FILES = ('model.onnx', 'model_quantized.onnx')

    def __init__(self, model_dir, *, threads=2, batch_size=8):
        directory = Path(model_dir)
        model = _find_model(directory, self.MODEL_FILES)
        tokenizer_path = directory / 'tokenizer.json'
        config_path = directory / 'preprocessor_config.json'
        if model is None or not tokenizer_path.is_file() or not config_path.is_file():
            raise EmbedderUnavailable('clip model files missing in ' + str(directory))
        try:
            import tokenizers
        except ImportError as error:  # pragma: no cover - depends on host
            raise EmbedderUnavailable('tokenizers not installed') from error
        self.model_path = str(model)
        self.batch_size = max(1, int(batch_size))
        config = json.loads(config_path.read_text(encoding='utf-8'))
        size = config.get('size') or {}
        self.shortest_edge = int(size.get('shortest_edge') or size.get('height') or 224)
        # Chinese-CLIP processors give size {height, width}: resize exactly, no crop.
        self.exact_size = ((int(size['width']), int(size['height']))
                           if 'shortest_edge' not in size and size.get('height') and size.get('width') else None)
        crop = config.get('crop_size') or {}
        if isinstance(crop, int):  # some exports use a square int crop size
            crop = {'width': crop, 'height': crop}
        self.crop_size = (int(crop.get('width') or crop.get('height') or 224),
                          int(crop.get('height') or crop.get('width') or 224))
        self.image_mean = np.asarray(config.get('image_mean') or [0.48145466, 0.4578275, 0.40821073], np.float32)
        self.image_std = np.asarray(config.get('image_std') or [0.26862954, 0.26130258, 0.27577711], np.float32)
        self.rescale_factor = float(config.get('rescale_factor') or 1.0 / 255.0)
        self.do_resize = bool(config.get('do_resize', True))
        self.do_center_crop = bool(config.get('do_center_crop', True))
        self.do_normalize = bool(config.get('do_normalize', True))
        self.tokenizer = tokenizers.Tokenizer.from_file(str(tokenizer_path))
        self.tokenizer.enable_truncation(max_length=52)
        self.session = _sessions(model, threads)
        self._inputs = {item.name for item in self.session.get_inputs()}
        self._text_output = _pick_output(self.session, 'text_embeds')
        self._image_output = _pick_output(self.session, 'image_embeds')
        self._text_length = _static_length(self.session, 'input_ids')
        self._probe = None
        try:
            sample = self.encode_images([self._probe_image()])
            self.dim = int(sample.shape[1])
        finally:
            if self._probe:
                Path(self._probe).unlink(missing_ok=True)
                self._probe = None

    def _probe_image(self):
        """One synthetic frame in a temp file, so dim detection needs no assets."""
        import tempfile
        from PIL import Image

        handle = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
        Image.new('RGB', (self.shortest_edge, self.shortest_edge), (127, 127, 127)).save(handle, 'JPEG')
        handle.close()
        self._probe = handle.name
        return handle.name

    # -- preprocessing -----------------------------------------------------
    def preprocess(self, path):
        """resize shortest side -> center crop -> rescale -> normalise -> CHW."""
        from PIL import Image

        with Image.open(path) as image:
            frame = image.convert('RGB')
            exact = getattr(self, 'exact_size', None)
            if self.do_resize and exact:
                frame = frame.resize(exact, Image.BICUBIC)
            elif self.do_resize:
                width, height = frame.size
                scale = self.shortest_edge / min(width, height)
                frame = frame.resize((max(1, round(width * scale)), max(1, round(height * scale))),
                                     Image.BICUBIC)
            if frame.size != self.crop_size and not self.do_center_crop:
                # The vision tower needs exactly crop_size pixels; never feed another shape.
                frame = frame.resize(self.crop_size, Image.BICUBIC)
            if self.do_center_crop:
                width, height = frame.size
                left = (width - self.crop_size[0]) // 2
                top = (height - self.crop_size[1]) // 2
                frame = frame.crop((left, top, left + self.crop_size[0], top + self.crop_size[1]))
            array = np.asarray(frame, dtype=np.float32) * self.rescale_factor
        if self.do_normalize:
            array = (array - self.image_mean) / self.image_std
        return np.ascontiguousarray(array.transpose(2, 0, 1), dtype=np.float32)

    def _feed_images(self, pixel_values):
        feed = {'pixel_values': np.asarray(pixel_values, dtype=np.float32)}
        if 'input_ids' in self._inputs:  # unused text tower keeps shapes valid
            feed['input_ids'] = np.zeros((len(pixel_values), 2), dtype=np.int64)
            feed['attention_mask'] = np.ones((len(pixel_values), 2), dtype=np.int64)
        return feed

    def _feed_texts(self, texts):
        encodings = self.tokenizer.encode_batch([str(text) for text in texts])
        length = self._text_length or max(len(encoding.ids) for encoding in encodings)
        input_ids = np.zeros((len(encodings), length), dtype=np.int64)
        attention = np.zeros((len(encodings), length), dtype=np.int64)
        for row, encoding in enumerate(encodings):
            ids = list(encoding.ids)[:length]
            input_ids[row, :len(ids)] = ids
            attention[row, :len(ids)] = list(encoding.attention_mask)[:length]
        feed = {'input_ids': input_ids, 'attention_mask': attention}
        if 'pixel_values' in self._inputs:
            feed['pixel_values'] = np.zeros((len(texts), 3, self.crop_size[1], self.crop_size[0]), np.float32)
        return feed

    # -- encoding ----------------------------------------------------------
    def encode_images(self, paths, *, batch_size=None):
        """Keyframes in batches; only one batch of decoded frames is resident."""
        paths = list(paths)
        batch_size = max(1, int(batch_size or self.batch_size))
        rows = []
        for start in range(0, len(paths), batch_size):
            batch = paths[start:start + batch_size]
            frames = [self.preprocess(path) for path in batch]
            output = self.session.run([self._image_output], self._feed_images(frames))[0]
            rows.append(_normalize(output))
        return np.concatenate(rows, axis=0) if rows else np.zeros((0, self.dim), dtype=np.float32)

    def encode_texts(self, texts):
        texts = list(texts)
        rows = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start:start + self.batch_size]
            output = self.session.run([self._text_output], self._feed_texts(batch))[0]
            rows.append(_normalize(output))
        return np.concatenate(rows, axis=0) if rows else np.zeros((0, self.dim), dtype=np.float32)
