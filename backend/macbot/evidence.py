import gc
import json
import re


def normalized(value):
    return re.sub(r"\s+", " ", value).strip()


def source_passages(question, sources):
    stop = {"the", "a", "an", "is", "are", "can", "does", "what", "how", "do", "to", "of", "in", "and", "its", "with", "give", "one", "according", "official", "documentation", "client"}
    words = set(re.findall(r"[a-z0-9]+", question.lower())) - stop
    ranked = []
    for source in sources:
        if source.get("status") != "read":
            continue
        best = None
        text = normalized(source.get("text", source.get("excerpt", "")))
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            subject = re.search(r"\b[A-Z][a-z]+(?:\s+[a-z]+){0,2}\s+(?:allows?|supports?|provides?|uses?|requires?|enables?)\b", sentence)
            if subject and len(sentence[:subject.start()].split()) <= 12:
                sentence = sentence[subject.start():]
            tokens = sentence.split()
            for offset in range(0, len(tokens), 12):
                quote = " ".join(tokens[offset:offset + 24])
                subject = re.search(r"\b[A-Z][a-z]+(?:\s+[a-z]+){0,2}\s+(?:allows?|supports?|provides?|uses?|requires?|enables?)\b", quote)
                if subject and len(quote[:subject.start()].split()) <= 12:
                    quote = quote[subject.start():]
                if len(quote.split()) < 6 or "[…]" in quote:
                    continue
                overlap = words & set(re.findall(r"[a-z0-9]+", quote.lower()))
                if len(overlap) < 2:
                    continue
                score = len(overlap) + (0.4 if len(tokens) <= 24 else 0)
                if best is None or score > best[0]:
                    best = (score, {"source": source["id"], "quote": quote})
        if best:
            ranked.append(best)
    passages, seen = [], set()
    for _, entry in sorted(ranked, key=lambda item: item[0], reverse=True):
        key = entry["quote"].casefold()
        if key not in seen:
            seen.add(key)
            passages.append(entry)
        if len(passages) == 3:
            break
    return passages


def repair_prompt(prompt, draft):
    return (prompt + "\nYour previous draft failed its evidence checks:\n" + json.dumps(draft)[:10000]
            + "\nTry once more. Each quote must be copied exactly and directly establish EVERY detail of its claim. "
              "Do not add a specific API call, number, subject or qualification that the quote itself does not mention. "
              "A narrower supported fact is better than an unsupported complete answer. If none is supported, return an empty claims list.")


def review_claims(value, sources, directory, threshold=0.80):
    """Require exact source excerpts and conservative NLI support; abstain otherwise."""
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    from .assets import prepare_asset
    indexed = {item["id"]: item for item in sources if item["status"] == "read"}
    candidates, used = [], {}
    for item in value.get("claims", [])[:8]:
        if not isinstance(item, dict):
            continue
        source = indexed.get(item.get("source"))
        claim, quote = str(item.get("claim", ""))[:500], normalized(str(item.get("quote", "")))
        if not source or not claim or not quote or len(quote.split()) > 25:
            continue
        # Quotes must exist in the actual page, rather than a search snippet.
        if quote.casefold() not in normalized(source["text"]).casefold():
            continue
        if used.get(source["id"], 0) + len(quote.split()) > 25:
            continue
        used[source["id"]] = used.get(source["id"], 0) + len(quote.split())
        candidates.append((source, claim, quote))
    if not candidates:
        return [], len(value.get("claims", []))
    path = prepare_asset(directory, "verifier")
    tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True)
    model = AutoModelForSequenceClassification.from_pretrained(path, local_files_only=True).eval()
    torch.set_num_threads(4)
    accepted = []
    entailment = model.config.label2id["entailment"]
    with torch.inference_mode():
        for source, claim, quote in candidates:
            encoded = tokenizer(quote, claim, return_tensors="pt", max_length=256, truncation=True)
            probability = float(model(**encoded).logits.softmax(-1)[0, entailment])
            if probability >= threshold:
                accepted.append({"claim": claim, "quote": quote, "source": source["id"], "support": round(probability, 4)})
    del model, tokenizer
    gc.collect()
    return accepted, len(value.get("claims", [])) - len(accepted)


def render_report(claims, rejected, passages=None):
    if not claims:
        if passages:
            quotes = [f"> {item['quote']} [{item['source']}]" for item in passages]
            return "I could not verify a complete answer from these pages. These relevant source passages may help:\n\n" + "\n\n".join(quotes) + "\n\nReview the linked pages for context. These excerpts have not established a complete answer to your question."
        return "I could not establish a supported answer from the pages I read. Try a narrower question or review the sources below."
    paragraphs = [f"- {item['claim']} [{item['source']}]\n  > {item['quote']}" for item in claims]
    return "**Evidence from this search**\n\n" + "\n\n".join(paragraphs) + (
        f"\n\n**Limits:** This search covers up to six pages. {rejected} draft claims were omitted because their evidence was insufficient. Evidence checks can still miss errors; review the linked sources.")
