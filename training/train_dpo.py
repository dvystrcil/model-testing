#!/usr/bin/env python3
"""
DPO fine-tuning trainer for K8s constraint alignment.

Reads a DPO JSONL dataset (from generate_dpo_dataset.py) and fine-tunes
a causal LM using trl.DPOTrainer on AMD ROCm.

Usage:
    python training/train_dpo.py \
        --model qwen3-coder-next \
        --dataset /data/k8s_resources_dpo.jsonl \
        --output /output/adapter \
        --epochs 3 \
        --lora-r 16
"""

import argparse
import json
import os
from pathlib import Path

import torch
from datasets import Dataset
from peft import LoraConfig, TaskType, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments
from trl import DPOConfig, DPOTrainer


def load_dpo_dataset(path: Path) -> Dataset:
    records = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        # trl DPOTrainer expects: prompt (str), chosen (str), rejected (str)
        # Our prompt is a messages list — flatten to a single string with role markers
        prompt_msgs = r["prompt"]
        prompt_str = "\n".join(
            f"<|{m['role']}|>\n{m['content']}" for m in prompt_msgs
        )
        records.append({
            "prompt": prompt_str,
            "chosen": r["chosen"],
            "rejected": r["rejected"],
        })
    return Dataset.from_list(records)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True,
                   help="HuggingFace model ID or local path (e.g. Qwen/Qwen2.5-Coder-14B-Instruct)")
    p.add_argument("--dataset", required=True, help="DPO JSONL file from generate_dpo_dataset.py")
    p.add_argument("--output", default="/output/adapter", help="Output path for PEFT adapter")
    p.add_argument("--epochs", type=int, default=3)
    p.add_argument("--batch-size", type=int, default=2)
    p.add_argument("--grad-accum", type=int, default=4)
    p.add_argument("--lora-r", type=int, default=16)
    p.add_argument("--lora-alpha", type=int, default=32)
    p.add_argument("--lora-dropout", type=float, default=0.05)
    p.add_argument("--beta", type=float, default=0.1, help="DPO temperature")
    p.add_argument("--max-length", type=int, default=2048)
    p.add_argument("--max-prompt-length", type=int, default=512)
    p.add_argument("--lr", type=float, default=5e-5)
    args = p.parse_args()

    print(f"Loading dataset from {args.dataset}")
    dataset = load_dpo_dataset(Path(args.dataset))
    print(f"  {len(dataset)} preference pairs")

    print(f"Loading tokenizer and model: {args.model}")
    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        args.model,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        trust_remote_code=True,
    )

    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
        bias="none",
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    dpo_config = DPOConfig(
        output_dir=args.output,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        beta=args.beta,
        max_length=args.max_length,
        max_prompt_length=args.max_prompt_length,
        logging_steps=10,
        save_strategy="epoch",
        bf16=True,
        remove_unused_columns=False,
        report_to="none",
    )

    trainer = DPOTrainer(
        model=model,
        ref_model=None,  # implicit reference via PEFT — saves VRAM vs loading two full models
        args=dpo_config,
        train_dataset=dataset,
        tokenizer=tokenizer,
    )

    print("Starting DPO training")
    trainer.train()

    print(f"Saving adapter to {args.output}")
    trainer.save_model(args.output)
    tokenizer.save_pretrained(args.output)
    print("Done")


if __name__ == "__main__":
    main()
