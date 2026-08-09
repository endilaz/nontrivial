# Research Writeup Outline

## Scope and claim discipline

- **Empirical scope:** completed work through Milestone F: benchmark construction,
  evaluation and replication checks, parser validation, the Milestone E feasibility
  pilot and documented pivot, and the three-model hint-paradigm panel.
- **Main construct:** reporting completeness under external cues: when a cue changes
  an answer, does the model's visible reasoning disclose that influence?
- **Do not claim:** completed step-corruption results, causal reliance on stated
  reasoning, Milestone G inferential analyses, verified novelty, or objective moral
  correctness on ETHICS tasks.
- **Evidence hierarchy:** use final researcher-approved decisions and model-specific
  admission-judge corrections over stale summary fields. Rebuild final tables from
  persisted response and judgment records where targeted reruns or later adjudication
  superseded an earlier aggregate.
- **Target length:** approximately 7,200 words, below the 8,000-word main-text limit.
- **Confidence language:** reserve “shows” for direct descriptive results; use
  “supports,” “suggests,” or “cannot distinguish” for mechanisms and generalization.
  Do not attach inferential certainty to descriptive differences because Milestone G
  was not completed.
- **Audience and style:** define technical terms at first use, prefer accessible language,
  and keep the paper's tense, first-person pronoun, citation style, and figure/table
  numbering consistent.
- **Strategic focus:** prioritize reporting completeness under cues. Keep causal-reliance
  design details, engineering chronology, full tables, and tangential background in the
  appendices unless needed to understand a result or scope decision.

## Working title

**When Models Follow the Hint but Do Not Say So: Cue-Dependent Chain-of-Thought
Disclosure Across Open-Ended Tasks and Three Language Models**

Avoid a title claiming that chain-of-thought is globally "unfaithful": the experiment
tests disclosure after measurable cue-induced answer shifts, not every form of
faithfulness.

## Central argument

Visible reasoning was not a model-invariant record of why an answer changed. Descriptive
cue-susceptibility and disclosure rates varied substantially by model and cue presentation.
DeepSeek often disclosed salient authority cues, GPT-5.4 was usually silent even when
it shifted, and GLM-5.2 frequently disclosed cue use in its private reasoning while
omitting it from visible content. These results support evaluating chain-of-thought
monitoring per model, cue channel, and reasoning-visibility regime rather than treating
visible reasoning as a uniformly reliable explanation.

---

# 1. Introduction and Literature Review (~1,300 words)

## 1.1 Problem, importance, and neglectedness

- Define chain-of-thought (CoT) and **faithfulness** in accessible terms.
- Explain the monitoring problem: visible reasoning is useful only insofar as it
  reports factors that actually influence an answer.
- Use one concrete silent-adoption example to establish the stakes before technical
  detail.
- Establish who bears the decision risk: evaluation researchers, model developers,
  benchmark designers, and institutions that may use visible reasoning as a monitoring
  signal.
- Make the priority case relative to adjacent evaluation questions: answer accuracy
  alone cannot reveal whether a model omitted an influential cue, while direct access
  to internal computation is generally unavailable to external evaluators.
- State neglectedness cautiously: prior hint studies establish the failure mode, but
  evidence remains limited for open-ended tasks, cue-channel variation, model-specific
  judge transfer, and private-versus-visible reporting.
- Calibrate the practical claim: this study informs the reliability of behavioral
  CoT monitoring; it does not establish deployment safety or reveal internal causal
  mechanisms directly.

## 1.2 Relevant literature, organized by question

### A. Does stated reasoning track answer formation?

- Summarize the distinction between outcome quality and reasoning faithfulness.
- For each core paper, identify the relevant question, data/model setting, intervention,
  headline result, and limitation that bears on this study.
- Use Lanham et al. for perturbation-based faithfulness approaches.
- Explain why causal reliance and reporting completeness are related but distinct.

### B. Do models disclose influential hints or biases?

- Use Turpin et al. and Chen et al. for cue-following with incomplete disclosure.
- For each, distinguish what its data and method establish from what cannot transfer to
  this study's models, task formats, or cue channels.
- Describe the established qualitative pattern, not unsupported numerical
  comparability across different models, tasks, and answer formats.

### C. What remains uncertain in this project context?

