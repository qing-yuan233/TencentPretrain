"""
  This script provides an example to use DeepSpeed for classification inference.
"""
import sys
import os
import torch
import argparse
import collections
import torch.nn as nn
import deepspeed
import torch.distributed as dist


tencentpretrain_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(tencentpretrain_dir)

from tencentpretrain.opts import *
from inference.run_classifier_infer import *
from tencentpretrain.utils.logging import *
from finetune.run_classifier_deepspeed import read_dataset, load_model

def batch_loader(batch_size, src, seg, is_pad):
    instances_num = src.size()[0]
    for i in range(instances_num // batch_size):
        src_batch = src[i * batch_size : (i + 1) * batch_size, :]
        seg_batch = seg[i * batch_size : (i + 1) * batch_size, :]
        is_pad_batch = is_pad[i * batch_size : (i + 1) * batch_size]
        yield src_batch, seg_batch, is_pad_batch
    if instances_num > instances_num // batch_size * batch_size:
        src_batch = src[instances_num // batch_size * batch_size :, :]
        seg_batch = seg[instances_num // batch_size * batch_size :, :]
        is_pad_batch = is_pad[instances_num // batch_size * batch_size :]
        yield src_batch, seg_batch, is_pad_batch
        
def predict(args, dataset):
    src = torch.LongTensor([sample[0] for sample in dataset])
    seg = torch.LongTensor([sample[2] for sample in dataset])
    is_pad = torch.LongTensor([sample[3] for sample in dataset])

    batch_size = args.batch_size

    args.model.eval()

    result = []
    for i, (src_batch, seg_batch, is_pad_batch) in enumerate(batch_loader(batch_size, src, seg, is_pad)):
        src_batch = src_batch.to(args.device)
        seg_batch = seg_batch.to(args.device)
        is_pad_batch = is_pad_batch.to(args.device)
        with torch.no_grad():
            _, logits = args.model(src_batch, None, seg_batch)
        is_pad_batch = is_pad_batch.view(is_pad_batch.shape[0],1)
        logits_all = torch.cat((logits, is_pad_batch), dim=-1)
        result.append(logits_all)
    result = torch.cat(result, dim=0)
    return result

def main():
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    infer_opts(parser)
    
    parser.add_argument("--world_size", type=int, default=1,
                        help="Total number of processes (GPUs) for training.")
    parser.add_argument("--labels_num", type=int, required=True,
                        help="Number of prediction labels.")
    log_opts(parser)

    tokenizer_opts(parser)

    parser.add_argument("--output_logits", action="store_true", help="Write logits to output file.")
    parser.add_argument("--output_prob", action="store_true", help="Write probabilities to output file.")

    deepspeed_opts(parser)

    args = parser.parse_args()

    # Load the hyperparameters from the config file.
    args = load_hyperparam(args)

    # Build tokenizer.
    args.tokenizer = str2tokenizer[args.tokenizer](args)

    # Build classification model and load parameters.
    args.soft_targets, args.soft_alpha = False, False
    deepspeed.init_distributed()
    # Build classification model.
    model = Classifier(args)
    load_model(args, model, args.load_model_path)
    
    # Get logger.
    args.logger = init_logger(args)

    model = deepspeed.initialize(model=model,config_params=args.deepspeed_config)[0]
    args.model = model

    args.rank = dist.get_rank()
    args.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    dataset = predict(args, read_dataset(args, args.test_path)[args.rank])
    output = torch.as_tensor(dataset).to(args.device)
    output_list = [torch.zeros_like(output).to(args.device) for _ in range(args.world_size)]
    dist.all_gather(output_list, output)

    if args.rank == 0:
        with open(args.prediction_path, mode="w", encoding="utf-8") as f:
            f.write("label")
            if args.output_logits:
                f.write("\t" + "logits")
            if args.output_prob:
                f.write("\t" + "prob")
            f.write("\n")
            for logits_all in output_list:
                logits = logits_all[:,:-1]
                is_pad = logits_all[:,-1]

                pred = torch.argmax(logits, dim=1)
                pred = pred.cpu().numpy().tolist()
                prob = nn.Softmax(dim=1)(logits)
                prob   = prob.cpu().numpy().tolist()
                logits = logits.cpu().numpy().tolist()
                pad = is_pad.cpu().numpy().tolist()
                for j in range(len(pred)):
                    if pad[j] == 1:
                        continue
                    f.write(str(pred[j]))
                    if args.output_logits:
                        f.write("\t" + " ".join([str(v) for v in logits[j]]))
                    if args.output_prob:
                        f.write("\t" + " ".join([str(v) for v in prob[j]]))
                    f.write("\n")
            f.close()

if __name__ == "__main__":
    main()
