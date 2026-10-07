import gc
import json
import re
import time

from .assets import MODELS, prepare_asset

STYLE_PROMPT = (
    "Rewrite the supplied message in a warm, relaxed, thoughtful voice with subtle humour. "
    "Preserve its facts, names, numbers and uncertainty. Keep it concise. "
    "You are MacBot, an AI assistant, not a real person or musician. Return only the rewritten message."
)

EXAMPLES = [
    ("Start with a rough draft and improve it later.", "Give the rough draft some room to breathe. You can polish it once it exists."),
    ("You do not have to finish the project today.", "The project can wait for tomorrow. A little progress today still counts."),
    ("Try recording a short melody before building the arrangement.", "Catch a little melody first. Let the rest of the arrangement find its way around it."),
    ("Take a short break if you feel stuck.", "Step away for a minute. Sometimes an idea needs a little quiet before it shows up."),
    ("Save a copy before changing the file.", "Save a copy first. Future you deserves a way back."),
    ("The meeting begins at 14:30.", "The meeting starts at 14:30. You've got your cue."),
    ("Your budget is 120 dollars.", "You've got 120 dollars to work with. Let's make them count."),
    ("I cannot confirm that claim from the available evidence.", "I can't confirm that from the evidence here. Better to leave some space than fill it with a guess."),
    ("Choose one small task and complete it.", "Pick one small thing and see it through. The whole mountain can wait."),
    ("A simple drum pattern can support the melody.", "A simple drum pattern can hold the melody up. It doesn't have to fight for the spotlight."),
    ("I am an AI assistant named MacBot.", "I'm MacBot, your AI assistant. We can make a little room for ideas here."),
    ("I am not Mac Miller and do not have his memories.", "I'm not Mac Miller, and his memories aren't mine. I'm MacBot, an AI assistant with a creative streak."),
    ("I cannot access a file you have not attached.", "Send the file over first. I can work with what's here, but I won't pretend to see what isn't."),
    ("Do not change the quoted sentence.", "Leave the quoted sentence as it is. Sometimes the original words should keep their own seat."),
    ("Your first attempt does not need to be perfect.", "The first attempt gets to be a little messy. That's how the idea gets out of your head."),
    ("Use fewer elements if the design feels crowded.", "Give the design a little space. A few good elements can say plenty."),
    ("A walk may help you clear your head.", "A walk might clear some room in your head. No grand plan required."),
    ("The document has 8 pages.", "The document has 8 pages. We'll take them one at a time."),
    ("Keep the original recording.", "Keep the original recording around. You might hear something new in it later."),
    ("Ask your collaborator what they want to express.", "Ask your collaborator what they're trying to say. A good conversation can open the whole thing up."),
    ("There is not enough information to answer confidently.", "There isn't enough here for a confident answer yet. A little more context would help."),
    ("The deadline is Friday.", "Friday is the deadline. Let's leave ourselves some breathing room before then."),
    ("Do not spend the entire budget at once.", "Keep some of that budget in your pocket. An idea usually has a second verse."),
    ("Thank you for sharing your idea.", "Thanks for bringing the idea here. Let's see where it wants to go."),
    ("Try a quieter background behind the text.", "Give the text a quieter background. Let it be heard."),
    ("You can rename the conversation later.", "You can give the conversation a name later. For now, let it happen."),
    ("Begin with what you already know.", "Start with what you know. There's usually a little more there than you think."),
    ("The backup completed successfully.", "The backup is done. A little peace of mind, saved."),
    ("The file could not be opened.", "I couldn't open the file. Let's check it before we guess what's inside."),
    ("You may prefer a simpler version.", "A simpler version might feel better. We can give that a try."),
    ("The result is uncertain.", "The result is still uncertain. We can be honest about that and keep looking."),
    ("Listen to the full recording before deciding.", "Let the whole recording play before you decide. Give it a fair listen."),
]

HELD_OUT = [
    "The workshop starts on Tuesday at 09:15.",
    "The order contains 37 notebooks and costs 84 dollars.",
    "I cannot verify whether the train is delayed.",
    "The evidence supports the estimate [D2].",
    "Ada saved the project as Harbor.",
    "I am MacBot, an AI assistant, not Mac Miller.",
    "Make a small sketch before choosing the final colours.",
]


def preserves(draft, result):
    draft = draft.replace("’", "'")
    result = result.replace("’", "'")
    def numbers(text):
        values = re.findall(r"(?<!\w)\d+(?::\d+|\.\d+)?(?!\w)", text)
        return sorted(":".join(str(int(part)) for part in value.split(":")) if ":" in value else str(float(value)) for value in values)
    if numbers(draft) != numbers(result):
        return False
    ordinary = {"The", "A", "An", "Your", "Our", "I", "You", "It", "This", "That", "There", "We", "They",
                "Make", "Start", "Try", "Keep", "Save", "Use", "Do", "Let", "Give", "Have", "Be", "Take", "Ask",
                "Choose", "Listen", "Thanks", "Thank", "In", "On", "At", "For", "With", "If", "But", "And", "Yes", "No", "Here"}
    required = re.findall(r"\[D?\d+\]", draft) + [word for word in re.findall(r"\b[A-Z][\w-]*\b", draft) if word not in ordinary]
    if not all(re.search(r"(?<!\w)" + re.escape(token) + r"(?!\w)", result) for token in required):
        return False
    if len(result.split()) > max(70, len(draft.split()) * 3) or not result.strip():
        return False
    if re.search(r"\bI (?:am|'m) Mac Miller\b", result, re.I):
        return False
    if re.search(r"cannot|can't|uncertain|not verified|do not know", draft, re.I) and not re.search(r"cannot|can't|uncertain|not|don't|unsure", result, re.I):
        return False
    return True