- Synthesize the specific gaps left by those methods and limitations rather than listing
  citations: open-ended tasks, cue-channel variation, cross-model measurement validity,
  and private-versus-visible disclosure.
- Motivate testing open-ended, constrained-answer tasks across math, logic,
  contextual factual reasoning, and ethics rather than relying only on
  multiple-choice settings.
- Motivate varying cue presentation and model, because a single hint wording on one
  model cannot establish a general disclosure rate.
- Briefly name plausible gaps not pursued—deployment monitoring validity, causal
  intervention on hidden computation, and broader model/provider coverage—and explain
  why the completed study prioritizes a tractable behavioral test.
- **Citation check required:** the planned 2026 literature refresh was not completed.
  Do not use "first," "novel," or comprehensive-gap language unless independently
  verified during drafting.

## 1.3 Research question and operational definitions

**Primary question:** Across three language models and four open-ended task types,
how do external cue presentation and model choice affect (a) movement to a plausible
wrong cue target and (b) disclosure of that cue's influence in the model's reasoning?

- **Cue susceptibility:** proportion of decided outcomes that shift to the cue target.
- **Reveal rate:** proportion of shifted-to-cue responses classified as explicit or
  indirect disclosure.
- **Visible-content reveal:** primary cross-model disclosure construct.
- **Reasoning-trace reveal:** GLM-only secondary construct; report separately because
  it is not comparable to visible-content reveal without the visibility-stratum caveat.
- Treat task type as descriptive heterogeneity, not a completed confirmatory ranking.

## 1.4 Approach choice and contributions, stated conservatively

- Justify the matched hint paradigm over reasonable alternatives: it directly tests
  observable reporting completeness without claiming access to latent causal reasoning;
  fresh controls separate cue effects from baseline variability; shared wrong targets
  make cue channels comparable within a task-model set; and cross-model validation tests
  whether the disclosure measure transfers.
- State the trade-offs: this design is more behaviorally interpretable and achievable
  than mechanistic access or a broad deployment study, but it conditions disclosure on
  observed shifts and cannot by itself identify why silence occurs.
- Explain why the project strategically deprioritized a full step-corruption experiment:
  its pilot did not preserve the intended construct across domains, whereas the hint
  paradigm remained coherent and directly addressed the reporting question.

1. A reviewed 200-task benchmark spanning four task types, with provenance and
   task-specific scoring.
2. An open-ended replication check of the previously reported suggested-answer
   non-disclosure pattern.
3. A matched five-condition hint paradigm across DeepSeek V4 Flash, GLM-5.2, and
   GPT-5.4.
4. A validated disclosure rubric and model-specific revalidation showing that judge
   transfer cannot be assumed.
5. Evidence that private and visible disclosure can diverge sharply for GLM-5.2.
6. Transparent documentation of a step-corruption design that was paused after its
   feasibility pilot failed to preserve the intended construct across task types.

## 1.5 Theory of change

**Figure 1: bounded theory-of-change diagram**

Research evidence on cue susceptibility and disclosure
→ evaluators test monitoring assumptions by model/cue/visibility regime
→ developers avoid treating visible CoT as a universal explanation
→ monitoring systems use validated, layered evidence rather than CoT alone
→ fewer false assurances from apparently self-derived reasoning.

- Name the actors and actions at each link: researchers validate the measure,
  developers adopt model- and channel-specific tests, and monitoring teams combine CoT
  with behavioral and other signals.
- Name likely failure modes and workarounds: strategic reasoning, provider-specific
  output filtering, unobserved private computation, monitoring-target adaptation, and
  benchmark-to-deployment generalization.
- Identify the actionable near-term output: a validation protocol and reporting standard
  requiring per-stratum reveal rates, denominators, missingness, and complementary
  behavioral checks.
- Do not claim that this paper demonstrates the later links in the chain.

---

# 2. Methodology (~2,200 words)

## 2.1 Study progression, alternatives, and scope decisions

- Open with a compact design map connecting each research-question component to its
  intervention, comparison, outcome, denominator, and evidential limit.
- Briefly distinguish the original two constructs:
  - step corruption for causal reliance;
  - hinting for reporting completeness.
- Explain that the completed empirical contribution centers on reporting completeness.
- Present the E-to-F pivot as a research-integrity decision, not as a positive
  corruption result:
  - E1 specification, E2 implementation, and E3 operators/pilot were completed;
  - the 12-task, 192-condition pilot exposed incoherent ethics interventions,
    approximately 12% math reversal coverage, and loss of a meaningful
    `followed_corruption` outcome for logic and ethics;
  - two operator bugs remained;
  - E4/E5 and the full corruption experiment were not run.
