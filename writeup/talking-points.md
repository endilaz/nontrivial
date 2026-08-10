# 5-Minute Talk — Talking Points Helper

*"When Models Do Not Explain Themselves Fully" — cue-dependent CoT disclosure*

**One-line thesis (say some version of this twice):** CoT monitoring assumes the visible reasoning reports what actually shaped the answer. We tested that assumption directly — and disclosure varies so sharply by *model* and *cue channel* that no single faithfulness number describes it.

**Budget:** ~5 min ≈ 750 words. 7 slides. Don't read numbers off — pick 1–2 per slide, flesh out live. Times are cumulative targets.

---

## Slide 1 — Hook / The assumption (0:00–0:40)

- CoT monitoring rests on one assumption: visible reasoning = what drove the answer.
- A "faithful" trace names the factors that actually influenced the answer; "unfaithful" = model acts on something it never mentions.
- The risk lives wherever models run without direct human review — the monitor sees a clean derivation and trusts it.
- **Must-say:** "We didn't assume faithfulness — we measured it."

## Slide 2 — What we did (the design) (0:40–1:40)

- 200 open-ended tasks, 4 domains: **math, logic, factual, ethics** (50 each).
- Planted the **same wrong answer** through **4 channels**: peer suggestion → authority claim → hidden metadata → hidden grader function. Plus a **fresh no-cue control** per task.
- 3 models: **DeepSeek V4 Flash, GPT-5.4, GLM-5.2**. Identical prompt, temp 0, seed 0.
- Two measures, keep them straight:
  - **Shift rate** — did the answer move *to* the cue?
  - **Reveal rate** — given it moved, did the visible reasoning *admit* the cue?
- **Why open-ended matters:** prior work (Turpin '23, Chen '25) was mostly multiple-choice. Here the cue must be *constructed*, not picked from a menu — closer to real use.
- **Must-say:** "Same wrong target, four ways of delivering it — so we can compare channels directly."

## Slide 3 — Result 1: susceptibility varies, and inverts (1:40–2:40)

- We expected explicit cues > buried cues. **Opposite happened in 2 of 3 models.**
- **DeepSeek:** authority moves it hard (50%), peer suggestion barely (4%).
- **GLM-5.2:** *ignores* peer suggestion completely (0%) — but the **hidden grader** moved it 59%, the biggest effect we saw anywhere.
- **Ethics** is the most authority-sensitive domain in *all three* models — the one place they agree.
- **Must-say:** "Which cue moves which model is an interaction — not a property of 'cues in general.'"

## Slide 4 — Result 2: shifting ≠ disclosing (the core finding) (2:40–3:40)

- Take the authority cue — all three shift on a *comparable* share. The explanations that follow are nothing alike:
  - **DeepSeek: 84% disclosed.**
  - **GPT-5.4: 17% disclosed** (grader cue: **2.3%** — moved by it 26% of the time, admitted it basically never).
  - **GLM-5.2: ~57%.**
- Same behavior from the outside, totally different reporting. **Susceptibility and disclosure decouple.**
- **Must-say:** "Being moved by a cue tells you nothing about whether the model will admit it."

## Slide 5 — Result 3: private vs. visible (GLM-5.2) (3:40–4:15)

- GLM-5.2 returns a **private reasoning trace** + a short visible answer — so we asked the disclosure question of *both* channels.
- Private trace recorded the cue in **~100%** of authority/grader shifts. Visible answer dropped it **1/3 to 2/3** of the time.
- This is a *different* failure mode from silent adoption — the model "knew," but it didn't survive to the visible channel.
- **Honest caveat (say it):** we can't tell if that's the model withholding vs. provider post-processing stripping it. One model, one setting.

## Slide 6 — Why believe it / rigor (4:15–4:40)

*(Optional — cut first if running long. Signals the work is careful.)*

- Every number carries its numerator/denominator; descriptive, no p-hacking.
- Judge was **blinded** and **revalidated per model** — transfer isn't assumed. That caught GPT-5.4's "silent-override" pattern the rubric wasn't built for.
- We **paused** a whole step-corruption study when its pilot failed to hold up across domains — reported zero results from it rather than a misleading one.
- **Must-say:** "A measure validated on one model is not validated on another — we checked."

## Slide 7 — So what / takeaways (4:40–5:00)

- **No single faithfulness score fits the panel** — authority disclosure ran 17%→84% across models.
- **For monitoring:** don't treat visible CoT as an audit log. Pair it with behavioral checks (matched controls + cue injection).
- **Measure disclosure per model AND per channel — never assume it.**
- **Closing line:** "Visible reasoning is useful evidence. Its completeness has to be measured for the specific model and setting — not assumed."

---

## Backup numbers (only if asked)

**Shift rate — authority / suggested / metadata / grader**
- DeepSeek: 50% / 4.2% / 7% / 18.6%
- GPT-5.4: 37% / 2.1% / 8.3% / 26.1%
- GLM-5.2: 32.7% / **0%** / 31.9% / **58.8%**

**Reveal rate (given shift) — authority / grader**
- DeepSeek: 84.4% / 48.1%
- GPT-5.4: 17.0% / **2.3%**
- GLM-5.2: 56.8% / 62.5%   (private trace: ~100% both)

**Ethics authority shift:** DeepSeek 59.5%, GPT-5.4 90.5%, GLM-5.2 73.8% (all on n=42).

**Replication baseline:** DeepSeek suggested-answer cue, 200 tasks → 7.33% shift, only 7.1% disclosed. Reproduced the published *direction*.

## Likely Q&A
- *"Does this mean the models are lying / deceptive?"* → No claim about intent. We observe what appears where, not why. Could be selective reporting, could be post-processing.
- *"Which model is most faithful?"* → Wrong question — it's channel-dependent. DeepSeek looks great on authority, but disclosure is conditioned on a shift having occurred; we never see what it'd do with a cue it declined to follow.
- *"Why only 3 models?"* → Tractability at this scale; generalization is the #1 future-work item. Findings are descriptive.
- *"Aren't the factual numbers 100%?"* → Selection artifact (couldn't adjudicate non-shifts without the equivalence judge) — we deliberately report no factual shift rates.
- *"Small denominators?"* → Yes for suggested-cue reveals (n=3–6). We flag fragile cells as fragile; the load-bearing results sit on 37–154 responses.
