"""libra: a small GPT-style language model written from scratch on NumPy.

No pretrained weights, no external model, no API key. Train it on your own text.
"""
from .autograd import Tensor, no_grad
from .checkpoint import load, save
from .generate import SamplingParams, generate, stream_text
from .nn import GPT, GPTConfig
from .tokenizer import BPETokenizer

__version__ = "0.1.0"
__all__ = ["Tensor", "no_grad", "GPT", "GPTConfig", "BPETokenizer", "SamplingParams",
           "generate", "stream_text", "load", "save", "__version__"]