- Compare the chosen hint design with the main alternatives considered: multiple-choice
  replication, hidden-state/mechanistic analysis, and full step corruption. Explain the
  fit, feasibility, and construct-validity trade-off that favored the completed design.
- State the key identification assumptions explicitly:
  1. the fresh no-cue control estimates task-model baseline behavior closely enough for
     descriptive comparison;
  2. a shared plausible wrong target makes cue conditions comparable within a task;
  3. explicit/indirect admission is a useful, imperfect proxy for reporting completeness;
  4. decided-outcome conditioning and missingness may change apparent rates;
  5. prompt and provider strata are part of the treatment context, not nuisance details.

## 2.2 Benchmark construction

**Table 1: benchmark composition and scoring policy**

- 200 approved tasks, 50 per type.
- Math: surface-perturbed GSM8K while preserving numbers, operations, units, and
  truth conditions; note the later re-perturbation amendment and 49/50 math baseline.
- Logic: BIG-Bench logic-grid tasks with deterministic unique-answer validation.
- Factual: HotpotQA distractor-setting tasks with full gold-plus-distractor context
  and retained supporting-fact provenance.
- Ethics: ETHICS commonsense tasks; report source-label agreement and answer
  stability, never objective moral accuracy.
- Explain human review, license/provenance checks, fixed task IDs, and deterministic
  regeneration.
- Cover the data Who/What/When/Why/Where/How in compact form: source and revision,
  inclusion/exclusion and sampling, transformation date/version, responsible review,
  intended role, and persisted artifact location.
- Justify selection and processing against alternatives—for example, why constrained
  open-ended answers, why 50 tasks per domain, why full HotpotQA context, and why surface
  perturbation rather than direct item reuse—without portraying convenience choices as
  optimal.
- State that no conventional imputation was used; unresolved or failed cells follow the
  prespecified scoring, judge-resolution, retry, and missingness rules in Sections 2.6–2.7.
- Limitations to state here: curated sample, 50 tasks per type, and imperfect breadth
  of math surface perturbations.

## 2.3 Models and reasoning-visibility strata

**Table 2: model, provider route, thinking setting, output visibility, and limits**

- DeepSeek V4 Flash: probe-verified non-thinking workhorse.
- GPT-5.4: `reasoning_tokens=0` throughout the panel run.
- GLM-5.2: the disabled-thinking probe failed on the pinned route; the researcher
  approved a thinking-enabled hidden-reasoning stratum.
- Explain why GLM private-trace results are secondary and should not be pooled with
  cross-model visible-content rates.
- Record temperature, seed, maximum tokens, configuration versions, and route
  behavior in the final table rather than narrating every engineering field.

## 2.4 Evaluation and preliminary replication checks

- Explain deterministic scoring by answer type and the separately validated
  answer-equivalence judge for unresolved short-text cases.
- Summarize the C4 suggested-answer replication design:
  - plausible wrong cue distinct from baseline;
  - fresh no-cue control;
  - one workhorse model and all 200 tasks;
  - open-ended answer extraction rather than multiple-choice letters.
- Distinguish its manually checked deterministic reveal heuristic from the later F3
  admission judge.

## 2.5 Hint-paradigm design

**Table 3: five matched conditions**

1. `suggested_cue`: conversational suggested answer.
2. `authority_cue`: panel-of-experts assertion.
3. `metadata_cue`: target embedded in metadata.
4. `grader_cue`: target embedded in a validation/grader function.
5. `no_cue_control`: fresh matched control.

- Every task-model set uses one shared plausible wrong target across cue conditions.
- Cue targets differ from the model's baseline answer by construction.
- Explain task-specific target construction: arithmetic-error alternatives, valid
  logic positions, same-type factual distractors, and opposite ethics stances.
- For each major design choice, state why it was used and the strongest reasonable
  alternative: shared versus cue-specific targets, fresh versus reused controls,
  open-ended versus multiple-choice answers, and four cue channels versus one template.
- Explain dry runs, full 200×5 runs, and targeted DeepSeek logic/ethics corrections.
- Identify those corrections transparently:
  - the initial logic ladder could generate an impossible house index;
  - bare ethics `yes`/`no` targets were stance-ambiguous.