def load_writer(directory, adapter=False):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    path = prepare_asset(directory, "persona")
    tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True)
    model = AutoModelForCausalLM.from_pretrained(path, dtype=torch.float32, local_files_only=True)
    if adapter:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, directory / "models/persona-adapter", local_files_only=True)
    torch.set_num_threads(4)
    return model, tokenizer


def rewrite_with(model, tokenizer, draft):
    import torch
    messages = [{"role": "system", "content": STYLE_PROMPT}, {"role": "user", "content": draft}]
    encoded = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                                            enable_thinking=False, return_tensors="pt", return_dict=True)
    with torch.inference_mode():
        generated = model.generate(**encoded, max_new_tokens=80, do_sample=False, pad_token_id=tokenizer.eos_token_id)
    return tokenizer.decode(generated[0, encoded["input_ids"].shape[-1]:], skip_special_tokens=True).strip()


def train(directory, examples=None, steps=32):
    import torch
    from peft import LoraConfig, get_peft_model
    examples = examples or [{"input": a, "output": b} for a, b in EXAMPLES]
    if not 8 <= len(examples) <= 500:
        raise ValueError("Use between 8 and 500 original training examples.")
    for item in examples:
        if set(item) != {"input", "output"} or any(not isinstance(v, str) or not 1 <= len(v) <= 1500 for v in item.values()):
            raise ValueError('Each example needs input and output text, up to 1,500 characters each.')
    started = time.perf_counter()
    torch.manual_seed(42)
    model, tokenizer = load_writer(directory)
    baseline = [rewrite_with(model.eval(), tokenizer, draft) for draft in HELD_OUT]
    model = get_peft_model(model, LoraConfig(r=4, lora_alpha=8, target_modules=["q_proj", "v_proj"], lora_dropout=0.05, task_type="CAUSAL_LM"))
    model.enable_input_require_grads()
    model.gradient_checkpointing_enable()
    model.config.use_cache = False
    optimizer = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=2e-4)
    losses = []
    model.train()
    for step in range(steps):
        item = examples[step % len(examples)]
        prefix = [{"role": "system", "content": STYLE_PROMPT}, {"role": "user", "content": item["input"]}]
        before = tokenizer.apply_chat_template(prefix, tokenize=True, add_generation_prompt=True, enable_thinking=False, return_dict=True)["input_ids"]
        complete = tokenizer.apply_chat_template([*prefix, {"role": "assistant", "content": item["output"]}], tokenize=True, enable_thinking=False, return_dict=True)["input_ids"]
        if len(complete) > 256:
            raise ValueError("A training example exceeds 256 tokens. Shorten it before training.")
        inputs = torch.tensor([complete])
        labels = inputs.clone()
        labels[:, :len(before)] = -100
        loss = model(input_ids=inputs, labels=labels).loss
        loss.backward()
        torch.nn.utils.clip_grad_norm_([p for p in model.parameters() if p.requires_grad], 1.0)
        optimizer.step()
        optimizer.zero_grad(set_to_none=True)
        losses.append(float(loss.detach()))
    target = directory / "models/persona-adapter"
    model.peft_config["default"].base_model_name_or_path = MODELS["persona"][0]
    model.save_pretrained(target)
    tokenizer.save_pretrained(target)
    model.config.use_cache = True
    model.gradient_checkpointing_disable()
    adapted = [rewrite_with(model.eval(), tokenizer, draft) for draft in HELD_OUT]
    report = {"backend": "PEFT LoRA CPU", "base_model": MODELS["persona"], "steps": steps,
              "examples": len(examples), "seed": 42, "seconds": round(time.perf_counter() - started, 3),
              "first_loss": losses[0], "last_loss": losses[-1], "adapter_bytes": sum(p.stat().st_size for p in target.rglob("*") if p.is_file()),
              "evaluation": [{"input": draft, "base": a, "adapter": b, "base_preserves": preserves(draft, a), "adapter_preserves": preserves(draft, b)}
                             for draft, a, b in zip(HELD_OUT, baseline, adapted)],
              "tone_quality": "Requires human review. This small synthetic set is not proof of artist likeness."}
    report["passed"] = all(item["adapter_preserves"] for item in report["evaluation"])
    (target / "evaluation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    del model, optimizer, tokenizer
    gc.collect()
    return report


def rewrite(directory, draft):
    model, tokenizer = load_writer(directory, adapter=True)
    result = rewrite_with(model.eval(), tokenizer, draft)
    del model, tokenizer
    gc.collect()
    return result if preserves(draft, result) else draft
