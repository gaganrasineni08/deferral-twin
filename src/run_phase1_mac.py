"""Phase 1 evaluation on a Mac. Usage (from project root):
   python src/run_phase1_mac.py [--model microsoft/Phi-3-mini-4k-instruct]
Uses Apple GPU (MPS) if available, else CPU."""
import argparse, sys, os
sys.path.insert(0, os.path.dirname(__file__))
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from eval_benchmark import main

ap = argparse.ArgumentParser()
ap.add_argument("--model", default="microsoft/Phi-3-mini-4k-instruct")
ap.add_argument("--benchmark_dir", default="data/benchmark/twin2k500")
a = ap.parse_args()

device = "mps" if torch.backends.mps.is_available() else "cpu"
dtype = torch.float16 if device == "mps" else torch.float32
print(f"Device: {device}, dtype: {dtype}")
tok = AutoTokenizer.from_pretrained(a.model)
model = AutoModelForCausalLM.from_pretrained(a.model, torch_dtype=dtype).to(device)

def real_llm_generate(system_prompt, query, context):
    msgs = [{"role": "system", "content": system_prompt}, {"role": "user", "content": query}]
    prompt = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    inp = tok(prompt, return_tensors="pt").to(device)
    with torch.no_grad():
        out = model.generate(**inp, max_new_tokens=80, do_sample=False, pad_token_id=tok.eos_token_id)
    return tok.decode(out[0][inp["input_ids"].shape[-1]:], skip_special_tokens=True).strip()

main(a.benchmark_dir, real_llm_generate)