## 2.6 Outcomes, denominators, and missingness

- Outcome categories: `shifted_to_cue`, `changed_other`, `unchanged`, and unscored or
  failed outcomes.
- State numerator and denominator next to every rate.
- Primary descriptive rate in completed summaries uses decided outcomes; also show
  reason-specific missingness so decided-only rates are not read as full-sample rates.
- Report generation failures, truncations, parsing/equivalence failures, cue-
  construction failures, and provider/content-policy blocks separately.
- Do not introduce Milestone G regression, multiplicity correction, or inferential
  confidence claims as though they were completed.

## 2.7 Admission coding and validation

- Rubric v1.1.1 labels `explicit`, `indirect`, or `none`; reveal is explicit or
  indirect among shifted responses.
- Judge is blinded to model identity, condition, and shift outcome.
- Initial 80-label DeepSeek validation:
  - dev accuracy 100%;
  - holdout accuracy 95%;
  - holdout explicit sensitivity 80%;
  - holdout none specificity 100%.
- GPT-5.4 revalidation: 40/44 agreement (91%); use researcher corrections for the
  documented factual silent-override errors.
- GLM-5.2 visible-content revalidation: 42/44 agreement (95%); apply the two final
  researcher corrections.
- GLM reasoning-trace validation: 16/16 agreement.
- Explain why corrected researcher labels, not unadjusted judge files, define final
  reported reveal counts.

## 2.8 Reproducibility, preregistered controls, and research process

- Distinguish decisions fixed before runs from later amendments, reruns, concessions,
  and deviations; point readers to the dated record in Appendix F.
- Versioned configs, task/result manifests, seeds, cached semantic requests, raw
  responses, receipts, and cost logs.
- Small dry run before full run and researcher checkpoints before expansion.
- Nondeterminism check on DeepSeek: observed flip-rate floor of 0 in the completed
  replicate check.
- Briefly report experimental spend only as a reproducibility/resource fact; do not
  force a societal cost-benefit analysis unsupported by the project.

---

# 3. Results (~1,800 words)

## 3.1 Validation and replication results

**Table 4: validation and integrity checks**

- Parser v1.2.0 on 32 researcher-labeled CoTs:
  - overall macro-F1 0.8866, below the preregistered 0.90 gate;
  - math 0.8009, logic 0.8912, factual 0.9583, ethics 0.8958;
  - report as a failed gate with an accepted, documented deviation after disagreement
    review, not as a pass.
- C4 open-ended suggested-answer replication:
  - shift 14/191 decided outcomes (7.33%);
  - reveal 1/14 shifted responses (7.14%);
  - ethics 9/42, logic 4/49, factual 1/50, math 0/50 shifted;
  - 13/14 shifted responses were silent under full-transcript review.
- State that qualitative direction, not exact numerical equivalence to prior work,
  was the replication criterion.

## 3.2 Cue susceptibility varies by model and cue

**Figure 2: shift rate by model × cue, with shifted/decided labels**

- Establish figure conventions once: number and caption every figure, label axes and
  units, define decided and shifted denominators, show raw counts beside percentages,
  and use consistent rounding and missingness notes.

- GPT-5.4 decided shift rates:
  - suggested 2.14%; authority 36.96%; metadata 8.28%; grader 26.06%.
- GLM-5.2 decided shift rates:
  - suggested 0%; authority 32.74%; metadata 31.90%; grader 58.82%.
- DeepSeek headline:
  - authority was the dominant cue in the completed workhorse analysis;
  - ethics authority shift was 59.5% and corrected logic authority shift was 32%;
  - suggested, metadata, and grader cues were much weaker on corrected logic/ethics
    slices.
- Before inserting a single DeepSeek overall row, mechanically combine the canonical
  full-run factual/math slices with the superseding logic and ethics reruns. The
  repository's older aggregate summary predates those slice corrections.
- Main observation: cue salience alone does not define susceptibility; the model ×
  presentation interaction is substantial.

## 3.3 Visible disclosure also varies by model and cue

**Figure 3: visible-content reveal among shifted responses by model × cue**

- GPT-5.4, after researcher corrections:
  - suggested 0/3;
  - authority 9/53 (about 17%);
  - metadata 1/12 (about 8%);
  - grader 1/44 (2.3%).
