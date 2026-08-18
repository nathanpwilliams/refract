# Lesson 1.2 — How language models actually work
**Module 1 · ~10 min · Objectives: 2** · Expanded from seed 1.2 · Contains one adaptable=false block

---

[heading h2]
Prediction, not lookup

[text]
A large language model does not look up answers in a database. It
generates text one small piece at a time, each time predicting what is
most likely to come next given everything so far. Training on vast text
collections makes those predictions remarkably fluent and often
accurate — but *plausible* is the target, not *true*.

[text]
This single fact explains most AI behavior you will encounter: why
answers sound confident even when wrong, why the same question can get
different answers on different days, and why models sometimes invent
details — a failure mode called hallucination. The machine isn't lying
to you; it's doing exactly what it was built to do, which is continue
text convincingly.

[text adaptable=false]
**Definitions used throughout this course**

- *Model*: the trained system that generates output.
- *Prompt*: everything you provide to the model — instructions,
  questions, and reference material.
- *Context window*: the maximum amount of text a model can consider
  at once.
- *Hallucination*: fluent, confident output that is factually wrong
  or fabricated.

[heading h3]
What it knows vs. what you show it

[text]
A model's general knowledge comes from training — a frozen snapshot of
public text with a cutoff date. Everything else, it only knows if you
put it in the prompt. It has never seen your organization's documents,
your project, your client, or anything that happened after its training
ended — unless you show it.

[text]
This is why the same tool feels brilliant one moment and useless the
next. Ask it to summarize a document you've pasted in, and it works
from evidence. Ask it about that document from memory, and it works
from probability — stitching together what documents *like* yours
usually say. The output looks identical. The reliability is not.

[knowledge_check]
Q: You ask an AI assistant a question about a contract without
providing the contract, and receive a detailed, confident answer. What
is actually happening?
- (a) The model found the contract in its database and is quoting it
  ✗ There is no database lookup — and the model has never seen your
    contract unless you provided it.
- (b) The model is predicting what a contract like yours probably
  says — which may or may not match what yours actually says ✓
  ✓ Exactly. Without the source, you're getting plausible text, not
    your contract. The fix is simple: paste the document.
- (c) The confident tone means the answer is probably reliable
  ✗ Fluency is style, not evidence — the model sounds the same when
    it's wrong.

[callout warning]
A confident tone is not evidence of accuracy. The model produces the
same fluent style whether it is right or wrong.

[heading h3]
A shape to work with, not a flaw to fix

[text]
None of this is a defect awaiting a patch — it is what this kind of
tool *is*. Prediction makes it a superb drafter, summarizer, and
explainer, and an untrustworthy witness. The professionals who get the
most from AI aren't the ones who trust it most; they're the ones who
learned its shape and route work accordingly. The next module teaches
the routing.

[callout key_idea]
Takeaway: the model predicts; it doesn't know. Its general knowledge is
frozen and approximate; its knowledge of *your* work is exactly what
you put in the prompt — nothing more. Show it your documents, and treat
every unsourced answer as a guess wearing a suit.

---
**Rubric self-check:** opens with the idea (h2) · closes with takeaway ·
interaction at ~6 min · definitions block locked (adaptable=false,
survives refraction verbatim) · limitation beside capability throughout ·
text blocks ≤150 words · sets up M2.3 grounding without teaching it ·
no buyer copy · "guess wearing a suit" flagged for your taste check
