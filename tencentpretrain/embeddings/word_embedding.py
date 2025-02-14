import math
import torch.nn as nn
from tencentpretrain import mpu


class WordEmbedding(nn.Module):
    """
    """

    def __init__(self, args, vocab_size):
        super(WordEmbedding, self).__init__()
        if hasattr(args, "tensor_model_parallel_size") and args.tensor_model_parallel_size > 1:
            self.embedding = mpu.VocabParallelEmbedding(vocab_size, args.emb_size)
        else:
            self.embedding = nn.Embedding(vocab_size, args.emb_size)
        self.emb_size = args.emb_size
        self.sinusoidalpos = False
        if "sinusoidalpos" in args.embedding:
            self.sinusoidalpos = True


    def forward(self, src, _):
        """
        Args:
            src: [batch_size x seq_length]
            seg: [batch_size x seq_length]
        Returns:
            emb: [batch_size x seq_length x hidden_size]
        """

        emb = self.embedding(src)

        if self.sinusoidalpos:
            return emb * math.sqrt(self.emb_size)
        else:
            return emb