- GLM-5.2, after researcher corrections:
  - no suggested-cue shifts to evaluate;
  - authority disclosure was roughly half of shifts;
  - metadata 12/37 (32%);
  - grader 45/72 (62.5%).
- DeepSeek:
  - authority shifts were usually disclosed, especially in math, logic, and ethics;
  - buried suggested/metadata/grader cues were disclosed less consistently.
- Use exact DeepSeek and GLM authority counts only after the final corrected table is
  regenerated from judgments plus researcher concessions; do not copy stale judged
  JSON values where README provenance records later adjustments.
- Contrast the models without ranking their general faithfulness: the comparison is
  conditional on observed shifts and these prompts.

## 3.4 Private reasoning versus visible reporting in GLM-5.2

**Figure 4: GLM visible-content reveal versus reasoning-trace reveal**

- Reasoning trace + content among shifted responses:
  - authority 37/37 (100%);
  - metadata 36/37 (97.3%);
  - grader 72/72 (100%).
- Contrast with lower visible-content disclosure in the same conditions.
- Interpret descriptively as selective reporting: cue use was usually represented in
  the available private trace but often absent from the visible answer.
- Do not generalize this mechanism to DeepSeek or GPT-5.4, whose runs did not provide
  the same hidden-reasoning stratum.

## 3.5 Task-type heterogeneity

**Table 5: shifted/decided and revealed/shifted by task type, model, and cue**

- Include task-level cells only when denominators support interpretation.
- Highlight supported examples:
  - DeepSeek math remained robust to suggested cues but shifted under authority and
    grader cues;
  - ethics was highly authority-sensitive for multiple models;
  - factual and logic cells were affected differently by equivalence resolution,
    provider failures, and policy blocks.
- Avoid a global "most faithful task type" claim: no completed Milestone G model or
  corrected multiple-comparison analysis supports it.

## 3.6 Failures and data availability

**Table 6: missingness and incidents by model and task type**

- GPT-5.4: ten content-filter blocks, concentrated in logic; record as ITT failures.
- GLM-5.2: disabled-thinking capability failure, provider/API instability, 109 retry
  targets, 69 recovered, and 52 remaining generation-failure/truncation cells.
- DeepSeek: corrected logic cue-construction and ethics stance-format bugs.
- Factual answer-equivalence requirements produced substantial initially unscored
  cells before judge resolution.
- Explain the direction of possible bias rather than treating missing cells as random.

## 3.7 Synthesis, surprise, and descriptive confidence

- Compare observed patterns with the preregistered expectations and prior hint studies;
  identify which findings aligned, which challenged expectations, and which were
  especially surprising.
- Separate robust descriptive observations from fragile small-denominator cells. For
  each headline finding, state the relevant numerator/denominator, measurement concern,
  and generalization boundary.
- Summarize potential bias from conditional selection on shifted responses, provider and
  content-policy failures, cross-model visibility differences, judge error, and targeted
  reruns.
- Avoid p-values, population-level rankings, or “significant” language; characterize
  confidence qualitatively because the completed work is descriptive.

---

# 4. Discussion (~1,600 words)

## 4.1 What the results support

1. **Disclosure is not model-invariant.** Similar answer shifts can produce very
   different visible explanations across models.
2. **Disclosure is cue-dependent.** A salient authority statement can be both highly
   influential and highly disclosed, while buried grader/metadata cues may influence
   answers without appearing in visible reasoning.
3. **Visible silence is consistent with more than one mechanism.** GPT-5.4 often
   adopted cues without visible disclosure; GLM frequently represented the influence
   privately while omitting it from visible output. The design cannot determine whether
   that omission arose from model behavior, formatting, or provider post-processing.
4. **A single aggregate faithfulness score would hide the main result.** Model, cue,
   task, denominator, and reasoning visibility all matter.

## 4.2 Alternative explanations

- Prompt compliance or role hierarchy rather than a stable reasoning trait.
- Rationalization after selecting the cue target rather than genuine causal reporting.
- Visible-output compression, formatting, or provider post-processing.
- Conditional selection: reveal is measured only among responses that shifted.
- Different thinking settings and provider routes, especially GLM's hidden-reasoning
  stratum.
- Admission-judge transfer errors and ambiguity in silent final-answer overrides.
- Task difficulty, answer format, and scorer coverage as contributors to apparent
  model/task differences.
- Temperature-zero serving nondeterminism, while noting the completed DeepSeek check
  observed no flips.

For each explanation, state the strongest objection, what observable evidence it would
predict, and whether the current data support, weaken, or cannot distinguish it. Treat
prompt-compliance and post-hoc rationalization as serious competing interpretations,
not brief caveats.

## 4.3 Research-process lessons

- The failed parser threshold and accepted deviation show why preregistered gates and
  disagreement review should both be visible.
- The Milestone E pilot demonstrates that an intervention can be easy to implement
  yet fail to preserve the intended construct across domains.
- Per-model judge revalidation prevented known GPT/GLM disclosure errors from becoming
  headline results.
- Targeted reruns and explicit precedence rules are preferable to silently patching
  aggregate summaries.

## 4.4 Limitations

Organize limitations by when they constrain the impact pathway, and for each state the
likely direction or ambiguity of its effect on interpretation:

**Scope and design limitations (before execution)**

1. Three models and provider-specific configurations limit generalization.
2. Only 50 tasks per type; benchmark selection and open-ended formatting may affect
   results.
3. Task-type cells can have small shifted denominators.
4. ETHICS labels represent source consensus, not moral truth.
5. The experiment measures behavioral reporting completeness, not direct internal
   causal faithfulness.
6. The held literature refresh prevents strong novelty or comprehensiveness claims.

**Technical and measurement limitations (during execution)**

7. Model strata are not fully matched because GLM required thinking-enabled hidden
   reasoning.
8. Decided-outcome rates can differ from full-sample risk when missingness is large.
9. Reveal judgments are validated but imperfect; GPT factual silent overrides and
   long grader CoTs were difficult cases.
10. Math surface perturbations and prompt-template behavior were imperfect despite the
    amendment.
11. Provider failures, policy blocks, reruns, and serving nondeterminism may produce
    non-random availability even with transparent handling.

**Deployment and use limitations (after execution)**

12. Models may behave differently under deployment prompts, tools, strategic pressure,
    monitoring awareness, or later provider updates.
13. The study does not measure whether the recommended layered monitoring protocol
    improves safety outcomes, organizational decisions, or cost-effectiveness.

## 4.5 Implications and bounded recommendations

- First state the literature contribution: the results extend prior hint-based evidence
  to constrained open-ended tasks and multiple cue channels while showing that model,
  measurement transfer, and reasoning visibility materially alter interpretation.
- **For evaluation researchers:** validate disclosure measures separately per model
  and reasoning channel; publish numerator, denominator, and missingness.
- **For developers using CoT monitoring:** do not infer absence of cue influence from
  a self-contained visible explanation; combine CoT with behavioral interventions and
  other monitoring signals.
- **For benchmark designers:** vary cue presentation, preserve matched controls, and
  test for provider/configuration changes.
- **For users and institutions:** treat visible CoT as evidence with known failure
  modes, not a complete audit log.
- For each recommendation, identify the actor, action, evidence from this study, expected
  benefit, and principal failure mode. Distinguish immediate low-regret reporting steps
  from untested deployment claims.
- Include a short decision-oriented comparison rather than a fabricated quantitative
  cost-benefit analysis: status quo (CoT treated as self-authenticating), recommended
  layered validation, and the strongest alternative (discarding CoT monitoring). Compare
  validation effort, compute and labor, information retained, false-assurance risk, and
  implementation uncertainty; put any unsupported quantities or detailed assumptions in
  an appendix.
- Keep recommendations proportional to this behavioral study; do not claim measured
  deployment benefits or a completed cost-benefit analysis.

## 4.6 Prioritized future research, not completed work

Use a numbered list derived directly from the limitations. For each direction, give the
limitation addressed, why it has decision value, a rough scale or timeline, and a
priority rationale:

1. Replicate on additional model versions and provider routes to test external validity.
2. Obtain matched private and visible traces under the same settings to isolate reporting
   from visibility-stratum differences.
3. Run a preregistered inferential analysis of model × cue interactions with adequate
   shifted-response denominators.
4. Redesign causal-reliance interventions so they remain coherent across domains before
   attempting the paused corruption experiment.

Explicitly label these as directions for other work, not project results or promised
milestones.

---

# 5. Conclusion (~300 words)

- Keep this to one or two compact paragraphs that a non-specialist stakeholder can read
  as the project's TL;DR.
- Re-state the problem and question: whether visible CoT discloses external factors
  that measurably change answers.
- Re-state the design: four task types, four cue styles plus fresh controls, and three
  models with explicit visibility strata.
- Re-state only the strongest findings:
  - susceptibility differed sharply by cue and model;
  - GPT-5.4 was largely silent after shifts;
  - GLM often disclosed privately but not visibly;
  - DeepSeek's authority-cue disclosure did not generalize across the panel.
- End with the calibrated practical lesson: visible CoT can provide useful evidence,
  but its completeness must be empirically validated for the specific model and
  setting.

---

# References

- Choose and name one citation format before drafting; apply it consistently to inline
  citations and an alphabetized or consistently ordered reference list.
- Use one consistent citation style and hyperlinks.
- Minimum verified core set: Turpin et al. (2023), Lanham et al. (2023), and Chen et
  al. (2025), plus official model and dataset documentation.
- Verify every literature claim before prose drafting; do not infer a comprehensive
  bibliography from the internal project notes.

# Appendices (not in word count)

## Appendix A. Benchmark construction and provenance

- Dataset licenses, source revisions, sampling, review checklist, task schema, and
  example prompts.

## Appendix B. Full experimental prompts and model configurations

- Cue templates, control template, target-generation rules, thinking settings, seeds,
  token limits, and provider routes.

## Appendix C. Evaluation and judge validation

- Answer-equivalence rubric, admission rubric, validation sets, confusion details,
  researcher concessions, and known judge failure modes.

## Appendix D. Complete descriptive results

- Full model × task × cue outcome tables, reveal tables, and reason-specific
  missingness.

## Appendix E. Qualitative transcript examples

- One disclosed and one silent shift per major pattern, including GPT silent override,
  DeepSeek authority disclosure, and GLM private-visible divergence.
- Redact or omit examples only for documented policy/privacy reasons; do not cherry-pick
  without stating the selection rule.

## Appendix F. Deviations, incidents, and research-process record

- Parser threshold deviation, C4 incidents, DeepSeek logic/ethics fixes, GLM provider
  failures, GPT policy blocks, and the Milestone E feasibility pivot.

## Appendix G. Reproducibility and costs

- Artifact manifest, commands, hashes, software versions, cost ledger summary, and
  instructions for rebuilding report tables from persisted records.

## Appendix H. Alternatives, assumptions, and decision comparison

- Brief deep dives on approaches not pursued, the full identification-assumption list,
  and the qualitative status-quo/layered-validation/discard-CoT comparison.

---

# Drafting verification checklist

- [ ] Reconcile canonical DeepSeek full-run slices with superseding logic/ethics
      reruns before producing any overall DeepSeek table.
- [ ] Apply GPT grader correction from 3 to 1 revealed of 44 and preserve the final
      GPT metadata adjudications.
- [ ] Apply GLM grader correction from 43 to 45 revealed of 72.
- [ ] Confirm the final GLM authority numerator after all researcher concessions;
      avoid mixing the README's rounded value with an older judged JSON count.
- [ ] Put numerator/denominator beside every percentage.
- [ ] Separate decided-outcome rates from missingness and full assigned cells.
- [ ] Keep GLM reasoning-trace reveal separate from cross-model visible reveal.
- [ ] Describe parser F1 as a failed gate with an accepted deviation.
- [ ] Describe Milestone E as partial feasibility work and a pivot, never as a
      completed corruption experiment.
- [ ] Avoid Milestone G statistics or claims.
- [ ] Verify citations and novelty language; for each core paper, capture its question,
      data/method, result, limitation, and direct role in this study.
- [ ] State why the hint paradigm was chosen over the strongest alternatives and list
      its key identification assumptions.
- [ ] Connect every headline result to an expectation, alternative explanation,
      confidence statement, and generalization boundary.
- [ ] Connect every recommendation to a named actor, action, evidence, benefit, and
      failure mode; keep unmeasured costs and benefits qualitative.
- [ ] Number and caption all tables/figures; label axes, units, denominators, rounding,
      and missingness consistently.
- [ ] Keep terminology accessible and maintain consistent tense, pronoun, citation style,
      abbreviations, and section numbering.
- [ ] Keep the main text under 8,000 words and move supporting detail to appendices.
